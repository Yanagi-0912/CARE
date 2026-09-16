"""走失求救：通知家人、接收長輩的即時位置、通知關鍵事件、結束。

整個流程：
1. 長輩在 LINE 說「我走丟了」（lost_intent）→ `report` 建立一次求救，
   handler 回長輩一張「讓家人看到我在哪裡」的卡，同時在背景
   `notify_family_of_report` 推通報給家人。**不等長輩打開定位頁才通知**：
   他可能卡在打開頁面、按允許定位那一步。
2. 長輩打開 LIFF 定位頁，每 20 秒上傳一次位置 → `record_location`。第一次收到
   位置時推「已經收到位置」給家人。
3. 家人打開 LIFF 地圖頁輪詢 `view`，地圖在頁面裡自己更新。**不在 LINE 裡每次
   位置都推一則**：一小時 120 則會洗掉家人的聊天室，也會吃光推播額度
   （目前方案每月 3,000 則）。
4. scheduler 每分鐘 `process_due`：位置超過 STALE_AFTER 沒更新就通知一次家人、
   並推一張卡請長輩重新打開；超過 AUTO_END_AFTER 自動結束。
5. 家人按「已找到」（`end_by_family`）或長輩按「我已經安全了」
   （`end_by_elder`）結束，通知其他人。

誰收得到、誰看得到地圖、誰按得了「已找到」都是同一份名單：
`notification_recipients(長輩, "elder_lost")`。收到通報卻打不開地圖，或打得開
地圖卻沒收到通報，都說不通。

每一則推播都先原子取得推播權（lost_session_repository.claim_notice／end_active），
API pod 與 scheduler pod、或滾動更新時並存的兩個 scheduler 才不會各推一次。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal, Optional
from urllib.parse import quote

from app.core.user_font_size import DEFAULT_USER_FONT_SIZE, normalize_user_font_size
from app.core.user_language import DEFAULT_USER_LANGUAGE, normalize_user_language
from app.i18n.messages import t
from app.repositories.lost_session_repository import (
    ACTIVE,
    LostSessionRepository,
    as_utc,
)
from resources.flex_messages.lost_location_flex_message import (
    LOST_ACCENT,
    build_elder_share_flex,
    build_family_alert_flex,
    build_family_notice_flex,
    family_alert_alt_text,
)
from resources.flex_messages.size_guard import fits

logger = logging.getLogger(__name__)

LOGGER_HEADER_TEXT = "[Services:LostLocation]"

NOTIFICATION_KIND = "elder_lost"

# 位置多久沒更新算「停止更新」。長輩頁每 20 秒上傳一次（CARE-LIFF
# src/pages/LostShare），3 分鐘等於連續漏掉 9 次，偶爾一兩次網路不穩不會誤報；
# 再長的話，畫面被關掉之後家人要等太久才知道地圖上的位置已經是舊的。
STALE_AFTER = timedelta(minutes=3)

# 自動結束。沒有人按「已找到」時定位不能無限期開著：長輩手機持續用 GPS 很耗電，
# 家人忘了按也會一直收到「停止更新」。2 小時還沒找到人已經是該報警的情況，
# 結束通知會叫家人撥 110；長輩要繼續分享，再說一次「我走丟了」就好。
AUTO_END_AFTER = timedelta(hours=2)

# 結束之後資料還留多久，見 lost_session_repository 的模組說明。
PURGE_AFTER = timedelta(hours=24)

EndStatus = Literal["found", "safe", "expired"]


@dataclass(frozen=True)
class LostReport:
    """`report` 的結果，handler 依此決定回長輩哪一張卡。

    - started：新的一次求救，要在背景通知家人。
    - already_active：已經在求救中，家人已經收過通報，不再推一次。
    - no_family：沒有任何家人收得到，沒有建立求救。
    """

    outcome: Literal["started", "already_active", "no_family"]
    session: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class _Prefs:
    language: str
    font_size: str
    notify_family: bool


def _now() -> datetime:
    return datetime.now(timezone.utc)


def google_maps_directions_url(lat: float, lng: float) -> str:
    return f"https://www.google.com/maps/dir/?api=1&destination={lat},{lng}"


class LostLocationService:
    def __init__(
        self,
        *,
        replier: Any,
        authorization_service: Any,
        user_profile_service: Any = None,
        repository: Any = LostSessionRepository,
        liff_id: str = "",
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self._replier = replier
        self._authorization_service = authorization_service
        self._user_profile_service = user_profile_service
        self._repository = repository
        self._liff_id = (liff_id or "").strip()
        self._clock = clock

    # ── LIFF 網址 ─────────────────────────────────────────────────────

    def share_url(self) -> Optional[str]:
        """長輩的定位頁。沒設 LIFF_ID 時回 None，卡片就不放按鈕。"""
        if not self._liff_id:
            return None
        return f"https://liff.line.me/{self._liff_id}/lost/share"

    def watch_url(self, patient_user_id: str) -> Optional[str]:
        if not self._liff_id:
            return None
        return (
            f"https://liff.line.me/{self._liff_id}/lost/watch"
            f"?user={quote(patient_user_id, safe='')}"
        )

    # ── 開始求救 ──────────────────────────────────────────────────────

    async def report(self, user_id: str, words: str, intent: str) -> LostReport:
        active = await self._repository.find_active(user_id)
        if active is not None:
            return LostReport("already_active", active)

        recipients = await self._recipients(user_id)
        if not recipients:
            return LostReport("no_family")

        now = self._clock()
        document = {
            "session_id": uuid.uuid4().hex,
            "user_id": user_id,
            "status": ACTIVE,
            "intent": intent,
            "patient_words": words or "",
            "started_at": now,
            "auto_end_at": now + AUTO_END_AFTER,
            "purge_at": now + PURGE_AFTER,
            "last_location": None,
            "last_seen_at": None,
            "trail": [],
            "location_notified_at": None,
            "stale_notified_at": None,
            "ended_at": None,
            "ended_by": None,
        }
        session, created = await self._repository.create_active(document)
        return LostReport("started" if created else "already_active", session)

    def elder_card(self, header_key: str, language: Optional[str] = None, font_size: Optional[str] = None):
        return build_elder_share_flex(
            header_key=header_key,
            share_url=self.share_url(),
            language=language,
            font_size=font_size,
        )

    async def notify_family_of_report(self, session: dict[str, Any]) -> bool:
        """推第一張通報給每位家人。回傳是否至少送到一位。"""
        user_id = session["user_id"]
        intent = session.get("intent") or "lost"
        patient_name = await self._patient_name(user_id)
        watch_url = self.watch_url(user_id)

        async def build(prefs: _Prefs):
            name = patient_name or t("emergency_family.fallback_name", prefs.language)
            flex = build_family_alert_flex(
                intent=intent,
                patient_name=name,
                patient_words=session.get("patient_words") or "",
                watch_url=watch_url,
                language=prefs.language,
                font_size=prefs.font_size,
            )
            return flex, family_alert_alt_text(intent, name, prefs.language)

        sent = await self._push_to_family(user_id, build)
        logger.info(f"{LOGGER_HEADER_TEXT} 走失通報推播完成 sent=%s", sent)
        return sent

    # ── 位置 ──────────────────────────────────────────────────────────

    async def record_location(
        self,
        user_id: str,
        *,
        lat: float,
        lng: float,
        accuracy: Optional[float] = None,
        source: str = "liff",
    ) -> Optional[dict[str, Any]]:
        """寫入最新位置。沒有進行中的求救時回傳 None（不存任何位置）。"""
        now = self._clock()
        point = {
            "lat": lat,
            "lng": lng,
            "accuracy": accuracy,
            "source": source,
            "received_at": now,
        }
        return await self._repository.append_location(user_id, point, now)

    def needs_location_started_notice(self, session: dict[str, Any]) -> bool:
        return session.get("location_notified_at") is None

    async def notify_location_started(self, session: dict[str, Any]) -> bool:
        """第一次收到位置時通知家人一次。沒搶到推播權（別人推過了）回 False。"""
        claimed = await self._repository.claim_notice(
            session["session_id"], "location_notified_at", self._clock()
        )
        if not claimed:
            return False
        user_id = session["user_id"]
        patient_name = await self._patient_name(user_id)
        watch_url = self.watch_url(user_id)

        async def build(prefs: _Prefs):
            name = patient_name or t("emergency_family.fallback_name", prefs.language)
            title = t("lost.family.started.alt_text", prefs.language).format(name=name)
            flex = build_family_notice_flex(
                title=title,
                body_text=t("lost.family.started.body", prefs.language),
                watch_url=watch_url,
                language=prefs.language,
                font_size=prefs.font_size,
            )
            return flex, title

        return await self._push_to_family(user_id, build)

    # ── 家人查看 ──────────────────────────────────────────────────────

    async def can_view(self, operator_id: str, owner_id: str) -> bool:
        if operator_id == owner_id:
            return True
        return operator_id in await self._recipients(owner_id)

    async def latest(self, owner_id: str) -> Optional[dict[str, Any]]:
        return await self._repository.find_latest(owner_id)

    async def active(self, owner_id: str) -> Optional[dict[str, Any]]:
        return await self._repository.find_active(owner_id)

    async def display_name(self, user_id: str) -> str:
        return await self._patient_name(user_id)

    def is_stale(self, session: dict[str, Any]) -> bool:
        last_seen = as_utc(session.get("last_seen_at"))
        if session.get("status") != ACTIVE or last_seen is None:
            return False
        return self._clock() - last_seen > STALE_AFTER

    # ── 結束 ──────────────────────────────────────────────────────────

    async def end_by_family(self, owner_id: str, finder_id: str) -> Optional[dict[str, Any]]:
        session = await self._repository.end_active(
            owner_id, "found", finder_id, self._clock()
        )
        if session is None:
            return None
        patient_name = await self._patient_name(owner_id)
        finder_name = await self._patient_name(finder_id)

        async def build_family(prefs: _Prefs):
            text = t("lost.family.found", prefs.language).format(
                finder=finder_name or t("lost.family.someone", prefs.language),
                name=patient_name or t("emergency_family.fallback_name", prefs.language),
            )
            return None, text

        await self._push_to_family(owner_id, build_family, skip={finder_id})
        prefs = await self._prefs(owner_id)
        await self._safe_push_text(
            owner_id,
            t("lost.elder.found", prefs.language).format(
                finder=finder_name or t("lost.family.someone", prefs.language)
            ),
        )
        return session

    async def end_by_elder(self, user_id: str) -> Optional[dict[str, Any]]:
        session = await self._repository.end_active(
            user_id, "safe", user_id, self._clock()
        )
        if session is None:
            return None
        patient_name = await self._patient_name(user_id)

        async def build(prefs: _Prefs):
            text = t("lost.family.elder_safe", prefs.language).format(
                name=patient_name or t("emergency_family.fallback_name", prefs.language)
            )
            return None, text

        await self._push_to_family(user_id, build)
        return session

    # ── 排程 ──────────────────────────────────────────────────────────

    async def process_due(self, now: Optional[datetime] = None) -> None:
        """scheduler 每分鐘呼叫一次。自動結束先做：要結束的就不用再通知停止更新。"""
        now = now or self._clock()
        for session in await self._repository.find_due_to_end(now):
            try:
                await self._auto_end(session, now)
            except Exception:  # noqa: BLE001
                logger.exception(f"{LOGGER_HEADER_TEXT} 自動結束失敗")
        for session in await self._repository.find_stale(now - STALE_AFTER):
            try:
                await self._notify_stale(session, now)
            except Exception:  # noqa: BLE001
                logger.exception(f"{LOGGER_HEADER_TEXT} 停止更新通知失敗")

    async def _auto_end(self, session: dict[str, Any], now: datetime) -> None:
        user_id = session["user_id"]
        ended = await self._repository.end_active(
            user_id,
            "expired",
            "system",
            now,
            extra_filter={"session_id": session["session_id"]},
        )
        if ended is None:
            return
        patient_name = await self._patient_name(user_id)
        hours = int(AUTO_END_AFTER.total_seconds() // 3600)

        async def build(prefs: _Prefs):
            text = t("lost.family.auto_ended", prefs.language).format(
                name=patient_name or t("emergency_family.fallback_name", prefs.language),
                hours=hours,
            )
            return None, text

        await self._push_to_family(user_id, build)
        prefs = await self._prefs(user_id)
        await self._safe_push_text(user_id, t("lost.elder.auto_ended", prefs.language))
        logger.info(f"{LOGGER_HEADER_TEXT} 求救已自動結束")

    async def _notify_stale(self, session: dict[str, Any], now: datetime) -> None:
        # 條件帶上當時看到的 last_seen_at：查詢之後、搶佔之前剛好又收到新位置，
        # 就不該再說它停止更新了。
        claimed = await self._repository.claim_notice(
            session["session_id"],
            "stale_notified_at",
            now,
            extra_filter={"status": ACTIVE, "last_seen_at": session.get("last_seen_at")},
        )
        if not claimed:
            return
        user_id = session["user_id"]
        last_seen = as_utc(session.get("last_seen_at")) or now
        minutes = max(1, int((now - last_seen).total_seconds() // 60))
        patient_name = await self._patient_name(user_id)
        watch_url = self.watch_url(user_id)
        location = session.get("last_location") or {}
        navigate_url = (
            google_maps_directions_url(location["lat"], location["lng"])
            if "lat" in location and "lng" in location
            else None
        )

        async def build(prefs: _Prefs):
            name = patient_name or t("emergency_family.fallback_name", prefs.language)
            title = t("lost.family.stale.alt_text", prefs.language).format(name=name)
            flex = build_family_notice_flex(
                title=title,
                body_text=t("lost.family.stale.body", prefs.language).format(minutes=minutes),
                watch_url=watch_url,
                navigate_url=navigate_url,
                accent=LOST_ACCENT,
                language=prefs.language,
                font_size=prefs.font_size,
            )
            return flex, title

        await self._push_to_family(user_id, build)

        # 請長輩重新打開定位頁。畫面被關掉最常見的原因就是他不知道要一直開著。
        prefs = await self._prefs(user_id)
        card = build_elder_share_flex(
            header_key="lost.elder.reopen.header",
            body_key="lost.elder.reopen.body",
            share_url=self.share_url(),
            language=prefs.language,
            font_size=prefs.font_size,
        )
        await self._push_with_fallback(user_id, card, t("lost.elder.reopen.body", prefs.language))

    # ── 共用 ──────────────────────────────────────────────────────────

    async def _recipients(self, user_id: str) -> list[str]:
        """長輩本人恆不在此清單內。判定失敗視為沒有收件人（不猜）。"""
        if self._authorization_service is None:
            return []
        try:
            recipients = await self._authorization_service.notification_recipients(
                user_id, NOTIFICATION_KIND
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"{LOGGER_HEADER_TEXT} 收件人判定失敗：%s", type(exc).__name__
            )
            return []
        unique: list[str] = []
        for uid in recipients or []:
            if uid and uid != user_id and uid not in unique:
                unique.append(uid)
        return unique

    async def _profile(self, user_id: str) -> dict[str, Any]:
        if not self._user_profile_service or not user_id:
            return {}
        try:
            profile = await self._user_profile_service.get_user_profile(user_id)
        except Exception:  # noqa: BLE001
            return {}
        return profile if isinstance(profile, dict) else {}

    async def _patient_name(self, user_id: str) -> str:
        """查不到時回空字串，由各收件人依自己的語言填泛稱。"""
        return (await self._profile(user_id)).get("name") or ""

    async def _prefs(self, user_id: str) -> _Prefs:
        settings = (await self._profile(user_id)).get("settings") or {}
        return _Prefs(
            normalize_user_language(settings.get("language") or DEFAULT_USER_LANGUAGE),
            normalize_user_font_size(settings.get("font_size") or DEFAULT_USER_FONT_SIZE),
            # 欄位缺席時視為開啟，同緊急通報。
            bool(settings.get("notify_family", True)),
        )

    async def _push_to_family(
        self,
        user_id: str,
        build: Callable[[_Prefs], Any],
        skip: frozenset[str] | set[str] = frozenset(),
    ) -> bool:
        """逐位家人以他自己的語言與字級組訊息並推播。

        `build(prefs)` 回傳 `(flex 或 None, 純文字)`：flex 為 None 時只送純文字。
        收件人關掉「家人通知」就不送，理由同緊急通報（emergency_alert_service
        的模組說明）：那是他明示的意願。
        """
        sent = False
        for member_id in await self._recipients(user_id):
            if member_id in skip:
                continue
            prefs = await self._prefs(member_id)
            if not prefs.notify_family:
                logger.info(f"{LOGGER_HEADER_TEXT} 收件人關閉了家人通知，略過")
                continue
            flex, text = await build(prefs)
            if flex is None:
                ok = await self._safe_push_text(member_id, text)
            else:
                ok = await self._push_with_fallback(member_id, flex, text)
            sent = sent or ok
        return sent

    async def _push_with_fallback(self, user_id: str, flex: Any, text: str) -> bool:
        """Flex 超過大小上限或推不出去就改送純文字，理由同緊急通報。"""
        try:
            if fits(flex.contents.to_dict()) and await self._replier.push_flex(user_id, flex):
                return True
        except Exception:  # noqa: BLE001
            logger.warning(f"{LOGGER_HEADER_TEXT} Flex 推播失敗，改送純文字", exc_info=True)
        return await self._safe_push_text(user_id, text)

    async def _safe_push_text(self, user_id: str, text: str) -> bool:
        try:
            return bool(await self._replier.push_text(user_id, text))
        except Exception:  # noqa: BLE001
            logger.warning(f"{LOGGER_HEADER_TEXT} 推播失敗", exc_info=True)
            return False
