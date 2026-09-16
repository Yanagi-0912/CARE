import logging

import pytest

from app.core import scheduler_heartbeat
from app.models.medical_news import DrugNews, make_news_ref
from app.services.line_messaging.send_result import SendOutcome, SendResult
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
    def __init__(self, fail=False, result=None):
        self.pushed = []
        self._fail = fail
        self._result = result

    async def push_flex_result(self, user_id, flex_message):
        self.pushed.append((user_id, flex_message))
        if self._result is not None:
            return self._result
        if self._fail:
            return SendResult(SendOutcome.TRANSIENT, 500, "boom")
        return SendResult.success()

    async def push_flex(self, user_id, flex_message):
        return (await self.push_flex_result(user_id, flex_message)).ok

    async def push_text(self, user_id, text):
        self.pushed.append((user_id, text))
        return True


class FakeUserRepo:
    def __init__(self, ids):
        self._ids = ids
        self.following = []

    async def list_all_line_ids(self, collection=None):
        return list(self._ids)

    async def set_following(self, line_id, following, at=None):
        self.following.append((line_id, following))
        return True


class FakeDayClaimRepo:
    def __init__(self, claim_result=True):
        self._claim_result = claim_result
        self.claims = []
        self.released = []

    async def claim(self, user_id, delivered_on, collection=None):
        self.claims.append((user_id, delivered_on))
        return self._claim_result

    async def release(self, user_id, delivered_on, collection=None):
        self.released.append((user_id, delivered_on))


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
        self.released = []

    async def list_pushed_refs(self, user_id, since, collection=None):
        return set(self._pushed)

    async def claim(self, user_id, news_ref, tier, collection=None, **payload):
        self.claims.append((user_id, news_ref, tier))
        self.payloads.append(payload)
        return self._claim_result

    async def release(self, user_id, news_ref, collection=None):
        self.released.append((user_id, news_ref))


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
        day_claim_repository=FakeDayClaimRepo(),
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
        async def push_flex_result(self, user_id, flex_message):
            order.append("push")
            return SendResult.success()

    scheduler = _scheduler(
        replier=OrderedReplier(), delivery_repository=OrderedDelivery()
    )

    await scheduler.run_once("2026-09-02")

    assert order == ["claim", "push"]


# ── 每日處理權（多實例）────────────────────────────────────────────


@pytest.mark.asyncio
async def test_day_is_claimed_before_any_picking():
    """先搶「今天這位使用者歸我」，再查用藥、選材。

    兩個實例各自選材可能挑到不同的消息，(user_id, news_ref) 擋不住那種情況；
    只有在選材之前就搶到日處理權，才能保證每日至多一則。
    """
    order = []

    class OrderedDayClaim(FakeDayClaimRepo):
        async def claim(self, user_id, delivered_on, collection=None):
            order.append(("day_claim", delivered_on))
            return True

    class OrderedMedRepo(FakeMedRepo):
        async def list_active_by_user(self, user_id, date_str, collection=None):
            order.append(("pick", date_str))
            return []

    scheduler = _scheduler(
        day_claim_repository=OrderedDayClaim(), medication_repository=OrderedMedRepo()
    )

    await scheduler.run_once("2026-09-02")

    assert order[:2] == [("day_claim", "2026-09-02"), ("pick", "2026-09-02")]


@pytest.mark.asyncio
async def test_day_already_claimed_by_other_instance_skips_user():
    replier = FakeReplier()
    delivery = FakeDeliveryRepo()
    scheduler = _scheduler(
        replier=replier,
        delivery_repository=delivery,
        day_claim_repository=FakeDayClaimRepo(claim_result=False),
    )

    await scheduler.run_once("2026-09-02")

    assert replier.pushed == []
    assert delivery.claims == []


@pytest.mark.asyncio
async def test_opted_out_user_does_not_claim_the_day():
    """關掉通知的人連日處理權都不該佔：判斷在任何寫入之前。"""

    class OptedOutProfiles:
        async def get_user_profile(self, line_id):
            return {"settings": {"notify_medical_news": False}}

    day_claims = FakeDayClaimRepo()
    scheduler = _scheduler(
        user_profile_service=OptedOutProfiles(), day_claim_repository=day_claims
    )

    await scheduler.run_once("2026-09-02")

    assert day_claims.claims == []


