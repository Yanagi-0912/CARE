"""HTTP 契約：打這支 URL 會回什麼。

授權服務是**真的** `FamilyAuthorizationService`（只換 repository），服務層也是真的
（repository 接記憶體 collection）——整包 mock 掉的話，403 與 exclude_unset 那兩條
路根本不會執行。
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional

import pytest
from fastapi.testclient import TestClient

from app.dependencies import (
    CurrentUser,
    get_appointment_service,
    get_current_user,
    get_family_authorization_service,
    get_medical_service,
)
from app.main import app
from app.models.family_tree import FamilyMember, FamilyTree
from app.repositories.appointment_repository import AppointmentReminderRepository
from app.routers.users.appointments import FORBIDDEN_READ_DETAIL, FORBIDDEN_WRITE_DETAIL
from app.schemas import MedicalFacility
from app.services.appointment.appointment_service import AppointmentService
from app.services.family.family_authorization_service import (
    FamilyAuthorizationService,
)

from .support import TPE, FakeCollection, make_appointment

ME = "U_ME"
ELDER = "U_ELDER"
TREE_TIME = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
THE_DAY_BEFORE = datetime(2026, 9, 14, 10, 0, tzinfo=TPE)
DAY_OF = datetime(2026, 9, 15, 8, 40, tzinfo=TPE)

PAYLOAD = {
    "user_id": ELDER,
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

RESPONSE_KEYS = {
    "id",
    "user_id",
    "creator_user_id",
    "appointment_at",
    "facility_id",
    "hospital_name",
    "hospital_address",
    "hospital_phone",
    "department",
    "doctor_name",
    "serial_number",
    "note",
    "status",
    "departed_at",
    "departed_by_user_id",
    "attended_at",
    "attended_by_user_id",
    "enabled",
    "notify_at",
    "created_at",
    "updated_at",
}


class _Trees:
    def __init__(self, trees):
        self.trees = trees

    async def get_by_user_id(self, user_id):
        return self.trees.get(user_id)


class _NoDelegations:
    async def has_active_delegation(self, owner_id, delegate_user_id, now=None):
        return False


def build_authz(role: Optional[str], state: str) -> FamilyAuthorizationService:
    members = [] if role is None else [FamilyMember(user_id=ME, family_role=role)]
    tree = FamilyTree(
        user_id=ELDER,
        family_members=members,
        rbac_migration_state=state,
        created_at=TREE_TIME,
        updated_at=TREE_TIME,
    )
    return FamilyAuthorizationService(
        family_tree_repository=_Trees({ELDER: tree}),
        delegation_repository=_NoDelegations(),
        enforcement_enabled=True,
    )


class Env:
    def __init__(self, role: Optional[str] = None, caller: str = ELDER, state: str = "enforced"):
        self.now = THE_DAY_BEFORE
        authz = build_authz(role, state)
        col = FakeCollection()
        self.repo = AppointmentReminderRepository(collection_provider=lambda: col)
        self.service = AppointmentService(
            repository=self.repo, authorization_service=authz, clock=lambda: self.now
        )
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(line_user_id=caller)
        app.dependency_overrides[get_family_authorization_service] = lambda: authz
        app.dependency_overrides[get_appointment_service] = lambda: self.service


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    app.dependency_overrides.clear()


def create(client, **overrides):
    return client.post("/api/appointments/reminders", json={**PAYLOAD, **overrides})


# ── POST ──────────────────────────────────────────────────────────────


def test_create_returns_the_full_shape_with_offsets_and_nulls(client):
    Env()
    res = create(client)
    assert res.status_code == 200
    body = res.json()
    assert set(body) == RESPONSE_KEYS
    assert body["appointment_at"] == "2026-09-15T09:30:00+08:00"
    assert body["notify_at"] == [
        "2026-09-15T08:30:00+08:00",
        "2026-09-15T09:30:00+08:00",
        "2026-09-15T10:00:00+08:00",
    ]
    assert body["note"] is None
    assert body["status"] == "scheduled"
    assert body["departed_at"] is None
    assert body["created_at"] == "2026-09-14T10:00:00+08:00"
    assert body["creator_user_id"] == ELDER


def test_create_without_offset_is_a_400_with_a_readable_detail(client):
    Env()
    res = create(client, appointment_at="2026-09-15T09:30:00")
    assert res.status_code == 400
    assert res.json() == {"detail": "門診時間必須帶時區，例如 2026-09-15T09:30:00+08:00。"}


def test_missing_required_field_is_fastapis_422(client):
    Env()
    body = {k: v for k, v in PAYLOAD.items() if k != "hospital_name"}
    res = client.post("/api/appointments/reminders", json=body)
    assert res.status_code == 422
    assert res.json()["detail"][0]["loc"] == ["body", "hospital_name"]


@pytest.mark.parametrize("role", ["GUARDIAN", "CAREGIVER"])
def test_general_writers_can_create_for_the_elder(client, role):
    Env(role=role, caller=ME)
    res = create(client)
    assert res.status_code == 200
    assert res.json()["creator_user_id"] == ME


@pytest.mark.parametrize("role", ["MEMBER", None])
def test_member_or_stranger_gets_the_appointment_specific_403(client, role):
    Env(role=role, caller=ME)
    res = create(client)
    assert res.status_code == 403
    assert res.json() == {"detail": FORBIDDEN_WRITE_DETAIL}


def test_member_in_shadow_mode_keeps_the_legacy_write(client):
    Env(role="MEMBER", caller=ME, state="shadow")
    assert create(client).status_code == 200


# ── GET ───────────────────────────────────────────────────────────────


def test_list_defaults_to_today_and_future(client):
    env = Env()
    asyncio.run(env.repo.create(make_appointment(
        user_id=ELDER, at=datetime(2026, 9, 10, 9, 0, tzinfo=TPE)
    )))
    create(client)

    default = client.get("/api/appointments/reminders").json()
    everything = client.get("/api/appointments/reminders?include_past=true").json()
    assert [r["appointment_at"] for r in default] == ["2026-09-15T09:30:00+08:00"]
    assert [r["appointment_at"] for r in everything] == [
        "2026-09-10T09:00:00+08:00",
        "2026-09-15T09:30:00+08:00",
    ]


def test_member_can_read_the_elders_list(client):
    env = Env(role="MEMBER", caller=ME)
    asyncio.run(env.repo.create(make_appointment(user_id=ELDER)))
    res = client.get(f"/api/appointments/reminders?target_user_id={ELDER}")
    assert res.status_code == 200
    assert set(res.json()[0]) == RESPONSE_KEYS  # 全部 GENERAL，遮蔽不拿掉任何欄位


def test_stranger_cannot_read(client):
    Env(role=None, caller=ME)
    res = client.get(f"/api/appointments/reminders?target_user_id={ELDER}")
    assert res.status_code == 403
    assert res.json() == {"detail": FORBIDDEN_READ_DETAIL}


# ── PUT ───────────────────────────────────────────────────────────────


def test_put_null_clears_and_absent_keys_are_untouched(client):
    Env()
    reminder_id = create(client).json()["id"]
    res = client.put(f"/api/appointments/reminders/{reminder_id}", json={"doctor_name": None})
    assert res.status_code == 200
    assert res.json()["doctor_name"] is None
    assert res.json()["serial_number"] == "23"


def test_put_null_on_a_required_field(client):
    Env()
    reminder_id = create(client).json()["id"]
    res = client.put(f"/api/appointments/reminders/{reminder_id}", json={"hospital_name": None})
    assert res.status_code == 400
    assert res.json() == {"detail": "以下欄位不接受空值：醫院名稱（hospital_name）"}


def test_put_new_time_resets_status_and_notify_at(client):
    env = Env()
    reminder_id = create(client).json()["id"]
    env.now = DAY_OF
    client.post(f"/api/appointments/reminders/{reminder_id}/depart")
    res = client.put(
        f"/api/appointments/reminders/{reminder_id}",
        json={"appointment_at": "2026-09-22T14:00:00+08:00"},
    )
    body = res.json()
    assert body["status"] == "scheduled"
    assert body["departed_at"] is None
    assert body["notify_at"][0] == "2026-09-22T13:00:00+08:00"


def test_put_by_member_is_forbidden(client):
    env = Env(role="MEMBER", caller=ME)
    saved = asyncio.run(env.repo.create(make_appointment(user_id=ELDER)))
    res = client.put(f"/api/appointments/reminders/{saved.id}", json={"note": "x"})
    assert res.status_code == 403
    assert res.json() == {"detail": FORBIDDEN_WRITE_DETAIL}


# ── DELETE ────────────────────────────────────────────────────────────


def test_delete_then_404(client):
    Env()
    reminder_id = create(client).json()["id"]
    assert client.delete(f"/api/appointments/reminders/{reminder_id}").json() == {"ok": True}
    res = client.delete(f"/api/appointments/reminders/{reminder_id}")
    assert res.status_code == 404
    assert res.json() == {"detail": "找不到這筆掛號提醒，可能已經被刪除。"}


# ── depart／attend ────────────────────────────────────────────────────


def test_depart_then_attend(client):
    env = Env()
    reminder_id = create(client).json()["id"]
    env.now = DAY_OF
    departed = client.post(f"/api/appointments/reminders/{reminder_id}/depart").json()
    assert departed["status"] == "departed"
    assert departed["departed_at"] == "2026-09-15T08:40:00+08:00"
    assert departed["departed_by_user_id"] == ELDER

    attended = client.post(f"/api/appointments/reminders/{reminder_id}/attend").json()
    assert attended["status"] == "attended"
    assert attended["notify_at"] == []


def test_caregiver_can_report_but_member_cannot(client):
    env = Env(role="MEMBER", caller=ME)
    saved = asyncio.run(env.repo.create(make_appointment(user_id=ELDER)))
    env.now = DAY_OF
    res = client.post(f"/api/appointments/reminders/{saved.id}/attend")
    assert res.status_code == 403
    assert res.json() == {"detail": "您沒有權限替這位家人回報出發或到診。"}

    env = Env(role="CAREGIVER", caller=ME)
    saved = asyncio.run(env.repo.create(make_appointment(user_id=ELDER)))
    env.now = DAY_OF
    body = client.post(f"/api/appointments/reminders/{saved.id}/attend").json()
    assert body["attended_by_user_id"] == ME


def test_reporting_too_early_is_a_409(client):
    Env()
    reminder_id = create(client).json()["id"]
    res = client.post(f"/api/appointments/reminders/{reminder_id}/attend")
    assert res.status_code == 409
    assert res.json() == {"detail": "門診當天才能回報出發或到診。"}


def test_depart_after_attend_is_a_409(client):
    env = Env()
    reminder_id = create(client).json()["id"]
    env.now = DAY_OF
    client.post(f"/api/appointments/reminders/{reminder_id}/attend")
    res = client.post(f"/api/appointments/reminders/{reminder_id}/depart")
    assert res.status_code == 409
    assert res.json() == {"detail": "已經回報到診了，不需要再回報出發。"}


# ── GET /api/medical/facilities/{id} ──────────────────────────────────


class _Medical:
    def __init__(self, facility):
        self.facility = facility
        self.calls = []

    async def get_facility_by_id(self, facility_id):
        self.calls.append(facility_id)
        return self.facility


def test_facility_by_id(client):
    medical = _Medical(
        MedicalFacility(
            id="665f1c2e8b3e4a0012345678",
            name="國立臺灣大學醫學院附設醫院",
            latitude=25.0408,
            longitude=121.5188,
            address="臺北市中正區中山南路7號",
            phone="0223123456",
            type="醫院",
            clinic_time=None,
            departments=["心臟內科", "家庭醫學科"],
        )
    )
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(line_user_id=ME)
    app.dependency_overrides[get_medical_service] = lambda: medical
    res = client.get("/api/medical/facilities/665f1c2e8b3e4a0012345678")
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == "665f1c2e8b3e4a0012345678"
    assert body["departments"] == ["心臟內科", "家庭醫學科"]
    assert body["distance_meters"] is None
    assert body["business_status"]["status"] == "unknown"
    assert medical.calls == ["665f1c2e8b3e4a0012345678"]


def test_facility_by_id_not_found(client):
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(line_user_id=ME)
    app.dependency_overrides[get_medical_service] = lambda: _Medical(None)
    res = client.get("/api/medical/facilities/nope")
    assert res.status_code == 404
    assert res.json() == {"detail": "查無此院所資料，可能已被更新或移除。"}
