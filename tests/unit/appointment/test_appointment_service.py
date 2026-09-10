from datetime import datetime

import pytest

from app.i18n import t
from app.models.appointment import (
    CreateAppointmentReminderRequest,
    UpdateAppointmentReminderRequest,
)
from app.repositories.appointment_repository import AppointmentReminderRepository
from app.services.appointment.appointment_service import (
    DUPLICATE_DETAIL,
    IN_THE_PAST_DETAIL,
    RESCHEDULE_ATTENDED_DETAIL,
    TIMEZONE_REQUIRED_DETAIL,
    AppointmentError,
    AppointmentService,
)

from .support import DAUGHTER, PATIENT, STRANGER, TPE, FakeAuthz, FakeCollection

THE_DAY_BEFORE = datetime(2026, 9, 14, 10, 0, tzinfo=TPE)
DAY_OF = datetime(2026, 9, 15, 8, 40, tzinfo=TPE)


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


# ── 重複的掛號 ────────────────────────────────────────────────────────


async def test_same_time_hospital_and_department_is_a_duplicate(service):
    """家屬與本人各建了一次同一張掛號單：第二筆要擋下來，否則每則推播都會發兩次。"""
    await service.create(PATIENT, create_request())
    await expect_error(service.create(DAUGHTER, create_request()), 409, DUPLICATE_DETAIL)


async def test_duplicate_check_ignores_spacing_in_names(service):
    await service.create(PATIENT, create_request(facility_id=None))
    await expect_error(
        service.create(
            PATIENT,
            create_request(facility_id=None, hospital_name="台大 醫院", department=" 心臟 內科"),
        ),
        409,
        DUPLICATE_DETAIL,
    )


async def test_same_time_different_department_is_allowed(service):
    """同一家醫院、同一個早上兩個科同時報到是常見的，不是重複。"""
    await service.create(PATIENT, create_request())
    saved = await service.create(PATIENT, create_request(department="眼科"))
    assert saved.department == "眼科"


async def test_same_name_but_different_facility_is_allowed(service):
    """連鎖診所的分院常常同名；兩邊都有 facility_id 時只看 id。"""
    await service.create(PATIENT, create_request(facility_id="branch-a", hospital_name="仁愛診所"))
    saved = await service.create(
        PATIENT, create_request(facility_id="branch-b", hospital_name="仁愛診所")
    )
    assert saved.facility_id == "branch-b"


async def test_same_facility_id_is_a_duplicate_even_if_the_name_was_edited(service):
    await service.create(PATIENT, create_request(facility_id="fac-1", hospital_name="台大醫院"))
    await expect_error(
        service.create(
            PATIENT, create_request(facility_id="fac-1", hospital_name="國立臺灣大學醫學院附設醫院")
        ),
        409,
        DUPLICATE_DETAIL,
    )


async def test_different_time_is_not_a_duplicate(service):
    await service.create(PATIENT, create_request())
    await service.create(PATIENT, create_request(appointment_at="2026-09-15T14:00:00+08:00"))


async def test_cancelled_reminder_does_not_count(service, repo):
    first = await service.create(PATIENT, create_request())
    await repo.update_fields(first.id, {"status": "cancelled"})
    await service.create(PATIENT, create_request())


async def test_moving_one_appointment_onto_another_is_rejected(service):
    await service.create(PATIENT, create_request())
    later = await service.create(PATIENT, create_request(appointment_at="2026-09-15T14:00:00+08:00"))
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
    message = "這個門診的當天已經結束，無法再回報出發或到診。"
    await expect_error(service.depart(saved.id, PATIENT), 409, message)
    await expect_error(service.attend(saved.id, PATIENT), 409, message)


async def test_reporting_on_a_cancelled_appointment_is_a_conflict(service, repo, clock):
    saved = await service.create(PATIENT, create_request())
    await repo.update_fields(saved.id, {"status": "cancelled"})
    clock.now = DAY_OF
    await expect_error(
        service.attend(saved.id, PATIENT), 409, "這筆掛號提醒已經取消，無法回報出發或到診。"
    )


async def test_localized_error_for_line(service, clock):
    saved = await service.create(PATIENT, create_request())
    clock.now = DAY_OF
    with pytest.raises(AppointmentError) as exc_info:
        await service.depart(saved.id, STRANGER)
    assert exc_info.value.localized("en") == t("appt.error.forbidden_report", "en")


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
