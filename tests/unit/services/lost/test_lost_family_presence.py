"""走失求救：長輩看得到誰正在看地圖、誰正在過來、離他多遠。

家人的位置只在他按了「我去找他」之後才給長輩看；「我去找他」每位家人只推長輩
一則，「正在看地圖」不推。
"""

import pytest

from app.services.lost.lost_location_service import (
    FAMILY_ONLINE_WITHIN,
    LostLocationService,
    distance_meters,
)
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

# 台北車站與往東約 800 公尺的一點（同緯度，經度差 0.0079°）
ELDER_AT = (25.0478, 121.5170)
DAUGHTER_AT = (25.0478, 121.5249)


def _service():
    clock = Clock()
    replier = FakeReplier()
    service = LostLocationService(
        replier=replier,
        authorization_service=FakeAuthorization(),
        user_profile_service=FakeProfiles(),
        repository=FakeLostRepository(),
        liff_id="1234-abcd",
        clock=clock,
    )
    return service, replier, clock


async def _started_with_location(service):
    await service.report(ELDER, "我走丟了", "lost")
    return await service.record_location(ELDER, lat=ELDER_AT[0], lng=ELDER_AT[1])


def test_distance_is_roughly_right():
    assert distance_meters(*ELDER_AT, *DAUGHTER_AT) == pytest.approx(796, abs=10)
    assert distance_meters(*ELDER_AT, *ELDER_AT) == 0


async def test_nobody_watching_means_empty_family():
    service, _, _ = _service()
    session = await _started_with_location(service)

    assert await service.family_status(session) == []


async def test_watching_family_is_online_without_location():
    service, _, _ = _service()
    await _started_with_location(service)

    session = await service.record_presence(
        ELDER, DAUGHTER, coming=False, lat=DAUGHTER_AT[0], lng=DAUGHTER_AT[1]
    )

    [member] = await service.family_status(session)
    assert member["name"] == "美玲"
    assert member["online"] is True
    assert member["coming"] is False
    # 只是在看地圖：就算前端送了座標也不存、不給長輩看
    assert member["location"] is None
    assert member["distance_m"] is None


async def test_watcher_drops_off_after_threshold():
    service, _, clock = _service()
    await _started_with_location(service)
    await service.record_presence(ELDER, DAUGHTER, coming=False)

    clock.advance(seconds=FAMILY_ONLINE_WITHIN.total_seconds() + 1)
    session = await service.active(ELDER)

    assert await service.family_status(session) == []


async def test_coming_family_has_distance_and_stays_after_leaving_the_page():
    service, _, clock = _service()
    await _started_with_location(service)
    await service.record_presence(
        ELDER, DAUGHTER, coming=True, lat=DAUGHTER_AT[0], lng=DAUGHTER_AT[1], accuracy=20
    )

    # 家人切去導航，地圖頁不再輪詢
    clock.advance(minutes=5)
    [member] = await service.family_status(await service.active(ELDER))

    assert member["online"] is False
    assert member["coming"] is True
    assert member["location"]["lat"] == DAUGHTER_AT[0]
    assert member["distance_m"] == pytest.approx(796, abs=10)


async def test_coming_without_fix_keeps_previous_location():
    service, _, _ = _service()
    await _started_with_location(service)
    await service.record_presence(ELDER, DAUGHTER, coming=True, lat=DAUGHTER_AT[0], lng=DAUGHTER_AT[1])

    session = await service.record_presence(ELDER, DAUGHTER, coming=True)

    [member] = await service.family_status(session)
    assert member["location"]["lng"] == DAUGHTER_AT[1]


async def test_stop_coming_clears_location():
    service, _, _ = _service()
    await _started_with_location(service)
    await service.record_presence(ELDER, DAUGHTER, coming=True, lat=DAUGHTER_AT[0], lng=DAUGHTER_AT[1])

    session = await service.record_presence(ELDER, DAUGHTER, coming=False)

    assert session["family"][DAUGHTER]["location"] is None
    [member] = await service.family_status(session)
    assert member["coming"] is False
    assert member["location"] is None


async def test_coming_sorted_first_then_nearest():
    service, _, _ = _service()
    await _started_with_location(service)
    await service.record_presence(ELDER, SON, coming=False)
    session = await service.record_presence(
        ELDER, DAUGHTER, coming=True, lat=DAUGHTER_AT[0], lng=DAUGHTER_AT[1]
    )

    names = [m["name"] for m in await service.family_status(session)]

    assert names == ["美玲", "John"]


async def test_presence_without_active_session_stores_nothing():
    service, _, _ = _service()

    assert await service.record_presence(ELDER, DAUGHTER, coming=True, lat=1, lng=2) is None


async def test_ended_session_shows_no_family():
    service, _, _ = _service()
    await _started_with_location(service)
    await service.record_presence(ELDER, DAUGHTER, coming=True)
    ended = await service.end_by_elder(ELDER)

    assert await service.family_status(ended) == []


async def test_coming_notice_pushes_elder_once_per_member():
    service, replier, _ = _service()
    await _started_with_location(service)

    session = await service.record_presence(ELDER, DAUGHTER, coming=True)
    assert service.needs_coming_notice(session, DAUGHTER)
    assert await service.notify_elder_family_coming(session, DAUGHTER) is True

    # 再輪詢、甚至先不去又再去，都不再推
    await service.record_presence(ELDER, DAUGHTER, coming=False)
    session = await service.record_presence(ELDER, DAUGHTER, coming=True)
    assert not service.needs_coming_notice(session, DAUGHTER)
    assert await service.notify_elder_family_coming(session, DAUGHTER) is False

    assert replier.texts_to(ELDER) == ["美玲正在過來找你，請待在原地。"]
    assert replier.texts_to(DAUGHTER) == []


async def test_watching_only_needs_no_notice():
    service, replier, _ = _service()
    await _started_with_location(service)

    session = await service.record_presence(ELDER, DAUGHTER, coming=False)

    assert not service.needs_coming_notice(session, DAUGHTER)
    assert replier.texts_to(ELDER) == []
