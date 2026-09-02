import pytest

from app.core import scheduler_heartbeat
from app.models.medical_news import DrugNews, make_news_ref
from app.services.medical_news.kb_digest_service import KbArticle
from app.services.medical_news.push_scheduler import MedicalNewsPushScheduler

URL = "https://www.fda.gov.tw/TC/newsContent.aspx?id=1"


def _news(drug_key="普拿疼", concern="recall", url=URL, published="2026-08-30"):
    return DrugNews(
        drug_key=drug_key,
        key_kind="name_zh",
        url=url,
        title="回收公告",
        source_name="食藥署",
        published_at=published,
        summary="食藥署公告某批號回收。",
        concern_kind=concern,
        content_hash="h",
    )


class FakeReplier:
    def __init__(self, fail=False):
        self.pushed = []
        self._fail = fail

    async def push_flex(self, user_id, flex_message):
        self.pushed.append((user_id, flex_message))
        return not self._fail

    async def push_text(self, user_id, text):
        self.pushed.append((user_id, text))
        return True


class FakeUserRepo:
    def __init__(self, ids):
        self._ids = ids

    async def list_all_line_ids(self, collection=None):
        return list(self._ids)


class FakeMedRepo:
    def __init__(self, by_user=None):
        self._by_user = by_user or {}

    async def list_active_by_user(self, user_id, date_str, collection=None):
        return list(self._by_user.get(user_id, []))


class FakeMedication:
    def __init__(self, name, generic_name=None):
        self.name = name
        self.generic_name = generic_name


class FakeNewsRepo:
    def __init__(self, news=None):
        self._news = news or []
        self.queried = []

    async def find_by_drug_keys(self, drug_keys, since, collection=None):
        self.queried.append(list(drug_keys))
        return [n for n in self._news if n.drug_key in set(drug_keys)]


class FakeDeliveryRepo:
    def __init__(self, pushed=None, claim_result=True):
        self._pushed = pushed or set()
        self._claim_result = claim_result
        self.claims = []
        self.payloads = []

    async def list_pushed_refs(self, user_id, since, collection=None):
        return set(self._pushed)

    async def claim(self, user_id, news_ref, tier, collection=None, **payload):
        self.claims.append((user_id, news_ref, tier))
        self.payloads.append(payload)
        return self._claim_result


class FakeKbDigest:
    def __init__(self, articles=None):
        self._articles = articles or []

    async def recent_articles(self, today, limit):
        return list(self._articles)


def _article(url="https://www.hpa.gov.tw/a/1"):
    return KbArticle(
        url=url,
        title="夏日補水",
        source_name="國民健康署",
        published_at="2026-08-30",
        excerpt="規律補充水分。",
    )


def _scheduler(**kwargs):
    defaults = dict(
        replier=FakeReplier(),
        user_profile_service=None,
        kb_digest=FakeKbDigest([_article()]),
        run_time="09:00",
        drug_news_repository=FakeNewsRepo(),
        delivery_repository=FakeDeliveryRepo(),
        medication_repository=FakeMedRepo(),
        user_repository=FakeUserRepo(["U1"]),
        max_age_days=30,
    )
    defaults.update(kwargs)
    return MedicalNewsPushScheduler(**defaults)


# ── 兩層選材 ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tier1_preferred_over_tier2():
    replier = FakeReplier()
    delivery = FakeDeliveryRepo()
    scheduler = _scheduler(
        replier=replier,
        delivery_repository=delivery,
        medication_repository=FakeMedRepo({"U1": [FakeMedication("普拿疼")]}),
        drug_news_repository=FakeNewsRepo([_news()]),
    )

    await scheduler.run_once("2026-09-02")

    assert len(replier.pushed) == 1
    assert delivery.claims[0][2] == 1


