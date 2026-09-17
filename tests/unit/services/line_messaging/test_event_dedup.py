"""LineEventDedup：webhook 事件 id 的認領（Redis SET NX 與本行程備援）。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.line_messaging.event_dedup import LineEventDedup, event_identity


def _redis(set_result=True):
    client = MagicMock()
    client.set = AsyncMock(return_value=set_result)
    return client


@pytest.mark.asyncio
async def test_first_claim_uses_redis_set_nx_with_ttl():
    client = _redis(set_result=True)
    dedup = LineEventDedup(lambda: client, ttl_seconds=600)

    assert await dedup.claim("evt-1") is True

    client.set.assert_awaited_once_with("line:webhook:event:evt-1", b"1", nx=True, ex=600)


@pytest.mark.asyncio
async def test_redis_says_already_claimed():
    dedup = LineEventDedup(lambda: _redis(set_result=None))

    assert await dedup.claim("evt-1") is False


@pytest.mark.asyncio
async def test_missing_event_id_is_always_handled():
    client = _redis()
    dedup = LineEventDedup(lambda: client)

    assert await dedup.claim(None) is True
    assert await dedup.claim("") is True
    client.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_redis_failure_falls_back_to_local_memory_and_logs_once(caplog):
    def _broken():
        raise ConnectionError("redis down")

    dedup = LineEventDedup(_broken)
    with caplog.at_level("WARNING"):
        assert await dedup.claim("evt-1") is True
        assert await dedup.claim("evt-1") is False
        assert await dedup.claim("evt-2") is True

    warnings = [r for r in caplog.records if "本行程記憶去重" in r.getMessage()]
    assert len(warnings) == 1


@pytest.mark.asyncio
async def test_redis_hang_is_bounded_by_timeout():
    client = MagicMock()

    async def _never(*_a, **_k):
        await asyncio.sleep(10)

    client.set = _never
    dedup = LineEventDedup(lambda: client, redis_timeout_seconds=0.01)

    assert await dedup.claim("evt-1") is True
    assert await dedup.claim("evt-1") is False  # 已退回本地記憶


@pytest.mark.asyncio
async def test_local_fallback_is_bounded():
    dedup = LineEventDedup(None, local_capacity=3)

    for i in range(5):
        assert await dedup.claim(f"evt-{i}") is True

    assert dedup.local_size == 3
    # 最舊的兩筆被淘汰，再認領視為新的
    assert await dedup.claim("evt-0") is True
    assert await dedup.claim("evt-4") is False


@pytest.mark.asyncio
async def test_local_fallback_expires_after_ttl(monkeypatch):
    dedup = LineEventDedup(None, ttl_seconds=1)
    now = [1000.0]
    monkeypatch.setattr(
        "app.services.line_messaging.event_dedup.time.monotonic", lambda: now[0]
    )

    assert await dedup.claim("evt-1") is True
    assert await dedup.claim("evt-1") is False
    now[0] += 2
    assert await dedup.claim("evt-1") is True


def test_event_identity_reads_sdk_fields():
    from linebot.v3.webhooks import DeliveryContext, FollowDetail, FollowEvent, UserSource

    event = FollowEvent(
        timestamp=1,
        mode="active",
        webhookEventId="01HZTESTID",
        deliveryContext=DeliveryContext(isRedelivery=True),
        replyToken="rt",
        source=UserSource(type="user", userId="U1"),
        follow=FollowDetail(isUnblocked=False),
    )
    assert event_identity(event) == ("01HZTESTID", True)
    assert event_identity(object()) == (None, False)
