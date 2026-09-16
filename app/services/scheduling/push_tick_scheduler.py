"""背景推播排程器的共用骨架：迴圈、心跳、推播權搶佔、收件人偏好。

用藥提醒（`MedicationScheduler`）與掛號提醒（`AppointmentScheduler`）都是
「每 60 秒一個 tick → 查出到期的階段 → 逐筆原子搶下推播權 → 推播 → 失敗還原」。
兩者的差別只在「有哪些階段、各推什麼」，那一段留在各自的子類別；其餘——包括
收件人的通知開關怎麼讀、搶佔失敗與推播失敗各怎麼處理、心跳怎麼報——只該有一份，
否則日後修一邊（例如通知開關的降級方向）必定漏改另一邊。
"""

import asyncio
import logging
from contextlib import suppress
from datetime import datetime
from enum import Enum
from typing import Any, Awaitable, Callable, NamedTuple, Optional, Union

from app.core import scheduler_heartbeat
from app.core.user_font_size import DEFAULT_USER_FONT_SIZE, normalize_user_font_size
from app.core.user_language import DEFAULT_USER_LANGUAGE, normalize_user_language
from app.services.line_messaging.send_result import (
    SendOutcome,
    SendResult,
    classify_send_exception,
)

logger = logging.getLogger(__name__)


class DispatchOutcome(str, Enum):
    """一個推播階段處理完的結果，決定 `_dispatch` 對搶到的推播權做什麼。

    以前 `send` 只回 bool：True 是「處理完了」（含刻意不送），False 是「還回去
    重試」。LINE 429（額度用完）、400（對象無效）、5xx 在那個合約裡長得一樣
    ——429 重試五次後旗標永遠停在「已送出」，30 分鐘後家屬收到他漏吃的警報，
    而他一則都沒收到。這裡把幾種結果分開；`send` 仍可回 bool（掛號提醒），
    由 `_dispatch` 翻譯。
    """

    DELIVERED = "delivered"  # LINE 回 200：寫送達標記
    SKIPPED = "skipped"      # 刻意不送（收件人關掉通知、沒有人該收）：寫略過標記
    RETRY = "retry"          # 暫時性失敗：還回推播權並計次，下一個 tick 重試
    QUOTA = "quota"          # 429：還回推播權但不計次，本 tick 彙整成一行警告
    REJECTED = "rejected"    # 400／404：立刻放棄，重送同樣的東西沒有意義


SendReturn = Union[bool, SendResult, DispatchOutcome]


def dispatch_outcome_of(value: SendReturn) -> DispatchOutcome:
    """把 `send` 的三種回傳形態統一成 DispatchOutcome。

    bool 沿用舊合約：True 視為處理完（不寫送達標記——掛號提醒沒有那個欄位，
    True 也可能是刻意不送），False 視為重試。`SendResult` 依 LINE 的回應分類；
    401（token 失效）當暫時性失敗處理：token 在 console 重發之後重試就會成功，
    而且有上限，不會無限重試。
    """
    if isinstance(value, DispatchOutcome):
        return value
    if isinstance(value, SendResult):
        if value.outcome is SendOutcome.OK:
            return DispatchOutcome.DELIVERED
        if value.outcome is SendOutcome.QUOTA_EXCEEDED:
            return DispatchOutcome.QUOTA
        if value.outcome is SendOutcome.REJECTED:
            return DispatchOutcome.REJECTED
        return DispatchOutcome.RETRY
    return DispatchOutcome.DELIVERED if value else DispatchOutcome.RETRY


def _error_label(value: SendReturn) -> Optional[str]:
    """寫進 `last_push_error` 的分類字串；只有 SendResult 的失敗才有。"""
    if isinstance(value, SendResult) and not value.ok:
        return value.outcome.value
    return None


class RecipientPrefs(NamedTuple):
    """一位收件人的呈現偏好與通知意願。

    `notify_reminder` 與 `notify_family` 在 UserSettings 與 LIFF 設定頁上存在
    已久，但後端從來沒有讀過它們——使用者把開關關掉，推播照送。UI 對使用者
    說謊，而這件事不報錯、不留 log，只表現為「我明明關了還是一直收到」，
    使用者多半會歸咎於自己按錯或直接封鎖官方帳號。這個型別是把它們真正接上
    的那一步。
    """

    language: str
    font_size: str
    notify_reminder: bool
    notify_family: bool


