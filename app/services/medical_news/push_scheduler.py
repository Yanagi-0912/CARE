"""每日為每位使用者挑一則消息卡推出。

與索引排程分開的兩個理由（design.md 決策 2）：

1. 成本模型不同——索引是 O(不重複藥數)，推播是 O(使用者數)。
2. 失敗模式不同——政府站台逾時會讓索引整輪失敗，但推播照常拿昨天的索引跑。
   合在一起時前者會拖垮後者。

因此兩支的心跳也分開登記。合併登記會讓其中一支停擺被另一支的心跳掩蓋：索引
停了、推播照跑，外觀完全健康，只是內容永遠停在停擺那一天。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.core import scheduler_heartbeat
from app.core.user_font_size import DEFAULT_USER_FONT_SIZE, normalize_user_font_size
from app.core.user_language import DEFAULT_USER_LANGUAGE, normalize_user_language
from app.models.medical_news import make_news_ref
from app.models.medication import TAIPEI_TZ
from app.repositories.medical_news_repository import (
    DrugNewsRepository,
    MedicalNewsDayClaimRepository,
    MedicalNewsDeliveryRepository,
)
from app.repositories.medication_repository import MedicationRepository
from app.repositories.user_profile_repository import UserProfileRepository
from app.services.line_messaging.flex.medical_news_flex import (
    build_tier1_news_flex,
    build_tier2_news_flex,
)
from app.services.line_messaging.reply.reply import LineReplier
from app.services.line_messaging.send_result import SendOutcome, SendResult
from app.services.medical_news.kb_digest_service import KbArticle
from app.services.medical_news.run_time import parse_run_time
from app.services.users.user_profile_service import UserProfileService

logger = logging.getLogger(__name__)

# 消息類型的優先序。回收與供應短缺是「現在就影響到你手上那盒藥」，衛教不是。
# 同一天有多則命中時，先推最靠近行動的那一則。
_CONCERN_PRIORITY: dict[str, int] = {
    "recall": 0,
    "safety": 1,
    "supply": 2,
    "education": 3,
}


# 單一使用者處理完的結果。run_once 只關心兩件事：額度是不是用完了（那要停下
# 整輪並記一行摘要），以及其他人是不是照常。
PUSH_SENT = "sent"
PUSH_SKIPPED = "skipped"  # 關閉通知、已封鎖、或今天已有別的實例處理
PUSH_NO_CONTENT = "no_content"
PUSH_QUOTA_EXCEEDED = "quota_exceeded"
PUSH_FAILED = "failed"  # 400／401／5xx；不重試、不回滾


def _user_tag(user_id: str) -> str:
    """log 用的使用者代號：LINE user id 是個資，不進 log；雜湊前 8 碼足以在同一
    份 log 裡對上同一個人，卻反解不回 id。"""
    return hashlib.blake2b((user_id or "").encode("utf-8"), digest_size=4).hexdigest()


def _is_invalid_target(result: SendResult) -> bool:
    """LINE 的 400 有沒有**明確**說對象無效。

    只認 `to` 欄位無效這一種（LINE 的回應是 "The property, 'to', in the request
    body is invalid"）；其他 400（Flex 內容不合法等）是我們的內容問題，不是使用者
    的問題，不得因此把人標成已封鎖。
    """
    if result.outcome is not SendOutcome.REJECTED:
        return False
    return "'to'" in result.detail.lower()


def _pool_offset(user_id: str, size: int) -> int:
    """這位使用者從池子的第幾篇開始看。穩定、與行程無關、分佈均勻。"""
    if size <= 0:
        return 0
    digest = hashlib.blake2b(
        (user_id or "").encode("utf-8"), digest_size=8
    ).digest()
    return int.from_bytes(digest, "big") % size


class MedicalNewsPushScheduler:
    HEARTBEAT_NAME = "medical_news_push"
    HEARTBEAT_INTERVAL_SECONDS = 24 * 60 * 60

    def __init__(
        self,
        *,
        replier: LineReplier,
        user_profile_service: Optional[UserProfileService],
        kb_digest: Any,
        run_time: str,
        max_age_days: int,
        drug_news_repository: Any = DrugNewsRepository,
        delivery_repository: Any = MedicalNewsDeliveryRepository,
        day_claim_repository: Any = MedicalNewsDayClaimRepository,
        medication_repository: Any = MedicationRepository,
        user_repository: Any = UserProfileRepository,
    ) -> None:
        self._replier = replier
        self._user_profile_service = user_profile_service
        self._kb_digest = kb_digest
        self._run_time = run_time
        # 在建構時就驗：設錯的話 task 會在第一次醒來前靜默死掉，外觀健康、永不推播。
        self._run_hour, self._run_minute = parse_run_time(
            run_time, setting_name="MEDICAL_NEWS_PUSH_TIME"
        )
        self._max_age_days = max_age_days
        # 五個 repository 全部走注入，預設就是真正的那五個（方法皆為
        # staticmethod，傳 class 本身即可）。慣例與 MedicationScheduler 相同：
        # 開這個縫是為了讓測試餵替身，而不必用 monkeypatch 換掉本模組 import
        # 進來的名稱——openspec 的測試規則明文禁止後者。
        self._drug_news_repository = drug_news_repository
        self._delivery_repository = delivery_repository
        self._day_claim_repository = day_claim_repository
        self._medication_repository = medication_repository
        self._user_repository = user_repository
        self._task: Optional[asyncio.Task] = None

    # ── 生命週期 ────────────────────────────────────────────────────

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        # 容忍倍數 1.5，理由同 ConsultationDailySummaryScheduler：每天只醒一次，
        # 預設的 3 倍代表要停擺三天才會被發現。
        scheduler_heartbeat.register(
            self.HEARTBEAT_NAME,
            expected_interval_seconds=self.HEARTBEAT_INTERVAL_SECONDS,
            tolerance_factor=1.5,
        )
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            "[MedicalNewsPushScheduler] started, run_time=%s", self._run_time
        )

    async def stop(self) -> None:
        if self._task is None or self._task.done():
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        logger.info("[MedicalNewsPushScheduler] stopped")

    async def _run_loop(self) -> None:
        while True:
            now = datetime.now(TAIPEI_TZ)
            next_run = self._next_run_at(now)
            await asyncio.sleep(max(0.0, (next_run - now).total_seconds()))
            # 心跳在執行之前回報：要證明的是「迴圈醒過來了」，不是「這次推播
            # 有沒有成功」。
            scheduler_heartbeat.beat(self.HEARTBEAT_NAME)
            try:
                await self.run_once(datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d"))
            except Exception:
                logger.exception("[MedicalNewsPushScheduler] tick 失敗")

    def _next_run_at(self, now: datetime) -> datetime:
        candidate = now.replace(
            hour=self._run_hour, minute=self._run_minute, second=0, microsecond=0
        )
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    # ── 選材與推播 ──────────────────────────────────────────────────

    async def run_once(self, today: str) -> None:
        """為每位使用者挑一則並推出。`today` 是台北日期（YYYY-MM-DD）。

        收件人是**全體**使用者，不是「有用藥的那批」——Tier 2 保底存在的理由
        正是讓沒有用藥資料的人也收得到東西。

        額度用完（LINE 回 429）就停下整輪：之後每一位都會同樣失敗，繼續跑只是
        對 LINE 多打 N 次、對資料庫多搶多放 N 次。沒輪到的使用者今天沒被搶佔，
        額度恢復後同一天重跑、或明天照常，都輪得到他們。整輪只記**一行**摘要，
        不是每人一行——維運要看的是「今天少送了多少人」，不是 N 行一樣的錯誤。
        """
        user_ids = await self._user_repository.list_all_line_ids()
        tier2_pool = await self._kb_digest.recent_articles(today, limit=10)

        unsent_for_quota = 0
        for index, user_id in enumerate(user_ids):
            try:
                outcome = await self._push_for_user(user_id, today, tier2_pool)
            except Exception:
                # 單一使用者的失敗不得影響其他人。
                logger.exception(
                    "[MedicalNewsPushScheduler] 使用者處理失敗：user=%s",
                    _user_tag(user_id),
                )
                continue
            if outcome == PUSH_QUOTA_EXCEEDED:
                unsent_for_quota = len(user_ids) - index
                break

        if unsent_for_quota:
            logger.warning(
                "[MedicalNewsPushScheduler] 因額度用完，%d 位未送（%s）",
                unsent_for_quota,
                today,
            )

    async def _push_for_user(
        self, user_id: str, today: str, tier2_pool: list[KbArticle]
    ) -> str:
        language, font_size, opted_in = await self._resolve_prefs(user_id)
        if not opted_in:
            # 在任何查詢與 claim 之前就退出。**不得先 claim 再檢查**：claim 會把
            # 該則記成「已推給這位使用者」，他日後重新打開開關時，那幾則會被
            # 當成推過而永遠收不到。
            return PUSH_SKIPPED

        # 先搶「今天這位使用者歸我」，再選材。兩個實例各自選材可能挑到**不同**
        # 的消息，(user_id, news_ref) 的唯一索引擋不住那種情況；這一道是
        # 「每日至多一則」在多實例下唯一的保證。
        if not await self._day_claim_repository.claim(user_id, today):
            return PUSH_SKIPPED

        since = datetime.now(timezone.utc) - timedelta(days=self._max_age_days)
        pushed_refs = await self._delivery_repository.list_pushed_refs(user_id, since)

        news = await self._pick_tier1(user_id, today, pushed_refs)
        if news is not None:
            return await self._send(
                user_id,
                make_news_ref("drug_news", news.url),
                tier=1,
                build=lambda ref, language, font_size: build_tier1_news_flex(
                    news_ref=ref,
                    drug_name=news.drug_key,
                    title=news.title,
                    summary=news.summary,
                    source_name=news.source_name,
                    url=news.url,
                    language=language,
                    font_size=font_size,
                ),
                language=language,
                font_size=font_size,
                payload={
                    "title": news.title,
                    "summary": news.summary,
                    "source_name": news.source_name,
                    "url": news.url,
                },
                today=today,
            )
            # 每位使用者每日至多一則（design.md 決策 8）。連發多張「你的藥有
            # 問題」對高齡使用者是恐慌而非資訊。

        article = self._pick_tier2(tier2_pool, pushed_refs, user_id)
        if article is None:
            # 兩層都沒有內容時安靜地不推。推一張空卡比不推糟——那正是
            # medication-reminder-lifecycle 那個 bug 的教訓。
            # 放掉今天的處理權：稍後索引補上、同一天再跑一次時仍應輪得到他。
            await self._day_claim_repository.release(user_id, today)
            return PUSH_NO_CONTENT

        return await self._send(
            user_id,
            make_news_ref("kb_article", article.url),
            tier=2,
            build=lambda ref, language, font_size: build_tier2_news_flex(
                news_ref=ref,
                title=article.title,
                summary=article.excerpt,
                source_name=article.source_name,
                url=article.url,
                language=language,
                font_size=font_size,
            ),
            language=language,
            font_size=font_size,
            payload={
                "title": article.title,
                "summary": article.excerpt,
                "source_name": article.source_name,
                "url": article.url,
            },
            today=today,
        )

    async def _pick_tier1(self, user_id: str, today: str, pushed_refs: set[str]):
        medications = await self._medication_repository.list_active_by_user(
            user_id, today
        )
        drug_keys: list[str] = []
        for medication in medications:
            # 藥名與學名兩邊都要：藥袋上印的常是品牌短名，官方公告常以成分名
            # 發布（「含 ACETAMINOPHEN 之藥品」）。
            for value in (medication.name, getattr(medication, "generic_name", None)):
                if value and value not in drug_keys:
                    drug_keys.append(value)
        if not drug_keys:
            return None

        candidates = await self._drug_news_repository.find_by_drug_keys(
            drug_keys, since=""
        )
        fresh = [
            news
            for news in candidates
            if make_news_ref("drug_news", news.url) not in pushed_refs
        ]
        if not fresh:
            return None

        fresh.sort(
            key=lambda n: (
                _CONCERN_PRIORITY.get(n.concern_kind, 99),
                # published_at 遞減：同一優先序內取最新的那則。
                _negated_date_key(n.published_at),
            )
        )
        return fresh[0]

    @staticmethod
    def _pick_tier2(
        pool: list[KbArticle], pushed_refs: set[str], user_id: str
    ) -> Optional[KbArticle]:
        """從全體共用的池子挑一篇，起點依 user_id 錯開。

        為什麼要錯開：池子是全體共用的，而唯一的個人化是「這位使用者收過沒」。
        對**沒有推播歷史的人**——新加入的使用者，以及功能剛上線那幾天的所有
        人——那個條件對誰都成立，於是所有人拿到同一篇 `pool[0]`。同一天同一群
        長輩收到一模一樣的卡，Tier 2「今日醫療小知識」的個人化外觀就破了。

        起點用 `blake2b` 而不是內建 `hash()`：`hash()` 對 str 受
        `PYTHONHASHSEED` 影響，每次行程重啟結果都不同，同一位使用者的偏移會在
        每次部署後跳掉，等於沒有穩定的個人化。這與 `medication_repository` 選
        用可重現雜湊的理由相同。

        代價：偏移之後不再保證「最新的先推」。可接受——池子裡每一篇都已通過
        `max_age_days`，彼此的新舊差異對衛教內容沒有意義，而 Tier 1 的警訊
        （新舊差異有意義的那些）根本不走這條路。
        """
        if not pool:
            return None
        # 依 priority 分群，先把前面的群挑完才輪到後面的群；群內才做 user_id
        # 錯開。若對整個池子做錯開，起點會隨機落在任何一群，「官方優先、媒體
        # 補位」就退化成「官方與媒體隨機」——而媒體一天十幾篇、官方兩篇，隨機
        # 起點幾乎總是落在媒體那一群。
        for priority in sorted({article.priority for article in pool}):
            group = [article for article in pool if article.priority == priority]
            offset = _pool_offset(user_id, len(group))
            for index in range(len(group)):
                article = group[(offset + index) % len(group)]
                if make_news_ref("kb_article", article.url) not in pushed_refs:
                    return article
        return None

    async def _send(
        self,
        user_id: str,
        news_ref: str,
        *,
        tier: int,
        build,
        payload: dict,
        language: str,
        font_size: str,
        today: str,
    ) -> str:
        """先搶後推。

        順序是承重的：反過來的話兩個排程實例會各推一次才發現撞號，而使用者已經
        收到兩張一樣的卡了。
        """
        # 卡片內容跟著 claim 一起寫入：分享路徑只拿得到 news_ref（雜湊），
        # 反解不回來源，因此內容必須在這裡就落地。
        if not await self._delivery_repository.claim(
            user_id, news_ref, tier, **payload
        ):
            return PUSH_SKIPPED

        try:
            flex = build(news_ref, language, font_size)
        except ValueError:
            # 卡片縮不進 LINE 的大小上限。退回不推而非退回純文字：這則消息
            # 不是使用者在等的回覆，沒有非送不可的理由。
            logger.warning(
                "[MedicalNewsPushScheduler] 卡片超過大小上限，略過：%s", news_ref
            )
            return PUSH_NO_CONTENT

        result = await self._replier.push_flex_result(user_id, flex)
        if result.ok:
            return PUSH_SENT

        if result.outcome is SendOutcome.QUOTA_EXCEEDED:
            # 確定沒送出：兩個 claim 都放掉。留著的話這則會被記成「已推給他」，
            # 額度恢復後他永遠不會再收到它；今天的處理權留著則會讓同一天重跑時
            # 跳過他。
            await self._delivery_repository.release(user_id, news_ref)
            await self._day_claim_repository.release(user_id, today)
            return PUSH_QUOTA_EXCEEDED

        if _is_invalid_target(result):
            # LINE 明確說這個對象無效（封鎖、刪除帳號）。標成未追蹤，明天起
            # 不再為他選材與推播；欄位缺席仍視為追蹤中，所以只寫 False。
            await self._mark_not_following(user_id)
            return PUSH_FAILED

        # 其餘（內容不合法、token 失效、LINE 5xx、逾時）：不重試、不回滾 claim。
        # 延遲後的消息卡已失去時效意義，補推只是騷擾——與用藥提醒的 misfire
        # grace 同一個判斷；而暫時性失敗送沒送到不確定，回滾反而可能推成兩張。
        logger.warning(
            "[MedicalNewsPushScheduler] 推播失敗 outcome=%s status=%s user=%s",
            result.outcome.value,
            result.status,
            _user_tag(user_id),
        )
        return PUSH_FAILED

    async def _mark_not_following(self, user_id: str) -> None:
        """標記失敗只記 log：這只是省掉明天的一次無效推播，不值得讓這一輪中斷。"""
        try:
            await self._user_repository.set_following(user_id, False)
        except Exception:
            logger.exception(
                "[MedicalNewsPushScheduler] 標記未追蹤失敗：user=%s", _user_tag(user_id)
            )
        else:
            logger.info(
                "[MedicalNewsPushScheduler] LINE 回報對象無效，已標記未追蹤：user=%s",
                _user_tag(user_id),
            )

    async def _resolve_prefs(self, user_id: str) -> tuple[str, str, bool]:
        """語言、字級、以及這位使用者要不要收每日消息卡。

        三者一次取回，因為它們來自同一份 profile——分成兩個方法會讓每則推播
        多打一次資料庫，而背景工作是逐使用者迴圈，那個成本會乘上使用者數。

        載入失敗時回傳預設值並**視為開啟**：這個方向與其他降級一致（缺資料時
        沿用預設），而預設本身就是開啟。若改成失敗即不推，一次資料庫抖動就會
        讓當天全體使用者靜默地收不到東西。
        """
        if not self._user_profile_service or not user_id:
            return DEFAULT_USER_LANGUAGE, DEFAULT_USER_FONT_SIZE, True
        try:
            profile = await self._user_profile_service.get_user_profile(user_id)
        except Exception:
            logger.exception(
                "[MedicalNewsPushScheduler] 無法載入使用者設定：user=%s",
                _user_tag(user_id),
            )
            return DEFAULT_USER_LANGUAGE, DEFAULT_USER_FONT_SIZE, True

        profile = profile or {}
        settings = profile.get("settings") or {}
        # 已封鎖官方帳號的人不推：LINE 對封鎖者的 push 不會送達卻照算額度。
        # 欄位缺席視為仍在追蹤——既有使用者的文件沒有這一欄。
        following = profile.get("is_following", True) is not False
        return (
            normalize_user_language(settings.get("language")),
            normalize_user_font_size(settings.get("font_size")),
            # 欄位缺席時視為開啟——既有使用者的文件沒有這一欄，不需要 backfill。
            following and bool(settings.get("notify_medical_news", True)),
        )


def _negated_date_key(published_at: Optional[str]) -> str:
    """讓較新的日期排在前面的排序鍵。

    直接用字串反轉不可行（民國年與西元年混雜），改以「補數」的方式：把每個
    數字字元換成 9 減它，字典序就與原本相反。缺日期者排最後。
    """
    if not published_at:
        return "0"
    return "".join(
        str(9 - int(ch)) if ch.isdigit() else ch for ch in published_at
    )


def start_medical_news_push_scheduler(
    *,
    enabled: bool = True,
    replier: LineReplier,
    user_profile_service: Optional[UserProfileService],
    kb_digest: Any,
    run_time: str,
    max_age_days: int,
) -> Optional[MedicalNewsPushScheduler]:
    if not enabled:
        logger.info("[MedicalNewsPushScheduler] disabled")
        return None

    scheduler = MedicalNewsPushScheduler(
        replier=replier,
        user_profile_service=user_profile_service,
        kb_digest=kb_digest,
        run_time=run_time,
        max_age_days=max_age_days,
    )
    scheduler.start()
    return scheduler
