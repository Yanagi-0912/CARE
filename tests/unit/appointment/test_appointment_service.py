from datetime import datetime

import pytest

from app.i18n import t
from app.models.appointment import (
    CreateAppointmentReminderRequest,
    UpdateAppointmentReminderRequest,
)
from app.repositories.appointment_repository import AppointmentReminderRepository
from app.services.appointment.appointment_service import (
    CANCEL_ATTENDED_DETAIL,
    CANCEL_DAY_ENDED_DETAIL,
    DUPLICATE_DETAIL,
    FORBIDDEN_CANCEL_DETAIL,
    IN_THE_PAST_DETAIL,
    INVALID_CURSOR_DETAIL,
    RESCHEDULE_ATTENDED_DETAIL,
    RESCHEDULE_CANCELLED_DETAIL,
    TIMEZONE_REQUIRED_DETAIL,
    AppointmentError,
    AppointmentService,
)

from .support import (
    DAUGHTER,
    PATIENT,
    STRANGER,
    TPE,
    FakeAuthz,
    FakeCollection,
    make_appointment,
)

THE_DAY_BEFORE = datetime(2026, 9, 14, 10, 0, tzinfo=TPE)
DAY_OF = datetime(2026, 9, 15, 8, 40, tzinfo=TPE)
# 9/15 09:30 門診的當日結束是 9/16 00:00。排程器下一次標記 missed 之前，狀態仍是
# scheduled／departed——這一刻就落在那段空窗裡。
AFTER_DAY_END = datetime(2026, 9, 16, 0, 5, tzinfo=TPE)
MISSED_MESSAGE = "這個門診的當天已經結束，無法再回報出發或到診。"
CANCELLED_MESSAGE = "這筆掛號提醒已經取消，無法回報出發或到診。"


class Clock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture()
def clock():
    return Clock(THE_DAY_BEFORE)


@pytest.fixture()
def repo():
    col = FakeCollection()
    return AppointmentReminderRepository(collection_provider=lambda: col)


@pytest.fixture()
def service(repo, clock):
    return AppointmentService(
        repository=repo,
        authorization_service=FakeAuthz(writers=[DAUGHTER]),
        clock=clock,
    )


def create_request(**overrides) -> CreateAppointmentReminderRequest:
    payload = {
        "user_id": PATIENT,
        "appointment_at": "2026-09-15T09:30:00+08:00",
        "facility_id": "abc123",
        "hospital_name": "台大醫院",
        "hospital_address": "臺北市中正區中山南路7號",
        "hospital_phone": "0223123456",
        "department": "心臟內科",
        "doctor_name": "王大明",
        "serial_number": "23",
        "note": None,
    }
    payload.update(overrides)
    return CreateAppointmentReminderRequest(**payload)


def update(**fields) -> UpdateAppointmentReminderRequest:
    return UpdateAppointmentReminderRequest.model_validate(fields)


async def expect_error(coro, status: int, detail: str):
    with pytest.raises(AppointmentError) as exc_info:
        await coro
    assert exc_info.value.status_code == status
    assert exc_info.value.detail == detail
    return exc_info.value


# ── 建立 ──────────────────────────────────────────────────────────────


async def test_create_requires_an_offset(service):
    await expect_error(
        service.create(PATIENT, create_request(appointment_at="2026-09-15T09:30:00")),
        400,
        TIMEZONE_REQUIRED_DETAIL,
    )


async def test_create_rejects_a_time_that_has_passed(service):
    await expect_error(
        service.create(PATIENT, create_request(appointment_at="2026-09-14T09:59:00+08:00")),
        400,
        IN_THE_PAST_DETAIL,
    )


async def test_create_truncates_to_the_minute_and_keeps_the_offset(service):
    saved = await service.create(
        DAUGHTER, create_request(appointment_at="2026-09-15T09:30:45.123+08:00")
    )
    assert saved.local_appointment_at.isoformat() == "2026-09-15T09:30:00+08:00"
    assert saved.creator_user_id == DAUGHTER
    assert saved.status == "scheduled"


async def test_create_accepts_a_null_facility_id(service):
    saved = await service.create(PATIENT, create_request(facility_id=None))
    assert saved.facility_id is None