@pytest.mark.asyncio
async def test_falls_back_to_tier2_when_no_drug_news():
    replier = FakeReplier()
    delivery = FakeDeliveryRepo()
    scheduler = _scheduler(
        replier=replier,
        delivery_repository=delivery,
        medication_repository=FakeMedRepo({"U1": [FakeMedication("普拿疼")]}),
        drug_news_repository=FakeNewsRepo([]),
    )

    await scheduler.run_once("2026-09-02")

    assert len(replier.pushed) == 1
    assert delivery.claims[0][2] == 2


@pytest.mark.asyncio
async def test_user_without_medications_still_gets_tier2():
    """沒有用藥資料的使用者是 Tier 2 真正的目標對象，不得被跳過。"""
    replier = FakeReplier()
    scheduler = _scheduler(replier=replier, medication_repository=FakeMedRepo({}))

    await scheduler.run_once("2026-09-02")

    assert len(replier.pushed) == 1


@pytest.mark.asyncio
async def test_nothing_pushed_when_no_tier1_and_no_tier2():
    """兩層都沒有內容時安靜地不推，不推一張空卡。"""
    replier = FakeReplier()
    scheduler = _scheduler(replier=replier, kb_digest=FakeKbDigest([]))

    await scheduler.run_once("2026-09-02")

    assert replier.pushed == []


@pytest.mark.asyncio
async def test_concern_kind_priority_order():
    """recall 勝過同日的 education。"""
    replier = FakeReplier()
    delivery = FakeDeliveryRepo()
    recall = _news(concern="recall", url=URL + "&a=1")
    education = _news(concern="education", url=URL + "&a=2")
    scheduler = _scheduler(
        replier=replier,
        delivery_repository=delivery,
        medication_repository=FakeMedRepo({"U1": [FakeMedication("普拿疼")]}),
        drug_news_repository=FakeNewsRepo([education, recall]),
    )

    await scheduler.run_once("2026-09-02")

    assert delivery.claims[0][1] == make_news_ref("drug_news", recall.url)


@pytest.mark.asyncio
async def test_newer_wins_within_same_concern_kind():
    replier = FakeReplier()
    delivery = FakeDeliveryRepo()
    older = _news(url=URL + "&a=1", published="2026-08-01")
    newer = _news(url=URL + "&a=2", published="2026-08-30")
    scheduler = _scheduler(
        replier=replier,
        delivery_repository=delivery,
        medication_repository=FakeMedRepo({"U1": [FakeMedication("普拿疼")]}),
        drug_news_repository=FakeNewsRepo([older, newer]),
    )

    await scheduler.run_once("2026-09-02")

    assert delivery.claims[0][1] == make_news_ref("drug_news", newer.url)


@pytest.mark.asyncio
async def test_generic_name_also_matches_drug_news():
    """公告常以成分名發布，藥袋上印的是品牌短名。"""
    replier = FakeReplier()
    scheduler = _scheduler(
        replier=replier,
        medication_repository=FakeMedRepo(
            {"U1": [FakeMedication("普拿疼", generic_name="ACETAMINOPHEN")]}
        ),
        drug_news_repository=FakeNewsRepo([_news(drug_key="ACETAMINOPHEN")]),
        kb_digest=FakeKbDigest([]),
    )

    await scheduler.run_once("2026-09-02")

    assert len(replier.pushed) == 1


# ── 每日一則與去重 ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_at_most_one_card_per_user_per_day():
    replier = FakeReplier()
    news = [
        _news(drug_key="普拿疼", url=URL + "&a=1"),
        _news(drug_key="冠脂妥", url=URL + "&a=2"),
        _news(drug_key="脈優", url=URL + "&a=3"),
    ]
    scheduler = _scheduler(
        replier=replier,
        medication_repository=FakeMedRepo(
            {
                "U1": [
                    FakeMedication("普拿疼"),
                    FakeMedication("冠脂妥"),
                    FakeMedication("脈優"),
                ]
            }
        ),
        drug_news_repository=FakeNewsRepo(news),
    )

    await scheduler.run_once("2026-09-02")

    assert len(replier.pushed) == 1