@pytest.mark.asyncio
async def test_unfollowed_user_is_skipped():
    """封鎖官方帳號的人不推：LINE 對封鎖者的 push 不會送達卻照算額度。"""

    class UnfollowedProfiles:
        async def get_user_profile(self, line_id):
            return {"is_following": False, "settings": {}}

    replier = FakeReplier()
    day_claims = FakeDayClaimRepo()
    scheduler = _scheduler(
        replier=replier,
        user_profile_service=UnfollowedProfiles(),
        day_claim_repository=day_claims,
    )

    await scheduler.run_once("2026-09-02")

    assert replier.pushed == []
    assert day_claims.claims == []


@pytest.mark.asyncio
async def test_missing_following_field_means_following():
    """既有使用者的文件沒有 is_following，視為仍在追蹤。"""

    class LegacyProfiles:
        async def get_user_profile(self, line_id):
            return {"settings": {}}

    replier = FakeReplier()
    scheduler = _scheduler(replier=replier, user_profile_service=LegacyProfiles())

    await scheduler.run_once("2026-09-02")

    assert len(replier.pushed) == 1


@pytest.mark.asyncio
async def test_no_content_releases_day_claim():
    """兩層都沒內容時放掉日處理權，稍後索引補上、當天重跑時仍輪得到他。"""
    day_claims = FakeDayClaimRepo()
    scheduler = _scheduler(kb_digest=FakeKbDigest([]), day_claim_repository=day_claims)

    await scheduler.run_once("2026-09-02")

    assert day_claims.claims == [("U1", "2026-09-02")]
    assert day_claims.released == [("U1", "2026-09-02")]


@pytest.mark.asyncio
async def test_successful_push_keeps_day_claim():
    day_claims = FakeDayClaimRepo()
    scheduler = _scheduler(day_claim_repository=day_claims)

    await scheduler.run_once("2026-09-02")

    assert day_claims.released == []


# ── 失敗處理 ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_quota_exceeded_releases_both_claims(caplog):
    """429 代表確定沒送出：news_ref 與日處理權都要放掉。

    留著 news_ref 的話這則會被記成「已推給他」，額度恢復後永遠不會再收到；
    留著日處理權的話同一天重跑會跳過他。
    """
    replier = FakeReplier(result=SendResult(SendOutcome.QUOTA_EXCEEDED, 429, "quota"))
    delivery = FakeDeliveryRepo()
    day_claims = FakeDayClaimRepo()
    scheduler = _scheduler(
        replier=replier, delivery_repository=delivery, day_claim_repository=day_claims
    )

    with caplog.at_level(logging.WARNING):
        await scheduler.run_once("2026-09-02")

    assert delivery.released == [("U1", delivery.claims[0][1])]
    assert day_claims.released == [("U1", "2026-09-02")]
    assert "因額度用完，1 位未送" in caplog.text


@pytest.mark.asyncio
async def test_quota_exceeded_stops_the_run_and_logs_one_summary_line(caplog):
    """額度用完後不再對其他人逐一嘗試；沒輪到的人沒被搶佔，明天照常。整輪只記一行。"""
    replier = FakeReplier(result=SendResult(SendOutcome.QUOTA_EXCEEDED, 429, "quota"))
    day_claims = FakeDayClaimRepo()
    scheduler = _scheduler(
        replier=replier,
        user_repository=FakeUserRepo(["U1", "U2", "U3"]),
        day_claim_repository=day_claims,
    )

    with caplog.at_level(logging.WARNING):
        await scheduler.run_once("2026-09-02")

    assert [user for user, _ in replier.pushed] == ["U1"]
    assert [user for user, _ in day_claims.claims] == ["U1"]
    summary_lines = [r for r in caplog.records if "因額度用完" in r.getMessage()]
    assert len(summary_lines) == 1
    assert "3 位未送" in summary_lines[0].getMessage()