async def test_create_accepts_a_time_outside_any_clinic_slot(service):
    """clinic_time 只能拿來提示，不能拿來限制：權威是使用者手上的掛號單。"""
    saved = await service.create(
        PATIENT, create_request(appointment_at="2026-09-15T09:47:00+08:00")
    )
    assert saved.local_appointment_at.strftime("%H:%M") == "09:47"


async def test_blank_optional_text_becomes_null_and_text_is_trimmed(service):
    saved = await service.create(
        PATIENT, create_request(doctor_name="   ", hospital_name="  台大醫院 ")
    )
    assert saved.doctor_name is None
    assert saved.hospital_name == "台大醫院"


async def test_blank_required_text_is_rejected(service):
    await expect_error(
        service.create(PATIENT, create_request(hospital_name="  ")),
        400,
        "醫院名稱不能是空的。",
    )
    await expect_error(
        service.create(PATIENT, create_request(department="")), 400, "科別不能是空的。"
    )


async def test_overlong_text_is_rejected(service):
    await expect_error(
        service.create(PATIENT, create_request(note="字" * 501)),
        400,
        "備註最多 500 個字。",
    )


# ── 重複的掛號：同一瞬間只能有一筆沒有取消的 ─────────────────────────


async def test_same_time_hospital_and_department_is_a_duplicate(service):
    """家屬與本人各建了一次同一張掛號單：第二筆要擋下來，否則每則推播都會發兩次。"""
    await service.create(PATIENT, create_request())
    await expect_error(service.create(DAUGHTER, create_request()), 409, DUPLICATE_DETAIL)


@pytest.mark.parametrize(
    "other",
    [
        {"department": "眼科"},
        {"facility_id": "branch-b", "hospital_name": "仁愛診所"},
        {"facility_id": None, "hospital_name": "馬偕醫院", "department": "骨科"},
    ],
    ids=["另一科", "另一家院所", "院所與科別都不同"],
)
async def test_same_instant_is_a_duplicate_whatever_the_hospital_or_department(
    service, other
):
    """2026-09-10 追加：同一位就診者、同一瞬間只能有一筆，不論醫院或科別。"""
    await service.create(PATIENT, create_request())
    await expect_error(
        service.create(PATIENT, create_request(**other)), 409, DUPLICATE_DETAIL
    )


async def test_different_time_is_not_a_duplicate(service):
    await service.create(PATIENT, create_request())
    await service.create(PATIENT, create_request(appointment_at="2026-09-15T14:00:00+08:00"))


async def test_the_same_instant_for_another_patient_is_not_a_duplicate(service):
    await service.create(PATIENT, create_request())
    await service.create(DAUGHTER, create_request(user_id=DAUGHTER))


async def test_a_cancelled_reminder_frees_its_time_slot(service):
    """取消了 9/15 09:30 那一筆，再建一筆 9/15 09:30 要能建。"""
    first = await service.create(PATIENT, create_request())
    await service.cancel(first.id, PATIENT)
    again = await service.create(PATIENT, create_request())
    assert again.id != first.id


async def test_moving_one_appointment_onto_another_is_rejected(service):
    await service.create(PATIENT, create_request())
    later = await service.create(
        PATIENT,
        create_request(appointment_at="2026-09-15T14:00:00+08:00", department="眼科"),
    )
    await expect_error(
        service.update(later.id, update(appointment_at="2026-09-15T09:30:00+08:00")),
        409,
        DUPLICATE_DETAIL,
    )


async def test_editing_a_reminder_never_collides_with_itself(service):
    saved = await service.create(PATIENT, create_request())
    assert (await service.update(saved.id, update(note="帶健保卡"))).note == "帶健保卡"
    resent = await service.update(
        saved.id, update(appointment_at="2026-09-15T09:30:00+08:00", department="心臟內科")
    )
    assert resent.id == saved.id


async def test_rows_that_predate_the_rule_stay_editable(service, repo):
    """舊規則下建立的同一時間兩筆（不同科）不溯及既往：編輯表單會整份回送原本的
    appointment_at，只改備註的 PUT 不能因為另一筆而失敗。"""
    first = await repo.create(make_appointment(department="心臟內科"))
    await repo.create(make_appointment(department="眼科"))
    updated = await service.update(
        first.id, update(appointment_at="2026-09-15T09:30:00+08:00", note="帶健保卡")
    )
    assert updated.note == "帶健保卡"


# ── 修改：exclude_unset ───────────────────────────────────────────────


