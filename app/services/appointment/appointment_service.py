"""掛號提醒的業務邏輯：建立、修改、刪除、查詢，以及「我已出發／我已到診」。

CRUD 的授權由 router 經 FamilyAuthorizationService 判定，與用藥提醒同一條慣例。
出發／到診例外，授權放在這裡：這兩個動作有兩個入口——LIFF 的 POST 端點與 LINE
卡片上的 postback——兩邊必須走同一個判定，不能一邊查了、一邊忘了查。
"""

import logging
from datetime import datetime, timezone
from typing import Any, Callable, List, Optional

from fastapi import HTTPException

from app.i18n import t
from app.models.appointment import (
    NULLABLE_TEXT_FIELDS,
    REQUIRED_TEXT_FIELDS,
    TEXT_FIELD_LABELS,
    TEXT_FIELD_LIMITS,
    AppointmentReminder,
    CreateAppointmentReminderRequest,
    UpdateAppointmentReminderRequest,
    actionable_from,
    day_end_for,
    utc_offset_minutes,
)
from app.repositories.appointment_repository import AppointmentReminderRepository

logger = logging.getLogger(__name__)

# 前端會把 detail 原樣顯示給使用者，所以每一則都是完整的一句話。
TIMEZONE_REQUIRED_DETAIL = "門診時間必須帶時區，例如 2026-09-15T09:30:00+08:00。"
IN_THE_PAST_DETAIL = "門診時間已經過了，請確認日期與時間。"
RESCHEDULE_ATTENDED_DETAIL = (
    "已經回報到診的掛號不能改時間；如果是另一次門診，請新增一筆掛號提醒。"
)
DUPLICATE_DETAIL = "這個時間已經有一筆同醫院、同科別的掛號提醒，不需要重複建立。"


def _norm_key(value: Optional[str]) -> str:
    """比對用：去掉所有空白、不分大小寫。「心臟 內科」與「心臟內科」是同一科。"""
    return "".join((value or "").split()).casefold()


def _is_same_appointment(
    *,
    facility_id: Optional[str],
    hospital_name: str,
    department: str,
    other: AppointmentReminder,
) -> bool:
    """同一個門診瞬間下，兩筆是不是同一次掛號。

    醫院的比法：兩邊都有 facility_id 時只看 id——連鎖診所的各分院常常同名，
    名稱相同不代表是同一家。只要有一邊沒有 id（手動輸入的院所），才退回比院名。
    """
    if _norm_key(department) != _norm_key(other.department):
        return False
    if facility_id and other.facility_id:
        return facility_id == other.facility_id
    return _norm_key(hospital_name) == _norm_key(other.hospital_name)


def _utcnow() -> datetime:
    # 精確到秒：時間欄位會原樣回給前端，微秒只會讓同一個時刻有兩種寫法
    # （剛建立時六位數、從 Mongo 讀回來剩三位數）。
    return datetime.now(timezone.utc).replace(microsecond=0)


class AppointmentError(Exception):
    """服務層的錯誤。

    `detail` 是 API 回給前端、會直接顯示給使用者的繁中字串。`code` 有值時，
    LINE 那一側改用 i18n 的 `appt.error.{code}` 依收件人語言呈現——兩個通道的
    繁中文案是同一份（`detail` 就是從同一個 key 取的）。
    """

    def __init__(self, status_code: int, detail: str, code: Optional[str] = None):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.code = code

    @classmethod
    def of(cls, status_code: int, code: str) -> "AppointmentError":
        return cls(status_code, t(f"appt.error.{code}", "zh-TW"), code)

    def localized(self, language: Optional[str]) -> str:
        if self.code:
            return t(f"appt.error.{self.code}", language)
        return self.detail


