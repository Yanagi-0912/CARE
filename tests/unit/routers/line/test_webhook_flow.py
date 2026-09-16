"""webhook 進站流程：先回 200、事件併行、重送去重、缺 secret 拒絕啟動。"""

import asyncio
import importlib
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.routers.line import webhook
from app.services.line_messaging.event_dedup import LineEventDedup


def _event(event_id: str, *, redelivery: bool = False):
    event = MagicMock()
    event.webhook_event_id = event_id
    event.delivery_context = MagicMock(is_redelivery=redelivery)
    return event


@pytest.mark.asyncio
async def test_events_in_one_batch_run_concurrently():
    """兩個事件各自等 0.05 秒；串行要 0.1 秒以上，併行不到。"""
    started, finished = [], []

    async def _handle(event):
        started.append(event.webhook_event_id)
        await asyncio.sleep(0.05)
        finished.append(event.webhook_event_id)

    handler = MagicMock()
    handler.handle = AsyncMock(side_effect=_handle)
    dedup = LineEventDedup(None)

    loop = asyncio.get_running_loop()
    t0 = loop.time()
    tasks = webhook.schedule_events(handler, [_event("a"), _event("b")], dedup=dedup)
    await asyncio.gather(*tasks)

    assert loop.time() - t0 < 0.09
    assert sorted(finished) == ["a", "b"]


@pytest.mark.asyncio
async def test_redelivered_event_with_claimed_id_is_skipped():
    handler = MagicMock()
    handler.handle = AsyncMock()
    dedup = LineEventDedup(None)

    await asyncio.gather(*webhook.schedule_events(handler, [_event("dup")], dedup=dedup))
    await asyncio.gather(
        *webhook.schedule_events(handler, [_event("dup", redelivery=True)], dedup=dedup)
    )

    assert handler.handle.await_count == 1


@pytest.mark.asyncio
async def test_handler_exception_stays_inside_the_task(caplog):
    handler = MagicMock()
    handler.handle = AsyncMock(side_effect=RuntimeError("boom"))

    with caplog.at_level("ERROR"):
        tasks = webhook.schedule_events(handler, [_event("x")], dedup=LineEventDedup(None))
        await asyncio.gather(*tasks)  # 不會 raise

    assert any("背景處理失敗" in r.getMessage() for r in caplog.records)
    assert not webhook._background_tasks  # 完成後不再持有參考


def test_callback_returns_ok_before_events_finish():
    """handler 永遠不完成，webhook 仍要立刻回 200。"""
    never = asyncio.Event()

    async def _hang(event):
        await never.wait()

    handler = MagicMock()
    handler.handle = AsyncMock(side_effect=_hang)
    # 只掛 webhook router：app.main 的 lifespan 會跑啟動檢查、連資料庫，
    # 這裡要驗的只有「回 200 不等事件處理完」。
    test_app = FastAPI()
    test_app.include_router(webhook.router, prefix="/line")
    test_app.dependency_overrides[webhook.get_line_event_handler] = lambda: handler
    with patch.object(webhook, "parser") as parser, patch.object(
        webhook, "_event_dedup", LineEventDedup(None)
    ):
        parser.parse.return_value = [_event("e1"), _event("e2")]
        # with 區塊讓事件迴圈活到區塊結束，背景 task 才有機會開始跑
        with TestClient(test_app) as client:
            response = client.post(
                "/line/callback",
                content=b'{"events":[]}',
                headers={"X-Line-Signature": "sig"},
            )
            assert response.status_code == 200
            assert response.text == '"OK"'
            # handler 永遠不會結束，但兩個事件都已經在背景開始跑（迴圈在另一個
            # 執行緒，等它排到 task 為止）
            deadline = time.monotonic() + 2
            while handler.handle.await_count < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert handler.handle.await_count == 2


def test_empty_channel_secret_fails_at_import():
    original = settings.LINE_CHANNEL_SECRET
    try:
        settings.LINE_CHANNEL_SECRET = ""
        with pytest.raises(RuntimeError, match="LINE_CHANNEL_SECRET"):
            importlib.reload(webhook)
    finally:
        settings.LINE_CHANNEL_SECRET = original
        importlib.reload(webhook)
