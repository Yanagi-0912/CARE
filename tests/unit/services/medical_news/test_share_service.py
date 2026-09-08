from datetime import datetime, timezone

import pytest

from app.models.family_tree import FamilyMember, FamilyTree
from app.models.medical_news import MedicalNewsDelivery, make_news_ref
from app.services.medical_news.share_service import MedicalNewsShareService

REF = make_news_ref("drug_news", "https://www.fda.gov.tw/TC/newsContent.aspx?id=1")


def _delivery(**overrides):
    payload = {
        "user_id": "U1",
        "news_ref": REF,
        "tier": 1,
        "title": "食藥署公告某批號回收",
        "summary": "食藥署公告某批號回收，已通知醫療院所下架。",
        "source_name": "食藥署",
        "url": "https://www.fda.gov.tw/TC/newsContent.aspx?id=1",
        "pushed_at": datetime.now(timezone.utc),
    }
    payload.update(overrides)
    return MedicalNewsDelivery(**payload)


class FakeReplier:
    def __init__(self, fail_for=()):
        self.pushed_flex = []
        self.replied = []
        self._fail_for = set(fail_for)

    async def push_flex(self, user_id, flex_message):
        self.pushed_flex.append((user_id, flex_message))
        return user_id not in self._fail_for

    async def reply(self, *, reply_token, message_text, user_id, **kwargs):
        self.replied.append(message_text)
        return True


class FakeDeliveryRepo:
    def __init__(self, delivery=None, shares_today=0):
        self._delivery = delivery
        self._shares_today = shares_today
        self.marked = []

    async def find(self, user_id, news_ref, collection=None):
        return self._delivery

    async def count_shares_today(self, user_id, day_start, collection=None):
        return self._shares_today

    async def mark_shared(self, user_id, news_ref, recipient_count, collection=None):
        self.marked.append((user_id, news_ref, recipient_count))


class FakeShareRepo:
    def __init__(self, taken=()):
        self._taken = set(taken)
        self.claims = []

    async def claim(self, recipient_id, news_ref, sharer_id, collection=None):
        self.claims.append(recipient_id)
        if recipient_id in self._taken:
            return False
        self._taken.add(recipient_id)
        return True


class FakeFamilyTreeService:
    def __init__(self, member_ids=("U2", "U3")):
        self._member_ids = member_ids

    async def get_family_tree(self, user_id):
        now = datetime.now(timezone.utc)
        return FamilyTree(
            user_id=user_id,
            family_members=[FamilyMember(user_id=m) for m in self._member_ids],
            created_at=now,
            updated_at=now,
        )


class FakeAuthorizationService:
    def __init__(self):
        self.notification_calls = []

    async def notification_recipients(self, subject_owner_id, kind, now=None):
        self.notification_calls.append(subject_owner_id)
        return []


class FakeProfileService:
    async def get_user_profile(self, line_id):
        return {"name": "小明", "settings": {"language": "zh-TW", "font_size": "large"}}


def _service(**kwargs):
    defaults = dict(
        replier=FakeReplier(),
        family_tree_service=FakeFamilyTreeService(),
        user_profile_service=FakeProfileService(),
        delivery_repository=FakeDeliveryRepo(_delivery()),
        share_repository=FakeShareRepo(),
        daily_share_limit=5,
    )
    defaults.update(kwargs)
    return MedicalNewsShareService(**defaults)


# ── 零洩漏 ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_shared_card_payload_excludes_drug_name():
    """Tier 1 的來源帶有 drug_key，分享卡不得攜帶它。

    分享卡的 builder 介面上根本沒有藥名參數，因此這裡驗的是「服務沒有把藥名
    塞進標題或摘要」。
    """
    replier = FakeReplier()
    delivery = _delivery(title="某批號回收", summary="食藥署公告某批號回收。")
    service = _service(
        replier=replier, delivery_repository=FakeDeliveryRepo(delivery)
    )

    await service.share(sharer_id="U1", news_ref=REF, reply_token="tok")

    payload = str(replier.pushed_flex[0][1])
    assert "普拿疼" not in payload