def _require_aware_minute(value: datetime) -> datetime:
    """門診時間必須帶 offset，並截到分鐘。

    不帶 offset 的時間不知道是哪裡的 09:30——猜成 UTC 會讓提醒晚 8 小時送達，
    猜成台北則在其他時區靜默出錯。掛號錯過就是錯過，寧可擋下來。
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise AppointmentError(400, TIMEZONE_REQUIRED_DETAIL)
    return value.replace(second=0, microsecond=0)


def _clean_text(field: str, value: Optional[str]) -> Optional[str]:
    """去頭尾空白；可為 null 的欄位把空字串收斂成 null，必填欄位擋下空字串。

    收斂成 null 是為了讓「沒有值」只有一種表示法——輸入框清空後送出的 ""
    與明確送 null 在資料庫裡是同一件事，前端讀回來也只需要判斷 null。
    """
    if value is None:
        return None
    stripped = value.strip()
    label = TEXT_FIELD_LABELS[field]
    if not stripped:
        if field in REQUIRED_TEXT_FIELDS:
            raise AppointmentError(400, f"{label}不能是空的。")
        return None
    limit = TEXT_FIELD_LIMITS[field]
    if len(stripped) > limit:
        raise AppointmentError(400, f"{label}最多 {limit} 個字。")
    return stripped


def _schedule_fields(appointment_at: datetime) -> dict:
    offset = utc_offset_minutes(appointment_at)
    return {
        "appointment_at": appointment_at.astimezone(timezone.utc),
        "appointment_utc_offset_minutes": offset,
        "day_end_at": day_end_for(appointment_at, offset),
    }


# 改了門診時間就是另一個時間點的門診：狀態回到 scheduled、三個階段重新排定。
# 舊時間的「已出發」不能帶到新時間去——那會讓新時間的 T-1h 不發（它只挑
# scheduled），而使用者其實還沒出發。
_RESET_ON_RESCHEDULE = {
    "status": "scheduled",
    "departed_at": None,
    "departed_by_user_id": None,
    "attended_at": None,
    "attended_by_user_id": None,
    "pre_reminder_sent": False,
    "start_reminder_sent": False,
    "caregiver_alert_sent": False,
    "pre_reminder_attempts": 0,
    "start_reminder_attempts": 0,
    "caregiver_alert_attempts": 0,
}

# 出發／到診失敗時，依重讀到的現況決定回哪一則。
_CONFLICT_CODE_BY_STATUS = {
    "attended": "depart_after_attend",
    "missed": "missed",
    "cancelled": "cancelled",
}


class AppointmentService:
    def __init__(
        self,
        repository: Optional[AppointmentReminderRepository] = None,
        authorization_service: Any = None,
        user_profile_service: Any = None,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._repository = repository or AppointmentReminderRepository()
        self._authorization_service = authorization_service
        self._user_profile_service = user_profile_service
        # 可注入是為了讓測試固定在某個時刻——「已經過了」「門診當天」「當日結束」
        # 都取決於現在幾點。
        self._clock = clock

    # ── 查詢 ──────────────────────────────────────────────────────────

    async def get(self, reminder_id: str) -> AppointmentReminder:
        """取得單筆提醒，供呼叫端在授權判定之前得知就診者是誰。"""
        reminder = await self._repository.get_by_id(reminder_id)
        if reminder is None:
            raise AppointmentError.of(404, "not_found")
        return reminder

    async def list_for_user(
        self, user_id: str, *, include_past: bool = False
    ) -> List[AppointmentReminder]:
        """`include_past=False` 只回傳「當日結束」還沒到的：今天（含今天稍早已經
        看完的）與未來的門診。界線與排程器標記 missed 是同一條。"""
        day_end_after = None if include_past else self._clock()
        return await self._repository.list_by_user(user_id, day_end_after=day_end_after)

    async def display_name(self, user_id: str) -> Optional[str]:
        if not self._user_profile_service or not user_id:
            return None
        try:
            profile = await self._user_profile_service.get_user_profile(user_id)
        except Exception:
            logger.warning("[AppointmentService] 無法取得 %s 的顯示名稱", user_id)
            return None
        if isinstance(profile, dict) and profile.get("name"):
            return profile["name"]
        return None

    # ── 建立／修改／刪除 ──────────────────────────────────────────────

    async def create(
        self, creator_user_id: str, request: CreateAppointmentReminderRequest
    ) -> AppointmentReminder:
        """授權由呼叫端（router）判定：為他人建立需要對就診者的 GENERAL 寫入權。

        `clinic_time` 刻意不在這裡驗證——那是整間院所的營業時段，不是各科的門診表，
        權威是使用者手上的掛號單。`facility_id` 為 null 也照常建立。
        """
        now = self._clock()
        appointment_at = _require_aware_minute(request.appointment_at)
        if appointment_at <= now:
            raise AppointmentError(400, IN_THE_PAST_DETAIL)

        text_fields = {
            field: _clean_text(field, getattr(request, field))
            for field in TEXT_FIELD_LIMITS
        }
        await self._ensure_not_duplicate(
            user_id=request.user_id,
            appointment_at=appointment_at,
            facility_id=text_fields["facility_id"],
            hospital_name=text_fields["hospital_name"],
            department=text_fields["department"],
        )
        reminder = AppointmentReminder(
            user_id=request.user_id,
            creator_user_id=creator_user_id,
            **_schedule_fields(appointment_at),
            **text_fields,
            created_at=now,
            updated_at=now,
        )
        saved = await self._repository.create(reminder)
        logger.info(
            "[AppointmentService] 已建立掛號提醒 %s: creator=%s, target=%s",
            saved.id,
            creator_user_id,
            request.user_id,
        )
        return saved

    async def update(
        self, reminder_id: str, request: UpdateAppointmentReminderRequest
    ) -> AppointmentReminder:
        """部分更新。授權由呼叫端判定，對象是**就診者**，不是建立者。

        `exclude_unset`，不是 `exclude_none`：沒帶的 key 不動，帶了 null 的清空。
        """
        current = await self.get(reminder_id)
        now = self._clock()
        update_data = request.model_dump(exclude_unset=True)

        # 「明確送 null」對每個欄位都成立了，所以要自己界定哪些 null 有意義。
        # hospital_name／department／appointment_at 被寫成 null 的話，推播文案
        # 與排程器都會壞掉，寧可在這裡擋成 400。
        illegal_nulls = sorted(
            key
            for key, value in update_data.items()
            if value is None and key not in UpdateAppointmentReminderRequest.NULLABLE_FIELDS
        )
        if illegal_nulls:
            fields = "、".join(
                f"{TEXT_FIELD_LABELS.get(key, key)}（{key}）" for key in illegal_nulls
            )
            raise AppointmentError(400, f"以下欄位不接受空值：{fields}")

        set_doc: dict = {}
        for field in TEXT_FIELD_LIMITS:
            if field in update_data:
                set_doc[field] = _clean_text(field, update_data[field])
        if "enabled" in update_data:
            set_doc["enabled"] = update_data["enabled"]

        if "appointment_at" in update_data:
            new_at = _require_aware_minute(update_data["appointment_at"])
            # 比的是瞬間，不是字串：同一刻換一個 offset 送來不是改時間，
            # 只會更新顯示用的 offset。把原值重送一次也不是改動。
            if new_at != current.appointment_at:
                if current.status == "attended":
                    raise AppointmentError(409, RESCHEDULE_ATTENDED_DETAIL)
                if new_at <= now:
                    raise AppointmentError(400, IN_THE_PAST_DETAIL)
                set_doc.update(_RESET_ON_RESCHEDULE)
            set_doc.update(_schedule_fields(new_at))

        if not set_doc:
            return current

        # 只在「是哪一次門診」的欄位有變動時才檢查：只改備註的請求不該因為
        # 另一筆提醒而失敗。檢查對象是改完之後的樣子，並排除自己。
        if {"appointment_at", "facility_id", "hospital_name", "department"} & set_doc.keys():
            await self._ensure_not_duplicate(
                user_id=current.user_id,
                appointment_at=set_doc.get("appointment_at", current.appointment_at),
                facility_id=set_doc.get("facility_id", current.facility_id),
                hospital_name=set_doc.get("hospital_name", current.hospital_name),
                department=set_doc.get("department", current.department),
                exclude_id=reminder_id,
            )

        set_doc["updated_at"] = now
        updated = await self._repository.update_fields(reminder_id, set_doc)
        if updated is None:
            # 讀到之後、寫入之前被刪掉了。
            raise AppointmentError.of(404, "not_found")
        return updated

    async def _ensure_not_duplicate(
        self,
        *,
        user_id: str,
        appointment_at: datetime,
        facility_id: Optional[str],
        hospital_name: str,
        department: str,
        exclude_id: Optional[str] = None,
    ) -> None:
        """同一位就診者、同一時間、同醫院、同科別，只能有一筆提醒。

        重複的代價不只是列表多一張：每一筆都會各自推 T-1h、T+0、T+30，家屬會
        收到兩倍的推播，其中一筆按了到診，另一筆仍會在 T+30 發出「尚未到診」。

        `cancelled` 不算：那一筆已經不會推播了。其餘狀態都算——同一刻已經有一筆
        回報到診的同一次門診，再建一筆就是重複。

        同一時間但不同醫院或不同科別**不擋**：同一家醫院上午兩個科同時報到是
        常見的，那是前端提示「時間撞在一起」的範圍，不是後端的錯誤。

        這是應用層的檢查，不是唯一索引：醫院的比法是「有 id 比 id、沒有才比
        院名」，唯一索引表達不了。兩個請求在同一瞬間送出同一筆時仍可能都通過；
        前端送出期間會停用按鈕，實務上只剩兩個人同時替同一位長輩建同一筆這種
        極端情況。
        """
        others = await self._repository.list_by_user_at(user_id, appointment_at)
        for other in others:
            if other.id == exclude_id or other.status == "cancelled":
                continue
            if _is_same_appointment(
                facility_id=facility_id,
                hospital_name=hospital_name,
                department=department,
                other=other,
            ):
                raise AppointmentError(409, DUPLICATE_DETAIL)

    async def delete(self, reminder_id: str) -> bool:
        await self.get(reminder_id)
        return await self._repository.delete(reminder_id)

    # ── 出發／到診 ────────────────────────────────────────────────────

    async def _authorize_report(self, operator_id: str, patient_id: str) -> None:
        """本人一定可以；對就診者有 GENERAL 寫入權的家屬也可以代按（已拍板）。"""
        if self._authorization_service is None:
            # 沒有注入授權服務時只放行本人——少了判定就當成沒有權限，不是全部放行。
            if operator_id != patient_id:
                raise AppointmentError.of(403, "forbidden_report")
            return
        try:
            await self._authorization_service.authorize(
                operator_id, patient_id, "GENERAL", "WRITE"
            )
        except HTTPException as exc:
            if exc.status_code == 403:
                raise AppointmentError.of(403, "forbidden_report") from exc
            raise

    async def _load_for_report(self, reminder_id: str, operator_id: str) -> AppointmentReminder:
        reminder = await self.get(reminder_id)
        await self._authorize_report(operator_id, reminder.user_id)
        if self._clock() < actionable_from(
            reminder.appointment_at, reminder.appointment_utc_offset_minutes
        ):
            raise AppointmentError.of(409, "too_early")
        return reminder

    async def _resolve_conflict(
        self, reminder_id: str, *, idempotent_status: str
    ) -> AppointmentReminder:
        """原子轉移沒有命中：重讀現況，是目標狀態就冪等回傳，否則回衝突。

        本人與家屬收到的是同一組按鈕，兩個人各按一次是常態而不是例外——第二下
        不該看到錯誤訊息。
        """
        current = await self._repository.get_by_id(reminder_id)
        if current is None:
            raise AppointmentError.of(404, "not_found")
        if current.status == idempotent_status:
            return current
        code = _CONFLICT_CODE_BY_STATUS.get(current.status, "cancelled")
        raise AppointmentError.of(409, code)

    async def depart(self, reminder_id: str, operator_id: str) -> AppointmentReminder:
        """scheduled → departed。從 attended／missed／cancelled 一律不接受。"""
        await self._load_for_report(reminder_id, operator_id)
        updated = await self._repository.mark_departed(
            reminder_id, by_user_id=operator_id, at=self._clock()
        )
        if updated is not None:
            return updated
        return await self._resolve_conflict(reminder_id, idempotent_status="departed")

    async def attend(self, reminder_id: str, operator_id: str) -> AppointmentReminder:
        """scheduled／departed → attended（使用者可能沒按出發就直接到了）。

        寫入 attended 之後，三個推播階段的查詢與搶佔都挑不到這筆（它們只挑
        scheduled／departed），T+0、T+30 以及還沒發出的本人與家屬推播自然全部停下。
        """
        await self._load_for_report(reminder_id, operator_id)
        updated = await self._repository.mark_attended(
            reminder_id, by_user_id=operator_id, at=self._clock()
        )
        if updated is not None:
            return updated
        return await self._resolve_conflict(reminder_id, idempotent_status="attended")