@pytest.mark.asyncio
async def test_rejected_invalid_target_marks_user_not_following():
    """LINE 明確說對象無效（'to' 欄位）才標成未追蹤。"""
    replier = FakeReplier(
        result=SendResult(
            SendOutcome.REJECTED,
            400,
            "The property, 'to', in the request body is invalid (line: -, column: -)",
        )
    )
    users = FakeUserRepo(["U1"])
    delivery = FakeDeliveryRepo()
    scheduler = _scheduler(replier=replier, user_repository=users, delivery_repository=delivery)

    await scheduler.run_once("2026-09-02")

    assert users.following == [("U1", False)]
    # 不回滾：對象無效的話這則本來就送不出去，回滾只是讓明天再撞一次。
    assert delivery.released == []


@pytest.mark.asyncio
async def test_rejected_for_other_reasons_only_logs(caplog):
    """Flex 內容不合法之類的 400 是我們的問題，不得把人標成已封鎖。"""
    replier = FakeReplier(
        result=SendResult(SendOutcome.REJECTED, 400, "messages[0] is invalid")
    )
    users = FakeUserRepo(["U1"])
    scheduler = _scheduler(replier=replier, user_repository=users)

    with caplog.at_level(logging.WARNING):
        await scheduler.run_once("2026-09-02")

    assert users.following == []
    assert "outcome=rejected" in caplog.text


@pytest.mark.asyncio
async def test_transient_failure_keeps_claims():
    """5xx／逾時送沒送到不確定，回滾反而可能推成兩張。"""
    replier = FakeReplier(fail=True)
    delivery = FakeDeliveryRepo()
    day_claims = FakeDayClaimRepo()
    scheduler = _scheduler(
        replier=replier, delivery_repository=delivery, day_claim_repository=day_claims
    )

    await scheduler.run_once("2026-09-02")

    assert delivery.released == []
    assert day_claims.released == []


@pytest.mark.asyncio
async def test_set_following_failure_does_not_abort_run():
    class ExplodingUserRepo(FakeUserRepo):
        async def set_following(self, line_id, following, at=None):
            raise RuntimeError("db down")

    replier = FakeReplier(
        result=SendResult(SendOutcome.REJECTED, 400, "The property, 'to', in the request body is invalid")
    )
    scheduler = _scheduler(replier=replier, user_repository=ExplodingUserRepo(["U1", "U2"]))

    await scheduler.run_once("2026-09-02")

    assert [user for user, _ in replier.pushed] == ["U1", "U2"]


# ── 設定驗證與 log ──────────────────────────────────────────────────


def test_invalid_run_time_fails_at_construction():
    """設錯時要在建構時就炸，而不是 task 在第一次醒來前靜默死掉。"""
    with pytest.raises(ValueError, match="MEDICAL_NEWS_PUSH_TIME"):
        _scheduler(run_time="9:00 AM")


@pytest.mark.asyncio
async def test_logs_do_not_contain_raw_user_id(caplog):
    class ExplodingMedRepo(FakeMedRepo):
        async def list_active_by_user(self, user_id, date_str, collection=None):
            raise RuntimeError("boom")

    scheduler = _scheduler(
        user_repository=FakeUserRepo(["Uabcdef0123456789abcdef0123456789"]),
        medication_repository=ExplodingMedRepo(),
    )

    with caplog.at_level(logging.INFO):
        await scheduler.run_once("2026-09-02")

    assert "使用者處理失敗" in caplog.text
    assert "Uabcdef0123456789abcdef0123456789" not in caplog.text


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


# ── Tier 2 池子的個人化錯開 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_new_users_do_not_all_get_the_same_tier2_article():
    """沒有推播歷史的使用者不得全部拿到同一篇。

    池子是全體共用的，而唯一的個人化是「這位使用者收過沒」——對新加入的
    使用者那個條件對誰都成立。少了 user_id 錯開，功能上線那幾天所有長輩會
    收到一模一樣的卡。
    """
    pool = [_article(url=f"https://www.hpa.gov.tw/a/{i}") for i in range(10)]
    replier = FakeReplier()
    scheduler = _scheduler(
        replier=replier,
        kb_digest=FakeKbDigest(pool),
        user_repository=FakeUserRepo([f"U{i}" for i in range(10)]),
        delivery_repository=FakeDeliveryRepo(),
    )

    await scheduler.run_once("2026-09-02")

    titles = [flex for _, flex in replier.pushed]
    assert len(replier.pushed) == 10
    assert len(set(map(str, titles))) > 1


