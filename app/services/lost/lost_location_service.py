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

長輩也看得到家人：家人的地圖頁每次輪詢都回報一次（`record_presence`），長輩
的定位頁從上傳位置的回應拿到 `family_status`——誰正開著地圖、誰按了「我去找他」、
離他多遠。家人的位置只在他按了「我去找他」之後才上傳：人在上班、沒要出門的家人
不該被當成正在趕過去，也不該沒問過就把他的位置送出去。第一次按「我去找他」時推
一則給長輩（`notify_elder_family_coming`）：長輩常把定位頁關掉，這一則是他最需要
收到的。「正在看地圖」不推，否則家人每打開一次地圖長輩就收到一則。

誰收得到、誰看得到地圖、誰按得了「已找到」都是同一份名單：
`notification_recipients(長輩, "elder_lost")`。收到通報卻打不開地圖，或打得開
地圖卻沒收到通報，都說不通。

每一則推播都先原子取得推播權（lost_session_repository.claim_notice／end_active），
API pod 與 scheduler pod、或滾動更新時並存的兩個 scheduler 才不會各推一次。
"""

from __future__ import annotations

import logging
import math
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
from app.services.lost.lost_classifier import LostDetection, LostIntentDetector
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

# 家人多久沒輪詢算離開了地圖頁。地圖頁每 15 秒輪詢一次（CARE-LIFF
# src/pages/Lost/WatchPage.tsx 的 POLL_INTERVAL_MS），45 秒是連漏 3 次：網路
# 偶爾慢一輪不會讓長輩看到家人忽上忽下。
FAMILY_ONLINE_WITHIN = timedelta(seconds=45)

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


def distance_meters(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """兩點的大圓距離（haversine，地球半徑取平均 6,371 公里）。

    長輩看的是「離你約 800 公尺」，只需要到百公尺的精度；走路實際要繞的距離
    一定比這長，畫面上寫的是「約」。
    """
    radius = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lng2 - lng1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


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
        intent_detector: Optional[LostIntentDetector] = None,
    ) -> None:
        self._replier = replier
        self._authorization_service = authorization_service
        self._user_profile_service = user_profile_service
        self._repository = repository
        self._liff_id = (liff_id or "").strip()
        self._clock = clock
        # 沒注入時只用關鍵字（測試與模型檔缺席時）；正式環境由 dependencies 載入分類器。
        self._intent_detector = intent_detector or LostIntentDetector()

    def detect_intent(self, text: str) -> LostDetection:
        return self._intent_detector.detect(text)

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

    # ── 長輩看家人 ────────────────────────────────────────────────────

    async def record_presence(
        self,
        owner_id: str,
        member_id: str,
        *,
        coming: bool,
        lat: Optional[float] = None,
        lng: Optional[float] = None,
        accuracy: Optional[float] = None,
    ) -> Optional[dict[str, Any]]:
        """家人的地圖頁回報一次。沒有進行中的求救時回傳 None（什麼都不存）。"""
        now = self._clock()
        point = None
        if coming and lat is not None and lng is not None:
            point = {"lat": lat, "lng": lng, "accuracy": accuracy, "received_at": now}
        return await self._repository.record_presence(
            owner_id, member_id, now, coming=coming, point=point
        )

    def needs_coming_notice(self, session: dict[str, Any], member_id: str) -> bool:
        member = (session.get("family") or {}).get(member_id) or {}
        return bool(member.get("coming")) and member.get("coming_notified_at") is None

    async def notify_elder_family_coming(self, session: dict[str, Any], member_id: str) -> bool:
        """某位家人第一次按「我去找他」時推一則給長輩。一次求救每位家人最多一則。"""
        claimed = await self._repository.claim_notice(
            session["session_id"], f"family.{member_id}.coming_notified_at", self._clock()
        )
        if not claimed:
            return False
        user_id = session["user_id"]
        prefs = await self._prefs(user_id)
        name = await self._patient_name(member_id) or t("lost.family.someone", prefs.language)
        return await self._safe_push_text(
            user_id, t("lost.elder.family_coming", prefs.language).format(name=name)
        )

    async def family_status(self, session: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
        """長輩定位頁上的家人：開著地圖的、按了「我去找他」的。

        按了「我去找他」的家人即使離開了地圖頁（多半是切去導航）也留在清單上，
        位置附上時間，由畫面寫「幾分鐘前」。排序：正在過來的在前、近的在前。
        """
        if not session or session.get("status") != ACTIVE:
            return []
        now = self._clock()
        elder = session.get("last_location") or {}
        members: list[dict[str, Any]] = []
        for member_id, state in (session.get("family") or {}).items():
            seen_at = as_utc(state.get("seen_at"))
            online = seen_at is not None and now - seen_at <= FAMILY_ONLINE_WITHIN
            coming = bool(state.get("coming"))
            if not (online or coming):
                continue
            location = state.get("location") if coming else None
            distance = None
            if location and "lat" in elder and "lng" in elder:
                distance = distance_meters(
                    elder["lat"], elder["lng"], location["lat"], location["lng"]
                )
            members.append(
                {
                    "name": await self._patient_name(member_id),
                    "online": online,
                    "coming": coming,
                    "location": location,
                    "distance_m": distance,
                }
            )
        members.sort(
            key=lambda m: (
                not m["coming"],
                m["distance_m"] is None,
                m["distance_m"] or 0.0,
            )
        )
        return members

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