async def test_absent_key_is_untouched_and_explicit_null_clears(service):
    saved = await service.create(PATIENT, create_request())
    updated = await service.update(saved.id, update(doctor_name=None))
    assert updated.doctor_name is None
    assert updated.serial_number == "23"  # 沒帶 → 不動
    assert updated.hospital_phone == "0223123456"


@pytest.mark.parametrize(
    "field",
    ["facility_id", "hospital_address", "hospital_phone", "doctor_name", "serial_number", "note"],
)
async def test_every_nullable_field_can_be_cleared(service, field):
    saved = await service.create(PATIENT, create_request(note="帶健保卡"))
    updated = await service.update(saved.id, update(**{field: None}))
    assert getattr(updated, field) is None


async def test_null_on_a_required_field_is_a_400(service):
    saved = await service.create(PATIENT, create_request())
    await expect_error(
        service.update(saved.id, update(hospital_name=None, appointment_at=None)),
        400,
        "以下欄位不接受空值：門診時間（appointment_at）、醫院名稱（hospital_name）",
    )


async def test_empty_body_changes_nothing(service):
    saved = await service.create(PATIENT, create_request())
    assert await service.update(saved.id, update()) == saved


async def test_rescheduling_resets_the_state_machine(service, clock):
    saved = await service.create(PATIENT, create_request())
    clock.now = DAY_OF
    await service.depart(saved.id, PATIENT)

    rescheduled = await service.update(
        saved.id, update(appointment_at="2026-09-22T14:00:00+08:00")
    )
    assert rescheduled.status == "scheduled"
    assert rescheduled.departed_at is None
    assert rescheduled.departed_by_user_id is None
    assert rescheduled.pre_reminder_sent is False
    assert rescheduled.local_appointment_at.isoformat() == "2026-09-22T14:00:00+08:00"


async def test_resending_the_same_instant_is_not_a_reschedule(service, clock):
    saved = await service.create(PATIENT, create_request())
    clock.now = DAY_OF
    await service.depart(saved.id, PATIENT)
    # 同一刻、換一個 offset：只換顯示用的 offset，狀態不動
    updated = await service.update(
        saved.id, update(appointment_at="2026-09-15T01:30:00+00:00")
    )
    assert updated.status == "departed"
    assert updated.appointment_utc_offset_minutes == 0


async def test_an_attended_appointment_cannot_be_moved(service, clock):
    saved = await service.create(PATIENT, create_request())
    clock.now = DAY_OF
    await service.attend(saved.id, PATIENT)
    await expect_error(
        service.update(saved.id, update(appointment_at="2026-09-22T14:00:00+08:00")),
        409,
        RESCHEDULE_ATTENDED_DETAIL,
    )
    # 但其他欄位仍可修改
    assert (await service.update(saved.id, update(note="拿了處方箋"))).note == "拿了處方箋"


async def test_a_cancelled_appointment_cannot_be_moved(service):
    """取消是終局：改期要新增一筆，舊的那筆留著當「這次門診後來取消了」的紀錄。"""
    saved = await service.create(PATIENT, create_request())
    await service.cancel(saved.id, PATIENT)
    await expect_error(
        service.update(saved.id, update(appointment_at="2026-09-22T14:00:00+08:00")),
        409,
        RESCHEDULE_CANCELLED_DETAIL,
    )
    stored = await service.get(saved.id)
    assert stored.status == "cancelled"
    assert stored.local_appointment_at.isoformat() == "2026-09-15T09:30:00+08:00"
    # 其他欄位仍可修改；整份回送同一個門診時間也不算改時間
    edited = await service.update(
        saved.id, update(appointment_at="2026-09-15T09:30:00+08:00", note="醫院通知停診")
    )
    assert (edited.status, edited.note) == ("cancelled", "醫院通知停診")


async def test_a_missed_appointment_can_still_be_moved(service, repo, clock):
    """missed 維持現狀：時間填錯了所以沒去成，改時間重置回 scheduled。"""
    saved = await service.create(PATIENT, create_request())
    await repo.mark_missed(datetime(2026, 9, 16, 0, 0, tzinfo=TPE))
    clock.now = datetime(2026, 9, 16, 8, 0, tzinfo=TPE)
    moved = await service.update(saved.id, update(appointment_at="2026-09-22T14:00:00+08:00"))
    assert moved.status == "scheduled"


