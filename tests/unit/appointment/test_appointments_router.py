"""HTTP 契約：打這支 URL 會回什麼。

授權服務是**真的** `FamilyAuthorizationService`（只換 repository），服務層也是真的
（repository 接記憶體 collection）——整包 mock 掉的話，403 與 exclude_unset 那兩條
路根本不會執行。
"""

import asyncio
from datetime import datetime
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
from app.repositories.appointment_repository import AppointmentReminderRepository
from app.routers.users.appointments import (
    DELETE_ALL_SCOPE_DETAIL,
    FORBIDDEN_DELETE_ALL_DETAIL,
    FORBIDDEN_READ_DETAIL,
    FORBIDDEN_WRITE_DETAIL,
)
from app.schemas import MedicalFacility
from app.services.appointment.appointment_service import AppointmentService
from app.services.family.family_authorization_service import (
    FamilyAuthorizationService,
)

from .support import TPE, FakeCollection, make_appointment, real_authz

ME = "U_ME"
ELDER = "U_ELDER"
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
    "cancelled_at",
    "cancelled_by_user_id",
    "enabled",
    "notify_at",
    "created_at",
    "updated_at",
}


def build_authz(role: Optional[str], state: str) -> FamilyAuthorizationService:
    """ME 對 ELDER 是 role；role=None 代表 ME 不在 ELDER 的族譜裡。"""
    return real_authz(ELDER, {} if role is None else {ME: role}, state)


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


def test_a_second_appointment_at_the_same_instant_is_a_409(client):
    """同一瞬間只能有一筆，不論醫院或科別。"""
    Env()
    assert create(client).status_code == 200
    res = create(client, facility_id=None, hospital_name="馬偕醫院", department="骨科")
    assert res.status_code == 409
    assert res.json() == {"detail": "這個時間已經有一筆掛號提醒，同一個時間只能有一筆。"}


def test_missing_required_field_is_fastapis_422(client):
    Env()
    body = {k: v for k, v in PAYLOAD.items() if k != "hospital_name"}
    res = client.post("/api/appointments/reminders", json=body)
    assert res.status_code == 422
    assert res.json()["detail"][0]["loc"] == ["body", "hospital_name"]


# ── 寫入權限：一律嚴格判定 ─────────────────────────────────────────────


REPORT_FORBIDDEN = "您沒有權限替這位家人回報出發或到診。"
CANCEL_FORBIDDEN = "您沒有權限替這位家人取消門診。"

# 掛號的每一條寫入路徑，與權限不足時該回的 detail。
WRITE_PATHS = {
    "POST": (lambda client, rid: create(client), FORBIDDEN_WRITE_DETAIL),
    "PUT": (
        lambda client, rid: client.put(f"/api/appointments/reminders/{rid}", json={"note": "x"}),
        FORBIDDEN_WRITE_DETAIL,
    ),
    "DELETE": (
        lambda client, rid: client.delete(f"/api/appointments/reminders/{rid}"),
        FORBIDDEN_WRITE_DETAIL,
    ),
    "depart": (
        lambda client, rid: client.post(f"/api/appointments/reminders/{rid}/depart"),
        REPORT_FORBIDDEN,
    ),
    "attend": (
        lambda client, rid: client.post(f"/api/appointments/reminders/{rid}/attend"),
        REPORT_FORBIDDEN,
    ),
    "cancel": (
        lambda client, rid: client.post(f"/api/appointments/reminders/{rid}/cancel"),
        CANCEL_FORBIDDEN,
    ),
}


