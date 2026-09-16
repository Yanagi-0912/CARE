"""血壓、血糖超出範圍與經期異常的推播（health-alerts spec）。

沿用 ``emergency_alert_service``／``otc_alert_service`` 的形狀：收件人判定
→ 逐人查偏好 → 建 Flex → push，任何失敗都吞在對外的唯一入口，不影響已經
寫入的紀錄——紀錄 SHALL 先寫入，推播才進行（spec「推播失敗不影響紀錄」）。
呼叫端（``health_measurement_service``／``menstrual_service``）在存檔「之後」
呼叫這裡的公開方法即可，不需要自己再包一層 try/except。

決策依據見 ``.superpowers/sdd/tasks/task-7-dispatch-notes.md``：

- 只有 ``above_range``／``below_range`` 推播；量測時間早於送出時間 6 小時
  以上的補記不推播（spec「過時的補記不推播」）。
- 節流 key：``bp_high``／``bp_low``／``glucose_high``／``glucose_low``
  （30 分鐘一個 claim），經期異常是 ``menstrual:<record_id>``（一個長 TTL
  的 claim，等同「這筆紀錄最多通知一次」）。
- 收件人：本人恆收、不受 ``notify_family`` 影響；加上
  ``notification_recipients(..., has_legacy_equivalent=False)`` 篩出的家人
  （GUARDIAN／CAREGIVER，含有效委任），各自套用自己的 ``notify_family``。
  這個判定 SHALL NOT 受遷移狀態影響——這種通知在本能力導入前不存在。
- 經期異常只送本人，SHALL NOT 呼叫 ``notification_recipients``。
- ``HEALTH_ALERTS_ENABLED`` 關閉時，等級照常判定與儲存，只是不推播——這裡
  在對外方法的第一行短路，呼叫端不需要知道這個旗標存在。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, List, Optional, Protocol, Tuple

from app.core.user_font_size import DEFAULT_USER_FONT_SIZE, normalize_user_font_size
from app.core.user_language import DEFAULT_USER_LANGUAGE, normalize_user_language
from app.models.health import HealthAlertThreshold, HealthMeasurement, MeasurementKind
from app.repositories.health_alert_claim_repository import HealthAlertClaimRepository
from app.services.health.health_level import _GLUCOSE_UPPER_FIELD_BY_MEAL_CONTEXT
from app.services.line_messaging.flex.health_alert_flex import (
    build_health_alert_flex,
    health_alert_alt_text,
)
from app.services.line_messaging.flex.menstrual_alert_flex import (
    build_menstrual_alert_flex,
    menstrual_alert_alt_text,
)
from app.services.line_messaging.rich_menu_layout import liff_uri
from resources.flex_messages.size_guard import fits

logger = logging.getLogger(__name__)

LOGGER_HEADER_TEXT = "[Services:HealthAlert]"

# 通知政策的種類（Task 1：{GUARDIAN, CAREGIVER}，見
# ``app/models/family_authorization.py`` 的 ``NOTIFICATION_POLICY``）。
NOTIFICATION_KIND = "health_out_of_range"

# 過時的補記不推播（health-alerts spec「過時的補記不推播」）。
BACKFILL_MAX_AGE = timedelta(hours=6)

# 重複推播的節流視窗（health-alerts spec「重複推播的節流」）。
THROTTLE_TTL_MINUTES = 30

# 經期異常的 claim 沒有真正的「節流」語意，只是要「這筆紀錄最多通知一次」
# ——用一個遠超過任何實際使用情境的 TTL（約 400 天），讓它等同永久佔用，
# 不必另外在應用層判斷「這筆紀錄推播過了沒」。
MENSTRUAL_CLAIM_TTL_MINUTES = 400 * 24 * 60

# 血壓／血糖 × 高於／低於範圍 → 節流 key，同時也是 Flex 內容的變化 key
# （``flex.health_alert.header.<key>``／``flex.health_alert.alt.<key>``）。
_ALERT_KEY_BY_CATEGORY: dict[Tuple[MeasurementKind, str], str] = {
    ("blood_pressure", "above_range"): "bp_high",
    ("blood_pressure", "below_range"): "bp_low",
    ("blood_glucose", "above_range"): "glucose_high",
    ("blood_glucose", "below_range"): "glucose_low",
}

HEALTH_RECORDS_PATH = "/health-records"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    """Motor 存取一圈後常見的 naive datetime 視為 UTC（同
    ``app/models/health.py`` 的既有慣例）。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class _Replier(Protocol):
    async def push_flex(self, user_id: str, flex: Any) -> bool: ...
    async def push_text(self, user_id: str, text: str) -> bool: ...


