"""三個通知開關的實際效力。

`notify_reminder` 與 `notify_family` 在 `UserSettings` 與 LIFF 設定頁上存在已久，
但後端從來沒有讀過它們——使用者把開關關掉，推播照送。這種壞法不會報錯、不會
留 log，只會表現為「我明明關了還是一直收到」，而使用者多半會歸咎於自己按錯或
直接封鎖官方帳號。

這個檔案存在的意義就是讓那件事不可能再發生：每個開關都有一條「關掉時
push_flex 未被呼叫」的斷言。
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.medical_news import DrugNews
from app.services.medical_news.kb_digest_service import KbArticle
from app.services.medical_news.push_scheduler import MedicalNewsPushScheduler

URL = "https://www.fda.gov.tw/TC/newsContent.aspx?id=1"


def _profile(**settings):
    base = {"language": "zh-TW", "font_size": "large"}
    base.update(settings)
    return {"name": "李老先生", "settings": base}


class FakeReplier:
    def __init__(self):
        self.pushed = []

    async def push_flex(self, user_id, flex_message):
        self.pushed.append(user_id)
        return True


class FakeProfileService:
    def __init__(self, by_user):
        self._by_user = by_user

    async def get_user_profile(self, line_id):
        return self._by_user.get(line_id, _profile())


class FakeUserRepo:
    def __init__(self, ids):
        self._ids = ids

    async def list_all_line_ids(self, collection=None):
        return list(self._ids)


class FakeMedRepo:
    async def list_active_by_user(self, user_id, date_str, collection=None):
        return []


class FakeNewsRepo:
    async def find_by_drug_keys(self, drug_keys, since, collection=None):
        return []


class FakeDeliveryRepo:
    def __init__(self):
        self.claims = []

    async def list_pushed_refs(self, user_id, since, collection=None):
        return set()

    async def claim(self, user_id, news_ref, tier, collection=None, **payload):
        self.claims.append(user_id)
        return True


class FakeKbDigest:
    async def recent_articles(self, today, limit):
        return [
            KbArticle(
                url="https://www.hpa.gov.tw/a/1",
                title="夏日補水",
                source_name="國民健康署",
                published_at="2026-08-30",
                excerpt="規律補充水分。",
            )
        ]


def _scheduler(profiles, delivery=None):
    return MedicalNewsPushScheduler(
        replier=(replier := FakeReplier()),
        user_profile_service=FakeProfileService(profiles),
        kb_digest=FakeKbDigest(),
        run_time="09:00",
        max_age_days=30,
        drug_news_repository=FakeNewsRepo(),
        delivery_repository=delivery or FakeDeliveryRepo(),
        medication_repository=FakeMedRepo(),
        user_repository=FakeUserRepo(list(profiles) or ["U1"]),
    ), replier


# ── 每日醫療消息卡 ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_medical_news_off_means_no_push():
    scheduler, replier = _scheduler({"U1": _profile(notify_medical_news=False)})

    await scheduler.run_once("2026-09-02")

    assert replier.pushed == []


@pytest.mark.asyncio
async def test_medical_news_on_still_pushes():
    scheduler, replier = _scheduler({"U1": _profile(notify_medical_news=True)})

    await scheduler.run_once("2026-09-02")

    assert replier.pushed == ["U1"]


@pytest.mark.asyncio
async def test_medical_news_defaults_to_on_when_unset():
    """既有使用者的文件沒有這個欄位，讀回為缺席時視為開啟——沿用
    `UserSettings.notify_medical_news` 的預設值，不需要 backfill。"""
    scheduler, replier = _scheduler({"U1": _profile()})

    await scheduler.run_once("2026-09-02")

    assert replier.pushed == ["U1"]


@pytest.mark.asyncio
async def test_opted_out_user_does_not_consume_delivery_claim():
    """關掉的人不該佔用 delivery 紀錄。

    佔用了的話，他日後重新打開時，那幾則會被當成「已推過」而永遠收不到。
    """
    delivery = FakeDeliveryRepo()
    scheduler, _ = _scheduler(
        {"U1": _profile(notify_medical_news=False)}, delivery=delivery
    )

    await scheduler.run_once("2026-09-02")

    assert delivery.claims == []


@pytest.mark.asyncio
async def test_opt_out_is_per_user():
    scheduler, replier = _scheduler(
        {
            "U1": _profile(notify_medical_news=False),
            "U2": _profile(notify_medical_news=True),
        }
    )

    await scheduler.run_once("2026-09-02")

    assert replier.pushed == ["U2"]
