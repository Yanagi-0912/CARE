"""走失求救 API：長輩上傳位置、家人看地圖與按已找到。

用真的 LostLocationService 搭記憶體 repository，驗的是路由與服務合起來的行為，
特別是權限：看得到地圖、按得了已找到的只有收到通報的家人。
"""

import pytest
from fastapi.testclient import TestClient

from app.dependencies import CurrentUser, get_current_user, get_lost_location_service
from app.main import app
from app.services.lost.lost_location_service import LostLocationService
from tests.unit.services.lost.lost_fakes import (
    DAUGHTER,
    ELDER,
    SON,
    Clock,
    FakeAuthorization,
    FakeLostRepository,
    FakeProfiles,
    FakeReplier,
)

client = TestClient(app)


@pytest.fixture()
def lost():
    clock = Clock()
    replier = FakeReplier()
    service = LostLocationService(
        replier=replier,
        authorization_service=FakeAuthorization(recipients=[DAUGHTER, SON]),
        user_profile_service=FakeProfiles(),
        repository=FakeLostRepository(),
        liff_id="1234-abcd",
        clock=clock,
    )
    current = {"user": ELDER}
    app.dependency_overrides[get_lost_location_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        line_user_id=current["user"]
    )

    def act_as(user_id):
        current["user"] = user_id

    yield service, replier, clock, act_as
    app.dependency_overrides.clear()


async def _start(service):
    return await service.report(ELDER, "我走丟了", "lost")


def test_elder_status_before_any_session(lost):
    response = client.get("/api/lost/me")

    assert response.status_code == 200
    assert response.json()["active"] is False


def test_location_upload_without_session_is_not_stored(lost):
    response = client.post(
        "/api/lost/me/location", json={"latitude": 25.03, "longitude": 121.56}
    )

    assert response.status_code == 200
    assert response.json()["active"] is False


async def test_upload_then_family_sees_the_map(lost):
    service, replier, clock, act_as = lost
    await _start(service)

    response = client.post(
        "/api/lost/me/location",
        json={"latitude": 25.033, "longitude": 121.565, "accuracy": 15},
    )
    assert response.status_code == 200
    assert response.json()["active"] is True
    # 背景任務在 TestClient 回應前跑完：第一次收到位置通知了家人
    assert len(replier.flex_to(DAUGHTER)) == 1

    act_as(DAUGHTER)
    view = client.get(f"/api/lost/{ELDER}")
    assert view.status_code == 200
    body = view.json()
    assert body["status"] == "active"
    assert body["patient_name"] == "王阿公"
    assert body["patient_words"] == "我走丟了"
    assert body["last_location"]["latitude"] == 25.033
    assert body["last_location"]["accuracy"] == 15
    assert body["stale"] is False
    assert body["stale_after_seconds"] == 180
    assert len(body["trail"]) == 1
    assert body["started_at"].endswith("Z") or body["started_at"].endswith("+00:00")


@pytest.mark.parametrize(
    "payload",
    [
        {"latitude": 91, "longitude": 121},
        {"latitude": 25, "longitude": 181},
        {"latitude": 25, "longitude": 121, "accuracy": -1},
        {"longitude": 121},
    ],
)
def test_invalid_coordinates_are_rejected(lost, payload):
    assert client.post("/api/lost/me/location", json=payload).status_code == 422


async def test_stranger_cannot_view_or_mark_found(lost):
    service, replier, clock, act_as = lost
    await _start(service)

    act_as("U_STRANGER")
    assert client.get(f"/api/lost/{ELDER}").status_code == 403
    assert client.post(f"/api/lost/{ELDER}/found").status_code == 403
    assert (await service.active(ELDER)) is not None


def test_family_view_without_any_session_is_404(lost):
    service, replier, clock, act_as = lost
    act_as(DAUGHTER)

    assert client.get(f"/api/lost/{ELDER}").status_code == 404