class HealthAlertService:
    def __init__(
        self,
        *,
        replier: _Replier,
        claim_repository: Any = HealthAlertClaimRepository,
        authorization_service: Any = None,
        user_profile_service: Any = None,
        enabled: bool = False,
        liff_url: str = "",
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._replier = replier
        self._claim_repository = claim_repository
        self._authorization_service = authorization_service
        self._user_profile_service = user_profile_service
        self._enabled = enabled
        self._liff_url = liff_url
        self._clock = clock

    # ── 對外入口：血壓／血糖超出範圍 ─────────────────────────────────

    async def notify_out_of_range(
        self,
        measurement: HealthMeasurement,
        thresholds: Optional[HealthAlertThreshold],
    ) -> None:
        """紀錄存檔之後呼叫。任何失敗都吞在這裡，SHALL NOT 影響已經完成的
        寫入（spec「推播失敗不影響紀錄」）。

        ``thresholds`` 必須是記錄當下實際用來判定等級的那一份——這裡
        SHALL NOT 重新讀取，避免與已經儲存的等級對不上（dispatch notes）。
        """
        if not self._enabled:
            return
        try:
            await self._notify_out_of_range(measurement, thresholds)
        except Exception:  # noqa: BLE001
            logger.warning(
                f"{LOGGER_HEADER_TEXT} 超出範圍推播失敗，紀錄本身不受影響",
                exc_info=True,
            )

    async def _notify_out_of_range(
        self,
        measurement: HealthMeasurement,
        thresholds: Optional[HealthAlertThreshold],
    ) -> None:
        if measurement.level not in ("above_range", "below_range"):
            return

        if _as_utc(measurement.measured_at) < self._clock() - BACKFILL_MAX_AGE:
            return

        alert_key = _ALERT_KEY_BY_CATEGORY.get((measurement.kind, measurement.level))
        if alert_key is None:
            return

        claimed = await self._claim_repository.try_claim(
            measurement.user_id, alert_key, THROTTLE_TTL_MINUTES
        )
        if not claimed:
            return

        recipients = await self._out_of_range_recipients(measurement.user_id)
        if not recipients:
            return

        exceeded = _exceeded_bounds(measurement, thresholds)
        recorder_name: Optional[str] = None
        if measurement.recorded_by != measurement.user_id:
            recorder_name = await self._display_name(measurement.recorded_by)

        for member_id in recipients:
            is_owner = member_id == measurement.user_id
            language, font_size, _ = await self._display_prefs(member_id)
            patient_name = None if is_owner else await self._display_name(measurement.user_id)
            flex = build_health_alert_flex(
                alert_key=alert_key,
                measurement=measurement,
                exceeded=exceeded,
                recorder_name=recorder_name,
                patient_name=patient_name,
                button_uri=self._records_uri(measurement.user_id, is_owner=is_owner),
                language=language,
                font_size=font_size,
            )
            await self._push(member_id, flex, alert_key, language)

    async def _push(self, member_id: str, flex: Any, alert_key: str, language: str) -> None:
        """送出一則。超過大小上限或 push_flex 未成功都退回純文字——寧可少了
        版面也不要讓收件人什麼都收不到（同 ``emergency_alert_service`` 的
        慣例）。"""
        try:
            if not fits(flex.contents.to_dict()):
                logger.warning(f"{LOGGER_HEADER_TEXT} 超出範圍卡超過大小上限，改以純文字送出")
                await self._replier.push_text(member_id, health_alert_alt_text(alert_key, language))
                return
            if await self._replier.push_flex(member_id, flex):
                return
            await self._replier.push_text(member_id, health_alert_alt_text(alert_key, language))
        except Exception:  # noqa: BLE001
            logger.warning(f"{LOGGER_HEADER_TEXT} 推播失敗，本位收件人未收到", exc_info=True)

    async def _out_of_range_recipients(self, owner_id: str) -> List[str]:
        """本人恆收（不受 ``notify_family`` 影響）＋通過家人健康通知設定的
        家人（spec「超出範圍的推播對象」）。"""
        recipients = [owner_id]
        for member_id in await self._family_recipients(owner_id):
            if not member_id or member_id == owner_id:
                continue
            _, _, notify_family = await self._display_prefs(member_id)
            if notify_family:
                recipients.append(member_id)
        return recipients

    async def _family_recipients(self, owner_id: str) -> List[str]:
        if self._authorization_service is None:
            return []
        try:
            return await self._authorization_service.notification_recipients(
                owner_id, NOTIFICATION_KIND, has_legacy_equivalent=False
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"{LOGGER_HEADER_TEXT} 收件人判定失敗：%s", type(exc).__name__)
            return []

    def _records_uri(self, owner_id: str, *, is_owner: bool) -> str:
        """開啟健康紀錄頁的按鈕連結；本人是 ``/health-records``，家人是帶
        ``?user=<owner_id>`` 的版本（前端在檢視者持有嚴格 SENSITIVE READ 時
        顯示該位家人的紀錄）。``LIFF_URL`` 未設定時回空字串，呼叫端據此不
        顯示按鈕，同其餘推播服務的慣例。"""
        if not self._liff_url:
            return ""
        path = HEALTH_RECORDS_PATH if is_owner else f"{HEALTH_RECORDS_PATH}?user={owner_id}"
        return liff_uri(self._liff_url, path)

    # ── 對外入口：經期異常 ───────────────────────────────────────────

    async def notify_menstrual_anomaly(self, user_id: str, record_id: str) -> None:
        """經期新增、或補上／改動結束日期之後，確認這筆紀錄異常成立時呼叫。
        只通知本人，SHALL NOT 呼叫 ``notification_recipients``（spec「經期
        異常只通知本人」）。任何失敗都吞在這裡。
        """
        if not self._enabled or not user_id or not record_id:
            return
        try:
            await self._notify_menstrual_anomaly(user_id, record_id)
        except Exception:  # noqa: BLE001
            logger.warning(f"{LOGGER_HEADER_TEXT} 經期異常推播失敗", exc_info=True)

    async def _notify_menstrual_anomaly(self, user_id: str, record_id: str) -> None:
        claimed = await self._claim_repository.try_claim(
            user_id, f"menstrual:{record_id}", MENSTRUAL_CLAIM_TTL_MINUTES
        )
        if not claimed:
            # 同一筆紀錄已經通知過（spec「重複推播的節流」：「同一筆經期紀錄
            # 的異常 SHALL 最多通知一次」）。
            return

        language, font_size, _ = await self._display_prefs(user_id)
        flex = build_menstrual_alert_flex(
            button_uri=self._records_uri(user_id, is_owner=True),
            language=language,
            font_size=font_size,
        )
        try:
            if fits(flex.contents.to_dict()) and await self._replier.push_flex(user_id, flex):
                return
            await self._replier.push_text(user_id, menstrual_alert_alt_text(language))
        except Exception:  # noqa: BLE001
            logger.warning(f"{LOGGER_HEADER_TEXT} 經期異常推播送出失敗", exc_info=True)

    # ── 收件人偏好 ───────────────────────────────────────────────────

    async def _display_prefs(self, user_id: str) -> Tuple[str, str, bool]:
        """逐一取收件人自己的語言、字級與家人健康通知意願。背景推播沒有
        request context 可以繼承（同 ``emergency_alert_service`` 的慣例）。
        """
        if not self._user_profile_service or not user_id:
            return DEFAULT_USER_LANGUAGE, DEFAULT_USER_FONT_SIZE, True
        try:
            profile = await self._user_profile_service.get_user_profile(user_id)
        except Exception:  # noqa: BLE001
            # Task 7 修復：個人資料讀取失敗時 SHALL 對「家人」收件人 fail
            # 關（``notify_family`` 視為 False），不是 fail 開（原本是
            # True）。本人不受影響——本人在 `_out_of_range_recipients` 一律
            # 無條件先加入收件人清單，不會呼叫這裡判斷是否要收；只有透過
            # `_out_of_range_recipients` 篩選家人收件人時才會用到這裡回傳的
            # 第三個值。一個已經明確關閉家人健康推播的人，不該因為個人資料
            # 儲存層短暫故障就被覆寫成「照樣收到」——基礎設施的錯誤 SHALL
            # NOT 蓋過使用者明確的退出選擇。
            return DEFAULT_USER_LANGUAGE, DEFAULT_USER_FONT_SIZE, False
        settings: dict = (profile or {}).get("settings") or {}
        return (
            normalize_user_language(settings.get("language")),
            normalize_user_font_size(settings.get("font_size")),
            # 欄位缺席時視為開啟——既有使用者的文件沒有這一欄，不需要 backfill。
            bool(settings.get("notify_family", True)),
        )

    async def _display_name(self, user_id: str) -> str:
        if not self._user_profile_service or not user_id:
            return ""
        try:
            profile = await self._user_profile_service.get_user_profile(user_id)
        except Exception:  # noqa: BLE001
            return ""
        if isinstance(profile, dict) and profile.get("name"):
            return profile["name"]
        return ""


def _exceeded_bounds(
    measurement: HealthMeasurement, thresholds: Optional[HealthAlertThreshold]
) -> List[Tuple[str, str, int]]:
    """回傳這筆紀錄實際超過的每一個範圍值：``(欄位, "upper"／"lower", 界線值)``
    （health-alerts spec「超出範圍推播的內容」：「同時呈現數值與上限」）。

    只回報與這筆紀錄等級同方向的那一側——``above_range`` 只看上限、
    ``below_range`` 只看下限，同 ``health_level.classify_measurement``
    判定等級時的方向；一筆紀錄可能同時有兩個欄位（例如血壓的收縮壓與
    舒張壓）都超過，因此回傳列表而不是單一值。
    """
    if thresholds is None:
        return []

    pairs: List[Tuple[str, Optional[int], Optional[int], Optional[int]]]
    if measurement.kind == "blood_pressure":
        pairs = [
            ("systolic", measurement.systolic, thresholds.systolic_high, thresholds.systolic_low),
            ("diastolic", measurement.diastolic, thresholds.diastolic_high, thresholds.diastolic_low),
        ]
    else:
        upper_field = _GLUCOSE_UPPER_FIELD_BY_MEAL_CONTEXT.get(measurement.meal_context or "")
        upper = getattr(thresholds, upper_field) if upper_field else None
        pairs = [("glucose", measurement.glucose_mg_dl, upper, thresholds.glucose_low)]

    exceeded: List[Tuple[str, str, int]] = []
    for field, value, upper, lower in pairs:
        if value is None:
            continue
        if measurement.level == "above_range" and upper is not None and value > upper:
            exceeded.append((field, "upper", upper))
        elif measurement.level == "below_range" and lower is not None and value < lower:
            exceeded.append((field, "lower", lower))
    return exceeded