class PushTickScheduler:
    """子類別覆寫 `HEARTBEAT_NAME`、`LOG_PREFIX` 與 `process_ticks`。"""

    HEARTBEAT_NAME: str = ""
    LOG_PREFIX: str = "[PushTickScheduler]"

    def __init__(
        self,
        replier: Any,
        user_profile_service: Optional[Any] = None,
        check_interval_seconds: int = 60,
    ) -> None:
        self._replier = replier
        self._user_profile_service = user_profile_service
        self._check_interval_seconds = check_interval_seconds
        self._task: Optional[asyncio.Task] = None
        # 本 tick 因 LINE 額度用完而沒送出的則數；子類別在 process_ticks 開頭
        # 歸零、結尾印一行彙整警告（見 `_dispatch` 的 QUOTA 分支）。
        self._quota_blocked = 0

    # ── 收件人 ────────────────────────────────────────────────────────

    async def _resolve_display_prefs(self, user_id: str) -> tuple[str, str]:
        """
        取得收件人的語言與字級設定。
        排程是背景工作，沒有 request context，因此每則推播都需按收件人各自解析。
        """
        prefs = await self._resolve_prefs(user_id)
        return prefs.language, prefs.font_size

    async def _resolve_prefs(self, user_id: str) -> RecipientPrefs:
        """收件人的語言、字級，以及他要不要收這兩類通知。

        三者一次取回：它們來自同一份 profile，分開查會讓每則推播多打一次
        資料庫，而這是逐筆推播的迴圈，成本會乘上待推播數。

        **開關看的是收件人自己的設定**，不是被通報對象的。每個人只決定自己
        收到什麼——當事人不該替家屬決定要不要被通報，家屬也不該替當事人關掉
        提醒。

        載入失敗時回傳預設值並視為兩者皆開啟：缺資料時沿用預設，與本專案其他
        降級方向一致。反過來（失敗即不送）會讓一次資料庫抖動變成整批提醒
        靜默消失，那是提醒類功能最不該發生的事。
        """
        if not self._user_profile_service or not user_id:
            return RecipientPrefs(
                DEFAULT_USER_LANGUAGE, DEFAULT_USER_FONT_SIZE, True, True
            )
        try:
            profile = await self._user_profile_service.get_user_profile(user_id)
        except Exception:
            logger.exception(
                "%s Failed to load display prefs for user %s", self.LOG_PREFIX, user_id
            )
            return RecipientPrefs(
                DEFAULT_USER_LANGUAGE, DEFAULT_USER_FONT_SIZE, True, True
            )

        settings = (profile or {}).get("settings") or {}
        return RecipientPrefs(
            normalize_user_language(settings.get("language")),
            normalize_user_font_size(settings.get("font_size")),
            # 欄位缺席時視為開啟——既有使用者的文件沒有這兩欄，不需要 backfill。
            bool(settings.get("notify_reminder", True)),
            bool(settings.get("notify_family", True)),
        )

    async def _resolve_patient_name(self, user_id: str) -> str:
        """取得當事人的顯示名稱；查不到時回退為泛稱。"""
        if not self._user_profile_service:
            return "成員"
        try:
            profile = await self._user_profile_service.get_user_profile(user_id)
            if profile and isinstance(profile, dict) and profile.get("name"):
                return profile["name"]
        except Exception:
            pass
        return "成員"

    async def _push(self, user_id: str, card: Any) -> bool:
        """推一則 Flex 給一位收件人。例外吞在這裡並回 False：同一則要推給多位
        家屬時，一位封鎖了官方帳號（推播永遠失敗）不該讓後面的人都收不到。"""
        return (await self._push_result(user_id, card)).ok

    async def _push_result(self, user_id: str, card: Any) -> SendResult:
        """同 `_push`，但回傳分類過的結果，讓呼叫端分得出 429 與 5xx。

        `push_flex_result` 自己已經把 LINE SDK 的例外翻成 SendResult；這裡再接
        一層是為了替身或網路層拋出的其他例外——同樣不能讓一位收件人的失敗
        打斷整批，翻成暫時性失敗交給重試。
        """
        try:
            push_result = getattr(self._replier, "push_flex_result", None)
            if push_result is None:
                # 只實作了舊介面 `push_flex` 的替身（例如掛號提醒測試的
                # RecordingReplier）：bool 翻成 SendResult，False 當暫時性失敗。
                ok = await self._replier.push_flex(user_id, card)
                return SendResult.success() if ok else SendResult(SendOutcome.TRANSIENT)
            return await push_result(user_id, card)
        except Exception as exc:  # noqa: BLE001
            logger.exception("%s push to %s failed", self.LOG_PREFIX, user_id)
            return classify_send_exception(exc)

    # ── 推播權搶佔 ────────────────────────────────────────────────────

    async def _dispatch(
        self,
        *,
        stage: str,
        log_id: str,
        claim: Callable[[str], Awaitable[bool]],
        release: Callable[..., Awaitable[bool]],
        send: Callable[[], Awaitable[SendReturn]],
        mark_sent: Optional[Callable[[str], Awaitable[Any]]] = None,
        mark_skipped: Optional[Callable[[str], Awaitable[Any]]] = None,
        give_up: Optional[Callable[[str, str], Awaitable[Any]]] = None,
    ) -> None:
        """
        推播權搶佔 → 推播 → 依結果標記或還原。

        所有階段共用同一套流程，差別只在旗標與訊息內容。搶佔的理由見
        `MedicationLogRepository` 的「推播權搶佔」段落：查詢與標記之間沒有原子性，
        多實例並存時會重複推播。

        `send` 的合約見 `dispatch_outcome_of`：bool（舊合約）、SendResult 或
        DispatchOutcome 都可以。三個可選的回呼分別對應送達、刻意略過、立刻放棄
        ——沒有提供（掛號提醒）就只做「失敗還回去」這一件事，行為與過去相同。
        `release` 在額度用完時會以 `count_attempt=False, error=...` 呼叫，其餘
        情況只帶 log_id。
        """
        try:
            claimed = await claim(log_id)
        except Exception:
            logger.exception(
                "%s Failed to claim %s for log %s", self.LOG_PREFIX, stage, log_id
            )
            return

        if not claimed:
            # 旗標已被其他實例搶走，或先前的 tick 已送出。
            return

        try:
            result = await send()
        except Exception:
            logger.exception(
                "%s Failed to process %s for log %s", self.LOG_PREFIX, stage, log_id
            )
            result = False
        except BaseException:
            # `CancelledError`／`KeyboardInterrupt`／`SystemExit` 不是 Exception：
            # pod 收到 SIGTERM 時 uvicorn 取消所有 task，這一則正好卡在推播中的
            # 話，上面的 except 接不到，旗標停在「已送出」而沒有人收到。還回去
            # 再把取消往外傳——取消本身必須照常生效，不能吞掉。租約
            # （`PUSH_CLAIM_LEASE`）是這裡接不到的情況（OOM kill、SIGKILL）的
            # 後盾。
            with suppress(Exception):
                await release(log_id)
            raise

        outcome = dispatch_outcome_of(result)
        error = _error_label(result)
        try:
            if outcome is DispatchOutcome.DELIVERED:
                if mark_sent is not None:
                    await mark_sent(log_id)
            elif outcome is DispatchOutcome.SKIPPED:
                if mark_skipped is not None:
                    await mark_skipped(log_id)
            elif outcome is DispatchOutcome.QUOTA:
                # 額度用完不是這一則的錯：不計次還回去，額度一恢復就送。逐筆印
                # 警告會在額度用完的那個月每分鐘刷幾十行，改由 process_ticks
                # 每個 tick 彙整成一行（見 `_quota_blocked`）。
                self._quota_blocked += 1
                await release(log_id, count_attempt=False, error=error)
            elif outcome is DispatchOutcome.REJECTED:
                if give_up is not None:
                    await give_up(log_id, error or "rejected")
                else:
                    await release(log_id)
            else:
                # 推播沒成功就把推播權還回去，下一個 tick 會重新搶佔並重試。
                if error is not None:
                    await release(log_id, error=error)
                else:
                    await release(log_id)
        except Exception:
            logger.exception(
                "%s Failed to record %s outcome %s for log %s",
                self.LOG_PREFIX,
                stage,
                outcome.value,
                log_id,
            )

    # ── 迴圈與心跳 ────────────────────────────────────────────────────

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        # 登記心跳：排程器與 API 拆成不同 pod 之後，這是 K8s 唯一能判斷
        # 「排程器還在跑」的依據——uvicorn 活著不代表這個 task 還在。
        scheduler_heartbeat.register(
            self.HEARTBEAT_NAME, expected_interval_seconds=self._check_interval_seconds
        )
        self._task = asyncio.create_task(self._run_loop())
        logger.info("%s Background scheduler started", self.LOG_PREFIX)

    async def stop(self) -> None:
        if self._task is None or self._task.done():
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        logger.info("%s Background scheduler stopped", self.LOG_PREFIX)

    async def _run_loop(self) -> None:
        while True:
            try:
                await self.process_ticks()
            except Exception:
                logger.exception("%s Error during tick execution", self.LOG_PREFIX)
            # 心跳放在 except 之外：單次 tick 失敗（例如資料庫瞬斷）不代表排程器
            # 停擺，迴圈本身仍在轉，重啟這個 pod 只會讓情況更糟——重啟期間錯過
            # 的時段不會補推。心跳要回答的是「這個迴圈還在不在」，不是「這一輪
            # 有沒有成功」。
            scheduler_heartbeat.beat(self.HEARTBEAT_NAME)
            await asyncio.sleep(self._check_interval_seconds)

    async def process_ticks(self, now: Optional[datetime] = None) -> None:
        raise NotImplementedError