async def test_found_ends_the_session_and_elder_page_stops(lost):
    service, replier, clock, act_as = lost
    await _start(service)

    act_as(DAUGHTER)
    response = client.post(f"/api/lost/{ELDER}/found")
    assert response.json() == {"ended": True, "status": "found"}
    assert client.get(f"/api/lost/{ELDER}").json()["ended_by_name"] == "美玲"

    # 第二位家人晚一步按：不是錯誤，回最新狀態
    act_as(SON)
    assert client.post(f"/api/lost/{ELDER}/found").json() == {
        "ended": False,
        "status": "found",
    }

    act_as(ELDER)
    upload = client.post(
        "/api/lost/me/location", json={"latitude": 25.03, "longitude": 121.56}
    )
    assert upload.json()["active"] is False
    assert upload.json()["status"] == "found"


async def test_elder_can_end_it(lost):
    service, replier, clock, act_as = lost
    await _start(service)

    response = client.post("/api/lost/me/end")

    assert response.json() == {"ended": True, "status": "safe"}
    assert client.get("/api/lost/me").json()["active"] is False


async def test_family_sees_stale_after_three_minutes(lost):
    service, replier, clock, act_as = lost
    await _start(service)
    client.post("/api/lost/me/location", json={"latitude": 25.03, "longitude": 121.56})

    clock.advance(minutes=4)
    act_as(DAUGHTER)

    assert client.get(f"/api/lost/{ELDER}").json()["stale"] is True


def test_requires_login():
    app.dependency_overrides.clear()
    assert client.get("/api/lost/me").status_code == 401


async def test_elder_sees_family_who_is_coming(lost):
    service, replier, clock, act_as = lost
    await _start(service)
    client.post("/api/lost/me/location", json={"latitude": 25.0478, "longitude": 121.5170})

    act_as(DAUGHTER)
    response = client.post(
        f"/api/lost/{ELDER}/presence",
        json={"coming": True, "latitude": 25.0478, "longitude": 121.5249},
    )
    assert response.status_code == 200
    assert response.json() == {"recorded": True}
    act_as(SON)
    client.post(f"/api/lost/{ELDER}/presence", json={"coming": False})

    act_as(ELDER)
    body = client.post(
        "/api/lost/me/location", json={"latitude": 25.0478, "longitude": 121.5170}
    ).json()

    [daughter, son] = body["family"]
    assert daughter["name"] == "美玲"
    assert daughter["coming"] is True
    assert daughter["online"] is True
    assert 780 < daughter["distance_m"] < 810
    assert daughter["latitude"] == 25.0478
    assert son == {
        "name": "John",
        "online": True,
        "coming": False,
        "latitude": None,
        "longitude": None,
        "location_at": None,
        "distance_m": None,
    }
    assert client.get("/api/lost/me").json()["family"][0]["name"] == "美玲"
    assert replier.texts_to(ELDER) == ["美玲正在過來找你，請待在原地。"]


async def test_presence_from_stranger_is_forbidden(lost):
    service, _, _, act_as = lost
    await _start(service)

    act_as("U_STRANGER")
    response = client.post(f"/api/lost/{ELDER}/presence", json={"coming": True})

    assert response.status_code == 403
    assert "family" not in (await service.active(ELDER))


async def test_elder_opening_own_map_is_not_presence(lost):
    service, _, _, _ = lost
    await _start(service)

    response = client.post(f"/api/lost/{ELDER}/presence", json={"coming": True})

    assert response.json() == {"recorded": False}
    assert "family" not in (await service.active(ELDER))


async def test_presence_needs_both_coordinates(lost):
    service, _, _, act_as = lost
    await _start(service)

    act_as(DAUGHTER)
    response = client.post(
        f"/api/lost/{ELDER}/presence", json={"coming": True, "latitude": 25.0}
    )

    assert response.status_code == 422


async def test_presence_without_session_is_not_recorded(lost):
    _, _, _, act_as = lost

    act_as(DAUGHTER)
    response = client.post(f"/api/lost/{ELDER}/presence", json={"coming": True})

    assert response.json() == {"recorded": False}


async def test_family_view_remembers_viewer_is_coming(lost):
    service, _, _, act_as = lost
    await _start(service)

    act_as(DAUGHTER)
    assert client.get(f"/api/lost/{ELDER}").json()["viewer_coming"] is False
    client.post(f"/api/lost/{ELDER}/presence", json={"coming": True})
    assert client.get(f"/api/lost/{ELDER}").json()["viewer_coming"] is True

    act_as(SON)
    assert client.get(f"/api/lost/{ELDER}").json()["viewer_coming"] is False