@pytest.mark.asyncio
async def test_does_not_call_notification_recipients():
    """分享 SHALL NOT 走 NOTIFICATION_POLICY。

    那張表答的是「他出事時通知誰」，與「我想主動分享給誰」是不同的信任。
    共用會讓兩者互相污染——日後調整通報政策，會在毫無關聯的地方改變分享行為。
    """
    auth = FakeAuthorizationService()
    service = _service(family_authorization_service=auth)

    await service.share(sharer_id="U1", news_ref=REF, reply_token="tok")

    assert auth.notification_calls == []


# ── 收件人與去重 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_shares_to_all_family_members():
    replier = FakeReplier()
    service = _service(replier=replier)

    await service.share(sharer_id="U1", news_ref=REF, reply_token="tok")

    assert [user for user, _ in replier.pushed_flex] == ["U2", "U3"]


@pytest.mark.asyncio
async def test_duplicate_recipient_claim_prevents_second_send():
    """兩位家人先後分享同一則給同一位收件人，該收件人只收到一次。"""
    replier = FakeReplier()
    share_repo = FakeShareRepo(taken={"U2"})
    service = _service(replier=replier, share_repository=share_repo)

    await service.share(sharer_id="U1", news_ref=REF, reply_token="tok")

    assert [user for user, _ in replier.pushed_flex] == ["U3"]


@pytest.mark.asyncio
async def test_sharer_never_receives_own_share():
    replier = FakeReplier()
    service = _service(
        replier=replier,
        family_tree_service=FakeFamilyTreeService(member_ids=("U1", "U2")),
    )

    await service.share(sharer_id="U1", news_ref=REF, reply_token="tok")

    assert [user for user, _ in replier.pushed_flex] == ["U2"]


@pytest.mark.asyncio
async def test_empty_family_replies_with_guidance():
    replier = FakeReplier()
    service = _service(
        replier=replier, family_tree_service=FakeFamilyTreeService(member_ids=())
    )

    await service.share(sharer_id="U1", news_ref=REF, reply_token="tok")

    assert replier.pushed_flex == []
    assert "家庭成員清單目前是空的" in replier.replied[0]


@pytest.mark.asyncio
async def test_daily_limit_blocks_further_shares():
    replier = FakeReplier()
    service = _service(
        replier=replier,
        delivery_repository=FakeDeliveryRepo(_delivery(), shares_today=5),
        daily_share_limit=5,
    )

    await service.share(sharer_id="U1", news_ref=REF, reply_token="tok")

    assert replier.pushed_flex == []
    assert "上限" in replier.replied[0]


@pytest.mark.asyncio
async def test_unknown_news_ref_is_reported_not_crashed():
    """使用者可能點到很久以前的卡片，而該筆紀錄已被清掉。"""
    replier = FakeReplier()
    service = _service(replier=replier, delivery_repository=FakeDeliveryRepo(None))

    await service.share(sharer_id="U1", news_ref=REF, reply_token="tok")

    assert replier.pushed_flex == []
    assert replier.replied


@pytest.mark.asyncio
async def test_push_failure_for_one_recipient_does_not_abort_others():
    replier = FakeReplier(fail_for={"U2"})
    service = _service(replier=replier)

    await service.share(sharer_id="U1", news_ref=REF, reply_token="tok")

    assert [user for user, _ in replier.pushed_flex] == ["U2", "U3"]


@pytest.mark.asyncio
async def test_marks_shared_with_successful_recipient_count():
    replier = FakeReplier(fail_for={"U2"})
    delivery_repo = FakeDeliveryRepo(_delivery())
    service = _service(replier=replier, delivery_repository=delivery_repo)

    await service.share(sharer_id="U1", news_ref=REF, reply_token="tok")

    assert delivery_repo.marked[0][2] == 1


@pytest.mark.asyncio
async def test_confirmation_reports_recipient_count():
    replier = FakeReplier()
    service = _service(replier=replier)

    await service.share(sharer_id="U1", news_ref=REF, reply_token="tok")

    assert "2" in replier.replied[0]


@pytest.mark.asyncio
async def test_all_recipients_already_have_it_replies_distinctly():
    replier = FakeReplier()
    service = _service(
        replier=replier, share_repository=FakeShareRepo(taken={"U2", "U3"})
    )

    await service.share(sharer_id="U1", news_ref=REF, reply_token="tok")

    assert replier.pushed_flex == []
    assert "已經收到" in replier.replied[0]