class _SomeoneActsBeforeTheWrite(AppointmentReminderRepository):
    """在服務層讀到現況之後、寫入之前，插進另一個人的動作（只插一次）。"""

    def __init__(self, collection, act):
        super().__init__(collection_provider=lambda: collection)
        self._act = act

    async def update_fields(self, reminder_id, set_doc, **kwargs):
        act, self._act = self._act, None
        if act is not None:
            await act(self, reminder_id)
        return await super().update_fields(reminder_id, set_doc, **kwargs)


@pytest.mark.parametrize(
    "act,detail,status",
    [
        (
            lambda repo, rid: repo.mark_attended(rid, by_user_id=DAUGHTER, at=DAY_OF),
            RESCHEDULE_ATTENDED_DETAIL,
            "attended",
        ),
        (
            lambda repo, rid: repo.mark_cancelled(rid, by_user_id=DAUGHTER, at=DAY_OF),
            RESCHEDULE_CANCELLED_DETAIL,
            "cancelled",
        ),
    ],
    ids=["剛回報到診", "剛取消"],
)
async def test_a_reschedule_racing_a_terminal_report_does_not_erase_it(
    clock, act, detail, status
):
    """洞 1：讀完之後、寫入之前有人回報到診（或取消）。無條件的 $set 會把狀態改回
    scheduled、清空回報紀錄、重排三個推播——紀錄消失，家屬還會再收到提醒。"""
    repo = _SomeoneActsBeforeTheWrite(FakeCollection(), act)
    service = AppointmentService(repository=repo, authorization_service=FakeAuthz(), clock=clock)
    saved = await repo.create(make_appointment())
    clock.now = DAY_OF

    await expect_error(
        service.update(saved.id, update(appointment_at="2026-09-22T14:00:00+08:00")),
        409,
        detail,
    )
    stored = await repo.get_by_id(saved.id)
    assert stored.status == status
    assert stored.local_appointment_at.isoformat() == "2026-09-15T09:30:00+08:00"
    assert stored.notify_at() == []


async def test_rescheduling_into_the_past_is_rejected(service):
    saved = await service.create(PATIENT, create_request())
    await expect_error(
        service.update(saved.id, update(appointment_at="2026-09-13T09:30:00+08:00")),
        400,
        IN_THE_PAST_DETAIL,
    )


async def test_update_of_an_unknown_id_is_404(service):
    await expect_error(
        service.update("nope", update(note="x")), 404, t("appt.error.not_found", "zh-TW")
    )


# ── 出發／到診 ────────────────────────────────────────────────────────


async def test_family_with_write_permission_can_report_on_behalf(service, clock):
    saved = await service.create(PATIENT, create_request())
    clock.now = DAY_OF
    departed = await service.depart(saved.id, DAUGHTER)
    assert departed.status == "departed"
    assert departed.departed_by_user_id == DAUGHTER
    attended = await service.attend(saved.id, PATIENT)
    assert attended.status == "attended"
    assert attended.attended_by_user_id == PATIENT


async def test_someone_without_write_permission_gets_the_appointment_specific_403(service, clock):
    saved = await service.create(PATIENT, create_request())
    clock.now = DAY_OF
    error = await expect_error(
        service.depart(saved.id, STRANGER), 403, "您沒有權限替這位家人回報出發或到診。"
    )
    assert error.code == "forbidden_report"


async def test_without_an_authorization_service_only_the_patient_may_report(repo, clock):
    service = AppointmentService(repository=repo, clock=clock)
    saved = await service.create(PATIENT, create_request())
    clock.now = DAY_OF
    with pytest.raises(AppointmentError):
        await service.depart(saved.id, DAUGHTER)
    assert (await service.depart(saved.id, PATIENT)).status == "departed"


async def test_reporting_before_the_appointment_day_is_refused(service):
    """手滑按了「我已到診」會取消所有後續推播——那正是這個功能要防止的結果。"""
    saved = await service.create(PATIENT, create_request())
    await expect_error(
        service.attend(saved.id, PATIENT), 409, "門診當天才能回報出發或到診。"
    )


async def test_second_press_is_idempotent_and_keeps_the_first_reporter(service, clock):
    saved = await service.create(PATIENT, create_request())
    clock.now = DAY_OF
    await service.depart(saved.id, DAUGHTER)
    again = await service.depart(saved.id, PATIENT)
    assert again.status == "departed"
    assert again.departed_by_user_id == DAUGHTER

    await service.attend(saved.id, PATIENT)
    assert (await service.attend(saved.id, DAUGHTER)).attended_by_user_id == PATIENT