@pytest.mark.parametrize("path", list(WRITE_PATHS))
@pytest.mark.parametrize(
    "role,allowed",
    [("GUARDIAN", True), ("CAREGIVER", True), ("MEMBER", False), (None, False)],
    ids=["GUARDIAN", "CAREGIVER", "MEMBER", "族譜外"],
)
@pytest.mark.parametrize("state", ["enforced", "shadow"])
def test_every_write_path_is_strict_in_both_modes(client, state, role, allowed, path):
    """已拍板：只有讀取權的家人不能更動掛號，影子模式下也一樣。

    寫入以 `has_legacy_equivalent=False` 判定，結果與遷移狀態無關；兩種模式跑同一張
    表，釘住的就是這件事。
    """
    env = Env(role=role, caller=ME, state=state)
    env.now = DAY_OF  # 出發／到診要在門診當天
    saved = asyncio.run(env.repo.create(make_appointment(
        user_id=ELDER, at=datetime(2026, 9, 15, 11, 0, tzinfo=TPE)
    )))
    call, forbidden = WRITE_PATHS[path]
    res = call(client, saved.id)
    if not allowed:
        assert res.status_code == 403
        assert res.json() == {"detail": forbidden}
        return
    assert res.status_code == 200
    if path == "POST":
        assert res.json()["creator_user_id"] == ME  # 代建：建立者是家屬，就診者是長輩


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


@pytest.mark.parametrize("state", ["enforced", "shadow"])
def test_member_can_read_the_elders_list(client, state):
    """讀取維持預設判定：MEMBER 本來就有 GENERAL 讀取權，兩種模式都讀得到。"""
    env = Env(role="MEMBER", caller=ME, state=state)
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


# ── cancel ────────────────────────────────────────────────────────────


def test_cancel_records_who_and_when_and_a_second_press_is_identical(client):
    env = Env(role="CAREGIVER", caller=ME)
    saved = asyncio.run(env.repo.create(make_appointment(user_id=ELDER)))

    res = client.post(f"/api/appointments/reminders/{saved.id}/cancel", json={"ignored": 1})
    assert res.status_code == 200
    body = res.json()
    assert set(body) == RESPONSE_KEYS
    assert body["status"] == "cancelled"
    assert body["cancelled_at"] == "2026-09-14T10:00:00+08:00"
    assert body["cancelled_by_user_id"] == ME
    assert body["notify_at"] == []

    # 長輩本人稍後也按了取消：200、逐字相同，取消者仍是第一位
    env.now = DAY_OF
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(line_user_id=ELDER)
    again = client.post(f"/api/appointments/reminders/{saved.id}/cancel")
    assert again.status_code == 200
    assert again.json() == body


@pytest.mark.parametrize(
    "status,detail",
    [
        ("attended", "已經回報到診的門診不能取消。"),
        ("missed", "這個門診的當天已經結束，不需要取消。"),
    ],
)
def test_cancel_conflicts(client, status, detail):
    env = Env()
    saved = asyncio.run(env.repo.create(make_appointment(user_id=ELDER, status=status)))
    res = client.post(f"/api/appointments/reminders/{saved.id}/cancel")
    assert res.status_code == 409
    assert res.json() == {"detail": detail}


def test_cancel_of_an_unknown_id_is_404(client):
    Env()
    res = client.post("/api/appointments/reminders/6aa27e77c964976ac9281c0f/cancel")
    assert res.status_code == 404
    assert res.json() == {"detail": "找不到這筆掛號提醒，可能已經被刪除。"}


def test_put_new_time_on_a_cancelled_reminder_is_a_409(client):
    Env()
    reminder_id = create(client).json()["id"]
    client.post(f"/api/appointments/reminders/{reminder_id}/cancel")
    res = client.put(
        f"/api/appointments/reminders/{reminder_id}",
        json={"appointment_at": "2026-09-22T14:00:00+08:00"},
    )
    assert res.status_code == 409
    assert res.json() == {
        "detail": "已經取消的掛號不能改時間；如果要改期，請新增一筆掛號提醒。"
    }


# ── GET ?scope= ───────────────────────────────────────────────────────


def seed_elder_history(env):
    """現在是 9/14 10:00：9/1～9/5 五筆已到診、9/20 一筆已取消（都算過去），
    9/15 一筆即將到來。"""
    for day in range(1, 6):
        asyncio.run(env.repo.create(make_appointment(
            user_id=ELDER, at=datetime(2026, 9, day, 9, 0, tzinfo=TPE), status="attended"
        )))
    asyncio.run(env.repo.create(make_appointment(
        user_id=ELDER, at=datetime(2026, 9, 20, 9, 0, tzinfo=TPE), status="cancelled"
    )))
    asyncio.run(env.repo.create(make_appointment(user_id=ELDER)))