@pytest.mark.asyncio
async def test_already_pushed_news_is_skipped():
    replier = FakeReplier()
    news = _news()
    scheduler = _scheduler(
        replier=replier,
        delivery_repository=FakeDeliveryRepo(
            pushed={make_news_ref("drug_news", news.url)}
        ),
        medication_repository=FakeMedRepo({"U1": [FakeMedication("普拿疼")]}),
        drug_news_repository=FakeNewsRepo([news]),
        kb_digest=FakeKbDigest([]),
    )

    await scheduler.run_once("2026-09-02")

    assert replier.pushed == []


@pytest.mark.asyncio
async def test_claim_failure_skips_push():
    """另一個排程實例已搶到時不得重複推播。"""
    replier = FakeReplier()
    scheduler = _scheduler(
        replier=replier, delivery_repository=FakeDeliveryRepo(claim_result=False)
    )

    await scheduler.run_once("2026-09-02")

    assert replier.pushed == []


@pytest.mark.asyncio
async def test_claim_happens_before_push():
    """先搶後推。反過來的話兩個實例會各推一次才發現撞號。"""
    order = []

    class OrderedDelivery(FakeDeliveryRepo):
        async def claim(self, user_id, news_ref, tier, collection=None, **payload):
            order.append("claim")
            return True

    class OrderedReplier(FakeReplier):
        async def push_flex(self, user_id, flex_message):
            order.append("push")
            return True

    scheduler = _scheduler(
        replier=OrderedReplier(), delivery_repository=OrderedDelivery()
    )

    await scheduler.run_once("2026-09-02")

    assert order == ["claim", "push"]


# ── 失敗處理 ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_push_failure_is_not_retried():
    replier = FakeReplier(fail=True)
    scheduler = _scheduler(replier=replier)

    await scheduler.run_once("2026-09-02")
    await scheduler.run_once("2026-09-02")

    assert len(replier.pushed) == 2  # 兩輪各一次，沒有同一輪內重試


@pytest.mark.asyncio
async def test_one_user_failure_does_not_abort_others():
    class ExplodingMedRepo(FakeMedRepo):
        async def list_active_by_user(self, user_id, date_str, collection=None):
            if user_id == "U1":
                raise RuntimeError("boom")
            return []

    replier = FakeReplier()
    scheduler = _scheduler(
        replier=replier,
        user_repository=FakeUserRepo(["U1", "U2"]),
        medication_repository=ExplodingMedRepo(),
    )

    await scheduler.run_once("2026-09-02")

    assert [user for user, _ in replier.pushed] == ["U2"]


# ── 心跳 ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_heartbeat_registered_separately_from_index_scheduler():
    """兩支排程的心跳必須分開登記。

    合併登記會讓其中一支停擺被另一支的心跳掩蓋——索引停了、推播照跑，
    外觀完全健康，只是內容永遠停在停擺那天。
    """
    scheduler_heartbeat.reset()
    scheduler = _scheduler()
    scheduler.start()
    try:
        assert MedicalNewsPushScheduler.HEARTBEAT_NAME in scheduler_heartbeat.registered()
    finally:
        await scheduler.stop()
        scheduler_heartbeat.reset()


@pytest.mark.asyncio
async def test_claim_stores_card_payload_for_sharing():
    """卡片內容必須跟著 claim 一起落地。

    news_ref 是雜湊、反解不回來源，分享的 postback 只帶得動它——內容沒有在
    這裡存下來，分享時就重建不出卡片。
    """
    delivery = FakeDeliveryRepo()
    news = _news()
    scheduler = _scheduler(
        delivery_repository=delivery,
        medication_repository=FakeMedRepo({"U1": [FakeMedication("普拿疼")]}),
        drug_news_repository=FakeNewsRepo([news]),
    )

    await scheduler.run_once("2026-09-02")

    payload = delivery.payloads[0]
    assert payload["url"] == news.url
    assert payload["title"] == news.title
    assert payload["summary"] == news.summary
    assert payload["source_name"] == news.source_name