async def test_attend_is_allowed_without_departing_first(service, clock):
    saved = await service.create(PATIENT, create_request())
    clock.now = DAY_OF
    attended = await service.attend(saved.id, PATIENT)
    assert attended.status == "attended"
    assert attended.departed_at is None


async def test_depart_after_attend_is_a_conflict(service, clock):
    saved = await service.create(PATIENT, create_request())
    clock.now = DAY_OF
    await service.attend(saved.id, PATIENT)
    await expect_error(
        service.depart(saved.id, PATIENT), 409, "已經回報到診了，不需要再回報出發。"
    )


async def test_missed_appointments_accept_neither_button(service, repo, clock):
    saved = await service.create(PATIENT, create_request())
    await repo.mark_missed(datetime(2026, 9, 16, 0, 0, tzinfo=TPE))
    clock.now = datetime(2026, 9, 16, 8, 0, tzinfo=TPE)
    await expect_error(service.depart(saved.id, PATIENT), 409, MISSED_MESSAGE)
    await expect_error(service.attend(saved.id, PATIENT), 409, MISSED_MESSAGE)


@pytest.mark.parametrize("press", ["depart", "attend"])
async def test_nothing_can_be_reported_after_the_day_ends_even_before_missed_is_marked(
    service, clock, press
):
    """洞 2：當日結束後、排程器標記 missed 之前，狀態仍是 scheduled。只看狀態的話，
    隔天凌晨按一下「我已到診」，沒去的門診就變成已到診——那等於補登（已拍板不做）。"""
    saved = await service.create(PATIENT, create_request())
    clock.now = AFTER_DAY_END
    await expect_error(getattr(service, press)(saved.id, PATIENT), 409, MISSED_MESSAGE)
    assert (await service.get(saved.id)).status == "scheduled"


async def test_a_departed_appointment_cannot_be_attended_after_the_day_ends(service, clock):
    saved = await service.create(PATIENT, create_request())
    clock.now = DAY_OF
    await service.depart(saved.id, PATIENT)
    clock.now = AFTER_DAY_END
    await expect_error(service.attend(saved.id, DAUGHTER), 409, MISSED_MESSAGE)
    assert (await service.get(saved.id)).status == "departed"


async def test_localized_error_for_line(service, clock):
    saved = await service.create(PATIENT, create_request())
    clock.now = DAY_OF
    with pytest.raises(AppointmentError) as exc_info:
        await service.depart(saved.id, STRANGER)
    assert exc_info.value.localized("en") == t("appt.error.forbidden_report", "en")


# ── 取消 ──────────────────────────────────────────────────────────────


async def test_cancel_keeps_the_record_and_stops_every_push(service):
    saved = await service.create(PATIENT, create_request())
    cancelled = await service.cancel(saved.id, DAUGHTER)
    assert cancelled.status == "cancelled"
    assert cancelled.cancelled_by_user_id == DAUGHTER
    assert cancelled.cancelled_at == THE_DAY_BEFORE
    assert cancelled.updated_at == THE_DAY_BEFORE
    assert cancelled.notify_at() == []
    # 輸出時與出發／到診相同，帶 appointment_at 的 offset
    assert cancelled.to_response().cancelled_at.isoformat() == "2026-09-14T10:00:00+08:00"
    assert (await service.get(saved.id)).hospital_name == "台大醫院"  # 紀錄還在


async def test_cancel_is_not_limited_to_the_appointment_day(service):
    """取消通常發生在門診前幾天：沒有出發／到診那種「門診當天」的限制。"""
    saved = await service.create(
        PATIENT, create_request(appointment_at="2026-10-20T09:30:00+08:00")
    )
    assert (await service.cancel(saved.id, PATIENT)).status == "cancelled"


async def test_cancel_after_departing_keeps_the_departure(service, clock):
    saved = await service.create(PATIENT, create_request())
    clock.now = DAY_OF
    await service.depart(saved.id, PATIENT)
    cancelled = await service.cancel(saved.id, DAUGHTER)
    assert cancelled.status == "cancelled"
    assert cancelled.departed_by_user_id == PATIENT


