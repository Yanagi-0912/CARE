"""看診錄音整理完成的推播：誰收得到、點進去是誰的紀錄、推不出去不能炸。"""

import pytest

from app.models.clinic_transcript import ClinicVisitRecord
from app.models.family_authorization import (
    STRICT_NOTIFICATION_KINDS,
    notification_recipient_roles,
)
from app.services.clinic_transcript.notifier import ClinicVisitNotifier

LIFF = "https://liff.line.me/123-abc"


class _Replier:
    def __init__(self, flex_ok=True, raise_flex=False):
        self.flex, self.text = [], []
        self.flex_ok, self.raise_flex = flex_ok, raise_flex

    async def push_flex(self, user_id, flex):
        if self.raise_flex:
            raise RuntimeError("LINE 掛了")
        self.flex.append((user_id, flex))
        return self.flex_ok

    async def push_text(self, user_id, text):
        self.text.append((user_id, text))
        return True


class _Authz:
    def __init__(self, recipients=None, exc=None):
        self.recipients, self.exc = recipients or [], exc
        self.calls = []

    async def notification_recipients(self, owner_id, kind):
        self.calls.append((owner_id, kind))
        if self.exc:
            raise self.exc
        return self.recipients


class _Profiles:
    def __init__(self, profiles):
        self.profiles = profiles

    async def get_user_profile(self, user_id):
        return self.profiles.get(user_id)


def _record(**overrides):
    data = dict(
        id="rec1",
        user_id="U-elder",
        created_by_user_id="U-elder",
        consent="doctor_agreed",
        hospital_name="臺大醫院",
    )
    data.update(overrides)
    return ClinicVisitRecord(**data)


def _uri(flex):
    return flex.contents.to_dict()["footer"]["contents"][0]["action"]["uri"]


def test_政策與讀取權一致只給看得到紀錄的角色且兩種模式都嚴格():
    assert notification_recipient_roles("clinic_visit_ready") == frozenset(
        {"GUARDIAN", "CAREGIVER"}
    )
    assert "clinic_visit_ready" in STRICT_NOTIFICATION_KINDS


@pytest.mark.asyncio
async def test_整理好了送本人與有權限的家人_家人的連結帶就診者():
    replier = _Replier()
    authz = _Authz(["U-guardian", "U-elder"])
    notifier = ClinicVisitNotifier(
        replier=replier,
        authorization_service=authz,
        user_profile_service=_Profiles({"U-elder": {"name": "王奶奶"}}),
        liff_url=LIFF,
    )
    await notifier.notify_ready(_record())

    assert authz.calls == [("U-elder", "clinic_visit_ready")]
    sent = dict(replier.flex)
    assert list(sent) == ["U-elder", "U-guardian"]  # 本人不重複送
    assert _uri(sent["U-elder"]) == f"{LIFF}/clinic-visits/rec1"
    assert _uri(sent["U-guardian"]) == f"{LIFF}/clinic-visits/rec1?target_user_id=U-elder"
    assert "王奶奶" in str(sent["U-guardian"].contents.to_dict())


@pytest.mark.asyncio
async def test_陪診的家人一定收到_即使他不在推播名單():
    replier = _Replier()
    notifier = ClinicVisitNotifier(
        replier=replier, authorization_service=_Authz([]), liff_url=LIFF
    )
    await notifier.notify_ready(_record(created_by_user_id="U-son"))
    assert [uid for uid, _ in replier.flex] == ["U-elder", "U-son"]


@pytest.mark.asyncio
async def test_家人關掉家人通知就不送_本人照送():
    replier = _Replier()
    notifier = ClinicVisitNotifier(
        replier=replier,
        authorization_service=_Authz(["U-guardian"]),
        user_profile_service=_Profiles(
            {"U-guardian": {"settings": {"notify_family": False}}}
        ),
        liff_url=LIFF,
    )
    await notifier.notify_ready(_record())
    assert [uid for uid, _ in replier.flex] == ["U-elder"]


@pytest.mark.asyncio
async def test_收件人判定失敗時仍通知本人():
    replier = _Replier()
    notifier = ClinicVisitNotifier(
        replier=replier, authorization_service=_Authz(exc=RuntimeError("db")), liff_url=LIFF
    )
    await notifier.notify_ready(_record())
    assert [uid for uid, _ in replier.flex] == ["U-elder"]


@pytest.mark.asyncio
async def test_失敗只通知按下錄音的人():
    replier = _Replier()
    authz = _Authz(["U-guardian"])
    notifier = ClinicVisitNotifier(replier=replier, authorization_service=authz, liff_url=LIFF)
    await notifier.notify_failed(_record(created_by_user_id="U-son"))
    assert [uid for uid, _ in replier.flex] == ["U-son"]
    assert authz.calls == []


@pytest.mark.asyncio
async def test_Flex推不出去改送純文字_純文字也掛了不拋錯():
    replier = _Replier(raise_flex=True)
    notifier = ClinicVisitNotifier(replier=replier, authorization_service=None, liff_url=LIFF)
    await notifier.notify_ready(_record())
    assert [uid for uid, _ in replier.text] == ["U-elder"]

    class _Broken(_Replier):
        async def push_text(self, user_id, text):
            raise RuntimeError("還是掛了")

    await ClinicVisitNotifier(
        replier=_Broken(raise_flex=True), authorization_service=None, liff_url=LIFF
    ).notify_ready(_record())


@pytest.mark.asyncio
async def test_沒設LIFF網址時卡片沒有按鈕但照送():
    replier = _Replier()
    notifier = ClinicVisitNotifier(replier=replier, authorization_service=None, liff_url="")
    await notifier.notify_ready(_record())
    assert "footer" not in replier.flex[0][1].contents.to_dict()


@pytest.mark.asyncio
async def test_卡片不含任何摘要內容():
    """LINE 聊天室列表誰都看得到，推播只說好了。"""
    replier = _Replier()
    record = _record(
        summary={"main_points": ["血糖偏高要調藥"], "medication_changes": [], "next_visit": "",
                 "reminders": [], "unclear": [], "truncated": False},
    )
    await ClinicVisitNotifier(
        replier=replier, authorization_service=None, liff_url=LIFF
    ).notify_ready(record)
    assert "血糖" not in str(replier.flex[0][1].contents.to_dict())
