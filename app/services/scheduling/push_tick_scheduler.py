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
from typing import Any, Awaitable, Callable, NamedTuple, Optional

from app.core import scheduler_heartbeat
from app.core.user_font_size import DEFAULT_USER_FONT_SIZE, normalize_user_font_size
from app.core.user_language import DEFAULT_USER_LANGUAGE, normalize_user_language

logger = logging.getLogger(__name__)


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
        try:
            return bool(await self._replier.push_flex(user_id, card))
        except Exception:  # noqa: BLE001
            logger.exception("%s push to %s failed", self.LOG_PREFIX, user_id)
            return False

    # ── 推播權搶佔 ────────────────────────────────────────────────────

    async def _dispatch(
        self,
        *,
        stage: str,
        log_id: str,
        claim: Callable[[str], Awaitable[bool]],
        release: Callable[[str], Awaitable[bool]],
        send: Callable[[], Awaitable[bool]],
    ) -> None:
        """
        推播權搶佔 → 推播 → 失敗還原。

        所有階段共用同一套流程，差別只在旗標與訊息內容。搶佔的理由見
        `MedicationLogRepository` 的「推播權搶佔」段落：查詢與標記之間沒有原子性，
        多實例並存時會重複推播。

        `send` 的合約：回 True 代表這個階段已處理完（包含「收件人關掉了通知」這種
        刻意不送），回 False 代表推播失敗、要把推播權還回去讓下一個 tick 重試。
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
            sent = await send()
        except Exception:
            logger.exception(
                "%s Failed to process %s for log %s", self.LOG_PREFIX, stage, log_id
            )
            sent = False

        if not sent:
            # 推播沒成功就把推播權還回去，下一個 tick 會重新搶佔並重試。
            with suppress(Exception):
                await release(log_id)

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