async def test_second_cancel_is_idempotent_and_keeps_the_first_canceller(service, clock):
    saved = await service.create(PATIENT, create_request())
    first = await service.cancel(saved.id, DAUGHTER)
    clock.now = DAY_OF
    again = await service.cancel(saved.id, PATIENT)
    assert again == first
    assert again.cancelled_by_user_id == DAUGHTER


async def test_an_attended_appointment_cannot_be_cancelled(service, clock):
    saved = await service.create(PATIENT, create_request())
    clock.now = DAY_OF
    await service.attend(saved.id, PATIENT)
    await expect_error(service.cancel(saved.id, PATIENT), 409, CANCEL_ATTENDED_DETAIL)


async def test_a_missed_appointment_cannot_be_cancelled(service, repo, clock):
    saved = await service.create(PATIENT, create_request())
    await repo.mark_missed(datetime(2026, 9, 16, 0, 0, tzinfo=TPE))
    clock.now = datetime(2026, 9, 16, 8, 0, tzinfo=TPE)
    await expect_error(service.cancel(saved.id, PATIENT), 409, CANCEL_DAY_ENDED_DETAIL)


async def test_cancel_after_the_day_ends_is_refused_even_before_missed_is_marked(
    service, clock
):
    """只看狀態會讓人取消一個已經結束的門診：那段空窗裡狀態還是 scheduled。"""
    saved = await service.create(PATIENT, create_request())
    clock.now = AFTER_DAY_END
    await expect_error(service.cancel(saved.id, PATIENT), 409, CANCEL_DAY_ENDED_DETAIL)
    assert (await service.get(saved.id)).status == "scheduled"


async def test_cancel_needs_write_permission(service):
    saved = await service.create(PATIENT, create_request())
    await expect_error(service.cancel(saved.id, STRANGER), 403, FORBIDDEN_CANCEL_DETAIL)
    assert (await service.get(saved.id)).status == "scheduled"


async def test_cancel_of_an_unknown_id_is_404(service):
    await expect_error(
        service.cancel("nope", PATIENT), 404, t("appt.error.not_found", "zh-TW")
    )


async def test_a_cancelled_appointment_accepts_neither_button(service, clock):
    """手機上留著取消前收到的 T-1h 卡片，按鈕仍按得下去：要說明已取消，不能寫入。"""
    saved = await service.create(PATIENT, create_request())
    await service.cancel(saved.id, PATIENT)
    # 門診前一天就按：說「已取消」，不說「門診當天才能回報」
    await expect_error(service.depart(saved.id, PATIENT), 409, CANCELLED_MESSAGE)
    clock.now = DAY_OF
    await expect_error(service.depart(saved.id, DAUGHTER), 409, CANCELLED_MESSAGE)
    await expect_error(service.attend(saved.id, DAUGHTER), 409, CANCELLED_MESSAGE)
    # 當日結束後再按，仍然說明已取消，不是「當天已經結束」
    clock.now = AFTER_DAY_END
    await expect_error(service.attend(saved.id, PATIENT), 409, CANCELLED_MESSAGE)


# ── 列表 ──────────────────────────────────────────────────────────────


async def test_list_hides_past_days_by_default(service, repo, clock):
    past = await service.create(PATIENT, create_request(appointment_at="2026-09-14T11:00:00+08:00"))
    future = await service.create(PATIENT, create_request())
    clock.now = datetime(2026, 9, 15, 8, 0, tzinfo=TPE)

    assert [r.id for r in await service.list_for_user(PATIENT)] == [future.id]
    assert [r.id for r in await service.list_for_user(PATIENT, include_past=True)] == [
        past.id,
        future.id,
    ]


async def test_the_legacy_list_is_unchanged_by_cancelling(service):
    """不帶 scope 的舊格式照舊只看當日結束：取消了的未來門診仍在預設清單裡。"""
    saved = await service.create(PATIENT, create_request())
    await service.cancel(saved.id, PATIENT)
    assert [r.id for r in await service.list_for_user(PATIENT)] == [saved.id]


def _tpe(month: int, day: int, hour: int = 9, minute: int = 0) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=TPE)


