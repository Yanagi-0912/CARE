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
    MedicalNewsDeliveryRepository,
)
from app.repositories.medication_repository import MedicationRepository
from app.repositories.user_profile_repository import UserProfileRepository
from app.services.line_messaging.flex.medical_news_flex import (
    build_tier1_news_flex,
    build_tier2_news_flex,
)
from app.services.line_messaging.reply.reply import LineReplier
from app.services.medical_news.kb_digest_service import KbArticle
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
        medication_repository: Any = MedicationRepository,
        user_repository: Any = UserProfileRepository,
    ) -> None:
        self._replier = replier
        self._user_profile_service = user_profile_service
        self._kb_digest = kb_digest
        self._run_time = run_time
        self._max_age_days = max_age_days
        # 四個 repository 全部走注入，預設就是真正的那四個（方法皆為
        # staticmethod，傳 class 本身即可）。慣例與 MedicationScheduler 相同：
        # 開這個縫是為了讓測試餵替身，而不必用 monkeypatch 換掉本模組 import
        # 進來的名稱——openspec 的測試規則明文禁止後者。
        self._drug_news_repository = drug_news_repository
        self._delivery_repository = delivery_repository
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
        hour, minute = (int(part) for part in self._run_time.split(":"))
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    # ── 選材與推播 ──────────────────────────────────────────────────

    async def run_once(self, today: str) -> None:
        """為每位使用者挑一則並推出。

        收件人是**全體**使用者，不是「有用藥的那批」——Tier 2 保底存在的理由
        正是讓沒有用藥資料的人也收得到東西。
        """
        user_ids = await self._user_repository.list_all_line_ids()
        tier2_pool = await self._kb_digest.recent_articles(today, limit=10)

        for user_id in user_ids:
            try:
                await self._push_for_user(user_id, today, tier2_pool)
            except Exception:
                # 單一使用者的失敗不得影響其他人。
                logger.exception(
                    "[MedicalNewsPushScheduler] 使用者處理失敗：%s", user_id
                )

    async def _push_for_user(
        self, user_id: str, today: str, tier2_pool: list[KbArticle]
    ) -> None:
        since = datetime.now(timezone.utc) - timedelta(days=self._max_age_days)
        pushed_refs = await self._delivery_repository.list_pushed_refs(user_id, since)

        news = await self._pick_tier1(user_id, today, pushed_refs)
        if news is not None:
            await self._send(
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
                payload={
                    "title": news.title,
                    "summary": news.summary,
                    "source_name": news.source_name,
                    "url": news.url,
                },
            )
            # 每位使用者每日至多一則（design.md 決策 8）。連發多張「你的藥有
            # 問題」對高齡使用者是恐慌而非資訊。
            return

        article = self._pick_tier2(tier2_pool, pushed_refs)
        if article is None:
            # 兩層都沒有內容時安靜地不推。推一張空卡比不推糟——那正是
            # medication-reminder-lifecycle 那個 bug 的教訓。
            return

        await self._send(
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
            payload={
                "title": article.title,
                "summary": article.excerpt,
                "source_name": article.source_name,
                "url": article.url,
            },
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
        pool: list[KbArticle], pushed_refs: set[str]
    ) -> Optional[KbArticle]:
        for article in pool:
            if make_news_ref("kb_article", article.url) not in pushed_refs:
                return article
        return None

    async def _send(
        self, user_id: str, news_ref: str, *, tier: int, build, payload: dict
    ) -> None:
        """先搶後推。

        順序是承重的：反過來的話兩個排程實例會各推一次才發現撞號，而使用者已經
        收到兩張一樣的卡了。
        """
        # 卡片內容跟著 claim 一起寫入：分享路徑只拿得到 news_ref（雜湊），
        # 反解不回來源，因此內容必須在這裡就落地。
        if not await self._delivery_repository.claim(
            user_id, news_ref, tier, **payload
        ):
            return

        language, font_size = await self._resolve_display_prefs(user_id)
        try:
            flex = build(news_ref, language, font_size)
        except ValueError:
            # 卡片縮不進 LINE 的大小上限。退回不推而非退回純文字：這則消息
            # 不是使用者在等的回覆，沒有非送不可的理由。
            logger.warning(
                "[MedicalNewsPushScheduler] 卡片超過大小上限，略過：%s", news_ref
            )
            return

        # 推播失敗不重試、不回滾 claim。延遲後的消息卡已失去時效意義，補推只是
        # 騷擾——與用藥提醒的 misfire grace 同一個判斷。
        await self._replier.push_flex(user_id, flex)

    async def _resolve_display_prefs(self, user_id: str) -> tuple[str, str]:
        """取得收件人的語言與字級。做法逐字沿用 MedicationScheduler：背景工作
        沒有 request context，每則推播都要按收件人各自解析。"""
        if not self._user_profile_service or not user_id:
            return DEFAULT_USER_LANGUAGE, DEFAULT_USER_FONT_SIZE
        try:
            profile = await self._user_profile_service.get_user_profile(user_id)
        except Exception:
            logger.exception(
                "[MedicalNewsPushScheduler] 無法載入使用者設定：%s", user_id
            )
            return DEFAULT_USER_LANGUAGE, DEFAULT_USER_FONT_SIZE

        settings = (profile or {}).get("settings") or {}
        return (
            normalize_user_language(settings.get("language")),
            normalize_user_font_size(settings.get("font_size")),
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
