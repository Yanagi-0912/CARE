"""走失求救服務：誰收到什麼、什麼時候不再推、結束之後誰被告知。

推播額度是實際約束（目前方案每月 3,000 則），所以這裡有一半的測試在驗「不推」：
重複說走丟不重推、位置持續更新不推、停止更新只推一次。
"""

import json

import pytest

from app.services.lost.lost_location_service import (
    AUTO_END_AFTER,
    NOTIFICATION_KIND,
    STALE_AFTER,
    LostLocationService,
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


def _service(**overrides):
    parts = {
        "replier": FakeReplier(),
        "authorization_service": FakeAuthorization(),
        "user_profile_service": FakeProfiles(),
        "repository": FakeLostRepository(),
        "liff_id": "1234-abcd",
        "clock": Clock(),
    }
    parts.update(overrides)
    return LostLocationService(**parts), parts


def _flex_json(flex) -> str:
    return json.dumps(flex.contents.to_dict(), ensure_ascii=False)


async def _started(service):
    report = await service.report(ELDER, "我迷路了，這裡有一間全聯", "lost")
    assert report.outcome == "started"
    return report.session


async def test_report_creates_one_session_and_asks_the_right_policy():
    service, parts = _service()

    report = await service.report(ELDER, "我走丟了", "lost")

    assert report.outcome == "started"
    assert report.session["user_id"] == ELDER
    assert report.session["patient_words"] == "我走丟了"
    assert parts["authorization_service"].calls == [(ELDER, NOTIFICATION_KIND)]
    assert len(parts["repository"].docs) == 1


async def test_saying_it_again_does_not_start_a_second_session():
    service, parts = _service()
    await _started(service)

    again = await service.report(ELDER, "我走丟了", "lost")

    assert again.outcome == "already_active"
    assert len(parts["repository"].docs) == 1


async def test_no_family_means_no_session():
    service, parts = _service(authorization_service=FakeAuthorization(recipients=[]))

    report = await service.report(ELDER, "我走丟了", "lost")

    assert report.outcome == "no_family"
    assert parts["repository"].docs == []


async def test_elder_is_never_their_own_recipient():
    service, _ = _service(authorization_service=FakeAuthorization(recipients=[ELDER]))

    assert (await service.report(ELDER, "我走丟了", "lost")).outcome == "no_family"


async def test_authorization_failure_is_treated_as_no_family():
    service, _ = _service(authorization_service=FakeAuthorization(error=RuntimeError("db")))

    assert (await service.report(ELDER, "我走丟了", "lost")).outcome == "no_family"


async def test_family_alert_goes_to_everyone_in_their_own_language_with_the_map_link():
    service, parts = _service()
    session = await _started(service)

    assert await service.notify_family_of_report(session) is True

    replier = parts["replier"]
    [daughter_card] = replier.flex_to(DAUGHTER)
    [son_card] = replier.flex_to(SON)
    assert daughter_card.alt_text == "王阿公說自己走丟了"
    assert son_card.alt_text == "王阿公 says they are lost"
    daughter_json = _flex_json(daughter_card)
    assert "我迷路了，這裡有一間全聯" in daughter_json
    assert "https://liff.line.me/1234-abcd/lost/watch?user=U_ELDER" in daughter_json
    # 長輩本人不會收到家人的通報卡
    assert replier.flex_to(ELDER) == []


async def test_share_intent_uses_the_gentler_wording():
    service, parts = _service()
    report = await service.report(ELDER, "傳位置給家人", "share")

    await service.notify_family_of_report(report.session)

    [card] = parts["replier"].flex_to(DAUGHTER)
    assert card.alt_text == "王阿公想讓你知道自己在哪裡"


async def test_recipient_who_turned_off_family_notifications_is_skipped():
    profiles = FakeProfiles()
    profiles.profiles[SON]["settings"]["notify_family"] = False
    service, parts = _service(user_profile_service=profiles)
    session = await _started(service)

    assert await service.notify_family_of_report(session) is True

    assert parts["replier"].flex_to(SON) == []
    assert len(parts["replier"].flex_to(DAUGHTER)) == 1


async def test_flex_failure_falls_back_to_text():
    service, parts = _service(replier=FakeReplier(flex_ok=False))
    session = await _started(service)

    assert await service.notify_family_of_report(session) is True

    assert parts["replier"].texts_to(DAUGHTER) == ["王阿公說自己走丟了"]


async def test_without_liff_id_cards_have_no_broken_buttons():
    service, parts = _service(liff_id="")
    session = await _started(service)

    await service.notify_family_of_report(session)

    [card] = parts["replier"].flex_to(DAUGHTER)
    assert "liff.line.me" not in _flex_json(card)
    assert "liff.line.me" not in _flex_json(service.elder_card("lost.elder.header.lost"))


async def test_location_without_an_active_session_is_not_stored():
    service, parts = _service()

    assert await service.record_location(ELDER, lat=25.03, lng=121.56) is None
    assert parts["repository"].docs == []


async def test_first_location_notifies_family_once_and_later_ones_do_not():
    service, parts = _service()
    await _started(service)

    first = await service.record_location(ELDER, lat=25.03, lng=121.56, accuracy=12)
    assert service.needs_location_started_notice(first)
    assert await service.notify_location_started(first) is True

    second = await service.record_location(ELDER, lat=25.031, lng=121.561)
    assert not service.needs_location_started_notice(second)
    # 就算兩個請求同時看到「還沒通知」，也只有一方推得出去
    assert await service.notify_location_started(first) is False

    assert len(parts["replier"].flex_to(DAUGHTER)) == 1
    assert len(second["trail"]) == 2


async def test_stale_location_notifies_family_and_elder_once():
    clock = Clock()
    service, parts = _service(clock=clock)
    await _started(service)
    await service.record_location(ELDER, lat=25.03, lng=121.56)

    clock.advance(minutes=2)
    await service.process_due()
    assert parts["replier"].flex == []

    clock.advance(minutes=2)
    await service.process_due()
    await service.process_due()

    [card] = parts["replier"].flex_to(DAUGHTER)
    assert card.alt_text == "王阿公的位置停止更新了"
    body = _flex_json(card)
    assert "已經 4 分鐘沒有收到新位置" in body
    assert "destination=25.03,121.56" in body
    # 長輩收到一張請他重新打開定位頁的卡
    [elder_card] = parts["replier"].flex_to(ELDER)
    assert "https://liff.line.me/1234-abcd/lost/share" in _flex_json(elder_card)


async def test_location_resuming_rearms_the_stale_notice():
    clock = Clock()
    service, parts = _service(clock=clock)
    await _started(service)
    await service.record_location(ELDER, lat=25.03, lng=121.56)
    clock.advance(minutes=4)
    await service.process_due()

    await service.record_location(ELDER, lat=25.04, lng=121.57)
    clock.advance(minutes=4)
    await service.process_due()

    assert len(parts["replier"].flex_to(DAUGHTER)) == 2


async def test_never_opened_page_is_not_reported_as_stopped():
    clock = Clock()
    service, parts = _service(clock=clock)
    await _started(service)

    clock.advance(minutes=30)
    await service.process_due()

    assert parts["replier"].flex == []


async def test_is_stale_matches_the_threshold():
    clock = Clock()
    service, _ = _service(clock=clock)
    await _started(service)
    session = await service.record_location(ELDER, lat=25.03, lng=121.56)

    clock.advance(seconds=STALE_AFTER.total_seconds() - 1)
    assert not service.is_stale(session)
    clock.advance(seconds=2)
    assert service.is_stale(session)


async def test_auto_end_tells_family_to_call_110_and_tells_the_elder():
    clock = Clock()
    service, parts = _service(clock=clock)
    await _started(service)

    clock.advance(seconds=AUTO_END_AFTER.total_seconds())
    await service.process_due()
    await service.process_due()

    assert parts["replier"].texts_to(DAUGHTER) == [
        "王阿公的位置分享已經超過 2 小時，自動停止了。如果還沒找到人，請撥 110 報警。"
    ]
    assert len(parts["replier"].texts_to(ELDER)) == 1
    assert (await service.active(ELDER)) is None
    assert (await service.latest(ELDER))["status"] == "expired"


async def test_found_by_one_family_member_tells_the_others_and_the_elder():
    service, parts = _service()
    await _started(service)

    ended = await service.end_by_family(ELDER, DAUGHTER)

    assert ended["status"] == "found"
    replier = parts["replier"]
    assert replier.texts_to(DAUGHTER) == []
    assert replier.texts_to(SON) == ["美玲 has found 王阿公. Location sharing has stopped."]
    assert replier.texts_to(ELDER) == ["美玲說已經找到你了，位置分享已經停止。"]


async def test_second_found_press_does_nothing():
    service, parts = _service()
    await _started(service)
    await service.end_by_family(ELDER, DAUGHTER)
    before = list(parts["replier"].texts)

    assert await service.end_by_family(ELDER, SON) is None
    assert parts["replier"].texts == before


async def test_elder_saying_they_are_safe_tells_the_family():
    service, parts = _service()
    await _started(service)

    ended = await service.end_by_elder(ELDER)

    assert ended["status"] == "safe"
    assert parts["replier"].texts_to(DAUGHTER) == ["王阿公說自己已經安全了，位置分享已經停止。"]
    assert parts["replier"].texts_to(ELDER) == []


async def test_only_recipients_can_view():
    service, _ = _service()

    assert await service.can_view(DAUGHTER, ELDER)
    assert await service.can_view(ELDER, ELDER)
    assert not await service.can_view("U_STRANGER", ELDER)


@pytest.mark.parametrize("name", ["", None])
async def test_missing_patient_name_uses_each_recipients_language(name):
    profiles = FakeProfiles()
    profiles.profiles[ELDER]["name"] = name
    service, parts = _service(user_profile_service=profiles)
    session = await _started(service)

    await service.notify_family_of_report(session)

    [daughter_card] = parts["replier"].flex_to(DAUGHTER)
    [son_card] = parts["replier"].flex_to(SON)
    assert daughter_card.alt_text == "你的家人說自己走丟了"
    assert son_card.alt_text == "Your family member says they are lost"
