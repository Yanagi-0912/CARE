"""掛號提醒的業務邏輯：建立、修改、刪除、查詢，以及「我已出發／我已到診／取消」。

CRUD 的授權由 router 經 FamilyAuthorizationService 判定，與用藥提醒同一條慣例。
出發／到診例外，授權放在這裡：這兩個動作有兩個入口——LIFF 的 POST 端點與 LINE
卡片上的 postback——兩邊必須走同一個判定，不能一邊查了、一邊忘了查。取消與它們
共用同一個判定（`_authorize_write`）。
"""

import base64
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, List, Optional, Tuple

from fastapi import HTTPException

from app.i18n import t
from app.models.appointment import (
    NULLABLE_TEXT_FIELDS,
    REQUIRED_TEXT_FIELDS,
    TEXT_FIELD_LABELS,
    TEXT_FIELD_LIMITS,
    AppointmentListScope,
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
RESCHEDULE_CANCELLED_DETAIL = "已經取消的掛號不能改時間；如果要改期，請新增一筆掛號提醒。"
DUPLICATE_DETAIL = "這個時間已經有一筆掛號提醒，同一個時間只能有一筆。"
FORBIDDEN_CANCEL_DETAIL = "您沒有權限替這位家人取消門診。"
CANCEL_ATTENDED_DETAIL = "已經回報到診的門診不能取消。"
CANCEL_DAY_ENDED_DETAIL = "這個門診的當天已經結束，不需要取消。"
INVALID_CURSOR_DETAIL = "分頁位置無效，請重新整理頁面後再試一次。"


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

# 出發／到診失敗時，依重讀到的現況決定回哪一則。scheduled／departed 會落到這裡，
# 只可能是當日已經結束、排程器還沒標記 missed——轉移的條件擋掉的正是那段空窗，
# 對使用者來說那就是錯過了。
_CONFLICT_CODE_BY_STATUS = {
    "scheduled": "missed",
    "departed": "missed",
    "attended": "depart_after_attend",
    "missed": "missed",
    "cancelled": "cancelled",
}

# 能改門診時間的狀態。到診與取消都是終局：到診改時間會洗掉到診紀錄；取消改時間
# 會把「這次門診後來取消了」的歷史改寫掉，改期的正確做法是再掛一筆。
_RESCHEDULABLE_STATUSES = ("scheduled", "departed", "missed")
_RESCHEDULE_CONFLICT_DETAIL = {
    "attended": RESCHEDULE_ATTENDED_DETAIL,
    "cancelled": RESCHEDULE_CANCELLED_DETAIL,
}


def _encode_cursor(reminder: AppointmentReminder) -> str:
    """分頁位置：這一頁最後一筆的（門診時間, id）。

    以位置而不是偏移量分頁：翻頁期間有門診跨過當日結束、被取消或被刪除時，偏移量
    會讓下一頁重複或漏掉，位置不會。id 決定同一時間多筆（例如取消後在原時間重掛）
    的先後。內容不是祕密——翻得到這一頁的人本來就看得到——編碼只是讓前端把它當成
    不透明字串，不去解析它。
    """
    raw = json.dumps(
        {"at": reminder.appointment_at.isoformat(), "id": reminder.id},
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> Tuple[datetime, str]:
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        at = datetime.fromisoformat(data["at"])
        last_id = data["id"]
    except (ValueError, KeyError, TypeError):
        raise AppointmentError(400, INVALID_CURSOR_DETAIL) from None
    if at.tzinfo is None or not isinstance(last_id, str):
        raise AppointmentError(400, INVALID_CURSOR_DETAIL)
    return at.astimezone(timezone.utc), last_id


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

    async def list_scope(
        self,
        user_id: str,
        scope: AppointmentListScope,
        *,
        limit: int,
        cursor: Optional[str] = None,
    ) -> Tuple[List[AppointmentReminder], Optional[str], int]:
        """「即將到來」或「過去」的一頁：（這一頁, 下一頁的 cursor, 整個 scope 的筆數）。

        upcoming 不分頁（未來的門診本來就少），`limit`／`cursor` 不適用。past 多取
        一筆來判斷有沒有下一頁，免得前端在最後多打一次空頁。清單與總筆數用同一個
        「現在」，兩者不會因為中間跨過某一筆的當日結束而對不上。
        """
        now = self._clock()
        if scope == "upcoming":
            items = await self._repository.list_scope(user_id, "upcoming", now)
            return items, None, len(items)

        older_than = _decode_cursor(cursor) if cursor else None
        rows = await self._repository.list_scope(
            user_id, "past", now, limit=limit + 1, older_than=older_than
        )
        total_count = await self._repository.count_scope(user_id, "past", now)
        items = rows[:limit]
        next_cursor = _encode_cursor(items[-1]) if len(rows) > limit else None
        return items, next_cursor, total_count

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
            user_id=request.user_id, appointment_at=appointment_at
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

        rescheduling = False
        if "appointment_at" in update_data:
            new_at = _require_aware_minute(update_data["appointment_at"])
            # 比的是瞬間，不是字串：同一刻換一個 offset 送來不是改時間，
            # 只會更新顯示用的 offset。把原值重送一次也不是改動。
            if new_at != current.appointment_at:
                if current.status in _RESCHEDULE_CONFLICT_DETAIL:
                    raise AppointmentError(409, _RESCHEDULE_CONFLICT_DETAIL[current.status])
                if new_at <= now:
                    raise AppointmentError(400, IN_THE_PAST_DETAIL)
                # 只在真的改時間時檢查：編輯表單會整份回送原本的 appointment_at，
                # 舊規則下建立的同一時間兩筆不能因此連備註都改不了。
                await self._ensure_not_duplicate(
                    user_id=current.user_id, appointment_at=new_at, exclude_id=reminder_id
                )
                set_doc.update(_RESET_ON_RESCHEDULE)
                rescheduling = True
            set_doc.update(_schedule_fields(new_at))

        if not set_doc:
            return current

        set_doc["updated_at"] = now
        # 改時間是條件式寫入：上面檢查的 current 是讀到的那一刻，寫入前可能有人剛
        # 回報到診或取消。無條件寫入會套用 _RESET_ON_RESCHEDULE，把狀態改回
        # scheduled、洗掉那筆紀錄，家屬還會再收到提醒。
        updated = await self._repository.update_fields(
            reminder_id,
            set_doc,
            only_if_status_in=_RESCHEDULABLE_STATUSES if rescheduling else None,
        )
        if updated is None:
            latest = await self.get(reminder_id)  # 讀到之後被刪掉了 → 404
            # 文件還在卻沒寫入：狀態在讀取之後變成了到診或取消。兩者都是終局，
            # 重讀到的就是讓條件落空的那個狀態。
            raise AppointmentError(409, _RESCHEDULE_CONFLICT_DETAIL[latest.status])
        return updated

    async def _ensure_not_duplicate(
        self,
        *,
        user_id: str,
        appointment_at: datetime,
        exclude_id: Optional[str] = None,
    ) -> None:
        """同一位就診者、同一個門診瞬間，只能有一筆沒有取消的提醒——不論醫院或
        科別是否相同（2026-09-10 追加）。

        重複的代價不只是列表多一張：每一筆都會各自推 T-1h、T+0、T+30，家屬會
        收到兩倍的推播，其中一筆按了到診，另一筆仍會在 T+30 發出「尚未到診」。

        `cancelled` 不算：那一筆已經不會推播了。取消了 9/15 09:30 那一筆，再建一筆
        9/15 09:30 要能建。其餘狀態都算。

        這是應用層的檢查，不是唯一索引。規則收斂成「同一瞬間」之後，部分唯一索引
        其實表達得了，但舊規則（同醫院、同科別才擋）下可能已經存在同一瞬間的兩筆，
        建索引會失敗。兩個請求在同一瞬間送出時仍可能都通過；前端已經先擋，這裡是
        清單過期時（家人剛在另一支手機建了同一時間的掛號）的最後一道防線。
        """
        others = await self._repository.list_by_user_at(user_id, appointment_at)
        if any(other.id != exclude_id and other.status != "cancelled" for other in others):
            raise AppointmentError(409, DUPLICATE_DETAIL)

    async def delete(self, reminder_id: str) -> bool:
        await self.get(reminder_id)
        return await self._repository.delete(reminder_id)

    async def delete_past(self, user_id: str) -> int:
        """刪除某位就診者「過去」的全部提醒，回傳實際刪除的筆數。

        「過去」與 `list_scope(..., "past")` 是同一份判定（`past_filter`），整個操作
        用同一個「現在」。授權——只有本人——由呼叫端（router）判定。
        """
        deleted = await self._repository.delete_past(user_id, self._clock())
        logger.info(
            "[AppointmentService] 已刪除 %s 的 %d 筆過去的掛號提醒", user_id, deleted
        )
        return deleted

    # ── 出發／到診／取消 ──────────────────────────────────────────────

    async def _authorize_write(
        self, operator_id: str, patient_id: str, forbidden: AppointmentError
    ) -> None:
        """本人一定可以；對就診者有 GENERAL 寫入權的家屬也可以代按（已拍板）。

        `has_legacy_equivalent=False`：掛號整個功能都是 RBAC 導入之後才有的，沒有要
        保留的 legacy 行為。影子模式下只有讀取權的 MEMBER 也不能動（已拍板）——LINE
        卡片的按鈕走的也是這裡。
        """
        if self._authorization_service is None:
            # 沒有注入授權服務時只放行本人——少了判定就當成沒有權限，不是全部放行。
            if operator_id != patient_id:
                raise forbidden
            return
        try:
            await self._authorization_service.authorize(
                operator_id, patient_id, "GENERAL", "WRITE", has_legacy_equivalent=False
            )
        except HTTPException as exc:
            if exc.status_code == 403:
                raise forbidden from exc
            raise

    async def _load_for_report(self, reminder_id: str, operator_id: str) -> AppointmentReminder:
        reminder = await self.get(reminder_id)
        await self._authorize_write(
            operator_id, reminder.user_id, AppointmentError.of(403, "forbidden_report")
        )
        # 取消是唯一可能在門診當天之前就成立的終局。先說「已取消」，不說「門診當天
        # 才能回報」——後者會讓人以為當天還按得下去。
        if reminder.status == "cancelled":
            raise AppointmentError.of(409, "cancelled")
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
        raise AppointmentError.of(409, _CONFLICT_CODE_BY_STATUS[current.status])

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

    async def cancel(self, reminder_id: str, operator_id: str) -> AppointmentReminder:
        """scheduled／departed → cancelled：保留紀錄、停止推播。

        取消者只取自 token（`operator_id`）。不限門診當天——取消通常發生在門診前
        幾天。不發任何通知（已拍板）：三個推播階段只挑 scheduled／departed，寫入
        cancelled 之後尚未發出的推播全部停下。排程器已經搶下、正在送的那一則仍會
        送達，那張卡片上的按鈕之後會回「已取消」。

        只有 LIFF 一個入口，錯誤文案因此不走 i18n，與改時間的 409 相同。
        """
        reminder = await self.get(reminder_id)
        await self._authorize_write(
            operator_id, reminder.user_id, AppointmentError(403, FORBIDDEN_CANCEL_DETAIL)
        )
        updated = await self._repository.mark_cancelled(
            reminder_id, by_user_id=operator_id, at=self._clock()
        )
        if updated is not None:
            logger.info(
                "[AppointmentService] 已取消掛號提醒 %s: by=%s", reminder_id, operator_id
            )
            return updated

        current = await self._repository.get_by_id(reminder_id)
        if current is None:
            raise AppointmentError.of(404, "not_found")
        if current.status == "cancelled":
            return current  # 冪等：保留第一位取消者
        if current.status == "attended":
            raise AppointmentError(409, CANCEL_ATTENDED_DETAIL)
        # missed，或當日已結束、排程器還沒標記 missed 的 scheduled／departed
        raise AppointmentError(409, CANCEL_DAY_ENDED_DETAIL)