def test_scope_upcoming_and_paged_past(client):
    env = Env()
    seed_elder_history(env)

    upcoming = client.get("/api/appointments/reminders?scope=upcoming").json()
    assert set(upcoming) == {"items", "next_cursor", "total_count"}
    assert [r["appointment_at"] for r in upcoming["items"]] == ["2026-09-15T09:30:00+08:00"]
    assert (upcoming["next_cursor"], upcoming["total_count"]) == (None, 1)

    first = client.get("/api/appointments/reminders?scope=past&limit=4").json()
    assert [r["appointment_at"][:10] for r in first["items"]] == [
        "2026-09-20", "2026-09-05", "2026-09-04", "2026-09-03",
    ]
    assert first["total_count"] == 6
    rest = client.get(
        "/api/appointments/reminders",
        params={"scope": "past", "limit": 4, "cursor": first["next_cursor"]},
    ).json()
    assert [r["appointment_at"][:10] for r in rest["items"]] == ["2026-09-02", "2026-09-01"]
    assert (rest["next_cursor"], rest["total_count"]) == (None, 6)

    # 不帶 scope：舊格式照舊
    legacy = client.get("/api/appointments/reminders?include_past=true").json()
    assert isinstance(legacy, list) and len(legacy) == 7


def test_scope_for_a_family_member(client):
    env = Env(role="MEMBER", caller=ME)
    seed_elder_history(env)
    body = client.get(f"/api/appointments/reminders?target_user_id={ELDER}&scope=past").json()
    assert body["total_count"] == 6
    assert set(body["items"][0]) == RESPONSE_KEYS


@pytest.mark.parametrize("query", ["scope=past&limit=51", "scope=past&limit=0", "scope=all"])
def test_malformed_list_parameters_are_fastapis_422(client, query):
    Env()
    assert client.get(f"/api/appointments/reminders?{query}").status_code == 422


def test_a_tampered_cursor_is_a_400(client):
    Env()
    res = client.get("/api/appointments/reminders?scope=past&cursor=e30")
    assert res.status_code == 400
    assert res.json() == {"detail": "分頁位置無效，請重新整理頁面後再試一次。"}


# ── DELETE /reminders?scope=past ──────────────────────────────────────


def test_delete_all_history_leaves_upcoming_alone(client):
    env = Env()
    seed_elder_history(env)
    assert client.delete("/api/appointments/reminders?scope=past").json() == {"deleted": 6}
    assert client.get("/api/appointments/reminders?scope=past").json()["total_count"] == 0
    assert client.get("/api/appointments/reminders?scope=upcoming").json()["total_count"] == 1
    # 沒有過去紀錄時不是 404；帶自己的 id 等同省略
    res = client.delete(f"/api/appointments/reminders?scope=past&target_user_id={ELDER}")
    assert (res.status_code, res.json()) == (200, {"deleted": 0})


@pytest.mark.parametrize("query", ["", "?scope=upcoming", "?scope=all"])
def test_delete_all_without_scope_past_is_a_400_and_deletes_nothing(client, query):
    env = Env()
    seed_elder_history(env)
    res = client.delete(f"/api/appointments/reminders{query}")
    assert res.status_code == 400
    assert res.json() == {"detail": DELETE_ALL_SCOPE_DETAIL}
    assert len(asyncio.run(env.repo.list_by_user(ELDER))) == 7


@pytest.mark.parametrize("role", ["GUARDIAN", "CAREGIVER"])
@pytest.mark.parametrize("target", [ELDER, ""])
def test_family_cannot_delete_all_and_it_never_falls_back_to_their_own(client, role, target):
    env = Env(role=role, caller=ME)
    seed_elder_history(env)
    mine = asyncio.run(env.repo.create(make_appointment(
        user_id=ME, at=datetime(2026, 9, 1, 9, 0, tzinfo=TPE)
    )))
    res = client.delete(f"/api/appointments/reminders?scope=past&target_user_id={target}")
    assert res.status_code == 403
    assert res.json() == {"detail": FORBIDDEN_DELETE_ALL_DETAIL}
    assert asyncio.run(env.repo.get_by_id(mine.id)) is not None
    assert len(asyncio.run(env.repo.list_by_user(ELDER))) == 7


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