def test_pool_offset_is_stable_across_processes():
    """偏移不得依賴內建 `hash()`——它受 PYTHONHASHSEED 影響，每次重啟就跳掉。

    直接驗算 blake2b 的值：這個測試的目的正是釘住「同一個 user_id 在任何行程
    裡都得到同一個偏移」，所以不能只比對兩次呼叫的結果相同。
    """
    import hashlib

    from app.services.medical_news.push_scheduler import _pool_offset

    for user_id in ("U1", "Uabcdef0123456789", ""):
        expected = (
            int.from_bytes(
                hashlib.blake2b(user_id.encode("utf-8"), digest_size=8).digest(),
                "big",
            )
            % 7
        )
        assert _pool_offset(user_id, 7) == expected


def test_pool_offset_handles_empty_pool():
    from app.services.medical_news.push_scheduler import _pool_offset

    assert _pool_offset("U1", 0) == 0


@pytest.mark.asyncio
async def test_offset_still_skips_already_pushed_articles():
    """錯開之後仍然不得重推——去重優先於偏移。"""
    pool = [_article(url=f"https://www.hpa.gov.tw/a/{i}") for i in range(3)]
    from app.services.medical_news.push_scheduler import _pool_offset

    start = _pool_offset("U1", len(pool))
    already = make_news_ref("kb_article", pool[start].url)

    replier = FakeReplier()
    scheduler = _scheduler(
        replier=replier,
        kb_digest=FakeKbDigest(pool),
        user_repository=FakeUserRepo(["U1"]),
        delivery_repository=FakeDeliveryRepo(pushed={already}),
    )

    await scheduler.run_once("2026-09-02")

    assert len(replier.pushed) == 1
    assert pool[start].url not in str(replier.pushed[0][1])


@pytest.mark.asyncio
async def test_nothing_pushed_when_every_pool_article_already_seen():
    pool = [_article(url=f"https://www.hpa.gov.tw/a/{i}") for i in range(3)]
    seen = {make_news_ref("kb_article", a.url) for a in pool}
    replier = FakeReplier()
    scheduler = _scheduler(
        replier=replier,
        kb_digest=FakeKbDigest(pool),
        user_repository=FakeUserRepo(["U1"]),
        delivery_repository=FakeDeliveryRepo(pushed=seen),
    )

    await scheduler.run_once("2026-09-02")

    assert replier.pushed == []


# ── 官方優先、媒體補位 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_higher_priority_group_is_exhausted_before_lower():
    """只要前一群還有這位使用者沒收過的，就不得跳到後一群。

    群內仍做 user_id 錯開；但錯開若做在整個池子上，起點會隨機落在任何一群，
    「官方優先」就退化成「官方與媒體隨機」——而媒體一天十幾篇，隨機起點幾乎
    總落在媒體那群。
    """
    official = _article(url="https://www.hpa.gov.tw/fresh")._replace(priority=0)
    media = [
        _article(url=f"https://health.udn.com/{i}")._replace(priority=1)
        for i in range(9)
    ]
    replier = FakeReplier()
    scheduler = _scheduler(
        replier=replier,
        kb_digest=FakeKbDigest([official] + media),
        user_repository=FakeUserRepo([f"U{i}" for i in range(10)]),
        delivery_repository=FakeDeliveryRepo(),
    )

    await scheduler.run_once("2026-09-14")

    assert len(replier.pushed) == 10
    assert all("hpa.gov.tw/fresh" in str(flex) for _, flex in replier.pushed)


@pytest.mark.asyncio
async def test_falls_to_media_when_official_already_seen():
    """官方那篇收過了，才輪到媒體。"""
    from app.models.medical_news import make_news_ref

    official = _article(url="https://www.hpa.gov.tw/fresh")._replace(priority=0)
    media = _article(url="https://health.udn.com/1")._replace(priority=1)
    # FakeDeliveryRepo 不記得 claim 過什麼，所以「收過官方那篇」要預先放進去，
    # 而不是跑兩次 run_once。
    deliveries = FakeDeliveryRepo(pushed={make_news_ref("kb_article", official.url)})
    scheduler = _scheduler(
        kb_digest=FakeKbDigest([official, media]),
        delivery_repository=deliveries,
    )

    await scheduler.run_once("2026-09-14")

    assert [ref for _, ref, _ in deliveries.claims] == [
        make_news_ref("kb_article", media.url)
    ]