async def seed_history(repo) -> dict:
    """現在是 9/15 08:00 時的一份歷史。"""
    return {
        "attended_0910": await repo.create(make_appointment(at=_tpe(9, 10), status="attended")),
        "missed_0912": await repo.create(make_appointment(at=_tpe(9, 12), status="missed")),
        # 當日已結束、排程器還沒標記 missed
        "ended_0914": await repo.create(make_appointment(at=_tpe(9, 14, 11))),
        "today_0915": await repo.create(make_appointment(at=_tpe(9, 15, 9, 30))),
        "later_0920": await repo.create(make_appointment(at=_tpe(9, 20))),
        # 下個月的門診取消了，也歸在過去
        "cancelled_1001": await repo.create(
            make_appointment(at=_tpe(10, 1), status="cancelled")
        ),
        "someone_else": await repo.create(
            make_appointment(at=_tpe(9, 10), user_id=DAUGHTER, creator_user_id=DAUGHTER)
        ),
    }


async def test_scopes_split_upcoming_and_past(service, repo, clock):
    rows = await seed_history(repo)
    clock.now = _tpe(9, 15, 8)

    upcoming, cursor, total = await service.list_scope(PATIENT, "upcoming", limit=1)
    assert [r.id for r in upcoming] == [rows["today_0915"].id, rows["later_0920"].id]
    assert (cursor, total) == (None, 2)  # upcoming 不分頁，limit 不適用

    past, cursor, total = await service.list_scope(PATIENT, "past", limit=20)
    assert [r.id for r in past] == [
        rows["cancelled_1001"].id,
        rows["ended_0914"].id,
        rows["missed_0912"].id,
        rows["attended_0910"].id,
    ]
    assert (cursor, total) == (None, 4)


async def test_past_pages_walk_to_the_oldest_record_without_gaps_or_repeats(
    service, repo, clock
):
    clock.now = _tpe(9, 30, 12)
    created = [await repo.create(make_appointment(at=_tpe(9, day))) for day in range(1, 8)]
    # 同一時間兩筆：取消之後在原時間重掛
    created.append(await repo.create(make_appointment(at=_tpe(9, 4), status="cancelled")))

    seen, cursor, pages = [], None, 0
    while True:
        items, cursor, total = await service.list_scope(
            PATIENT, "past", limit=3, cursor=cursor
        )
        pages += 1
        assert total == 8  # 整個 scope 的筆數，不是這一頁的
        seen.extend(items)
        if cursor is None:
            break

    assert pages == 3  # 3 + 3 + 2，最後不會多一頁空的
    assert sorted(r.id for r in seen) == sorted(r.id for r in created)
    stamps = [r.appointment_at for r in seen]
    assert stamps == sorted(stamps, reverse=True)


async def test_paging_is_stable_when_a_shown_record_is_deleted(service, repo, clock):
    """以位置分頁：刪掉已經顯示過的一筆，下一頁不會因此漏掉一筆。"""
    clock.now = _tpe(9, 30, 12)
    for day in range(1, 6):
        await repo.create(make_appointment(at=_tpe(9, day)))

    first, cursor, _ = await service.list_scope(PATIENT, "past", limit=2)
    assert [r.appointment_at.day for r in first] == [5, 4]
    await repo.delete(first[0].id)

    second, _, total = await service.list_scope(PATIENT, "past", limit=2, cursor=cursor)
    assert [r.local_appointment_at.day for r in second] == [3, 2]
    assert total == 4


@pytest.mark.parametrize("cursor", ["not-base64!", "e30", "W10", "eyJhdCI6IjIwMjYtMDktMDFUMDk6MDA6MDAiLCJpZCI6IngifQ"])
async def test_a_tampered_cursor_is_a_400(service, cursor):
    """"e30"、"W10" 是 {}、[]；最後一個的時間沒有 offset。"""
    await expect_error(
        service.list_scope(PATIENT, "past", limit=20, cursor=cursor),
        400,
        INVALID_CURSOR_DETAIL,
    )


async def test_delete_past_removes_exactly_what_the_past_list_counts(service, repo, clock):
    rows = await seed_history(repo)
    clock.now = _tpe(9, 15, 8)
    _, _, shown = await service.list_scope(PATIENT, "past", limit=50)

    assert await service.delete_past(PATIENT) == shown == 4
    remaining = await repo.list_by_user(PATIENT)
    assert [r.id for r in remaining] == [rows["today_0915"].id, rows["later_0920"].id]
    assert await repo.get_by_id(rows["someone_else"].id) is not None
    assert await service.delete_past(PATIENT) == 0
