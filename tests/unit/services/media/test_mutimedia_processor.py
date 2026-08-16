import asyncio
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from app.services.media.mutimedia_processor import MediaProcessorService

class FakeGetResponse:
    def __init__(self, headers=None, chunks=None, status_code=200):
        self.headers = headers or {}
        self._chunks = chunks or [b"abc"]
        self.status_code = status_code

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=8192):
        for c in self._chunks:
            yield c

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

class FakePostResponse:
    def __init__(self, headers=None, text="", payload=None, status_code=200):
        self.headers = headers or {}
        self.text = text
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        return None

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

@pytest.fixture
def svc():
    return MediaProcessorService()

@pytest.mark.asyncio
async def test_process_media_success_and_cleanup(svc, tmp_path):
    token_mgr = MagicMock()
    token_mgr.get_token.return_value = "t"
    with patch("app.services.media.mutimedia_processor.TMP_DIR", tmp_path), \
         patch(
             "app.dependencies.get_line_token_manager",
             return_value=token_mgr,
         ), \
         patch("app.services.media.mutimedia_processor.requests.get", return_value=FakeGetResponse(
             headers={"Content-Type": "image/jpeg", "Content-Length": "3"},
             chunks=[b"abc"],
         )), \
         patch.object(svc, "_extract_user_text_via_webhook", return_value="辨識結果"):
        out = await svc.process_media("mid", "image", user_id="U1")
        assert out == "辨識結果"
        assert len(list(tmp_path.glob("*"))) == 0

def test_download_rejects_bad_media_type(svc):
    with pytest.raises(ValueError, match="Unsupported media type"):
        svc._download_media_to_tmp("mid", "unknown")

def test_extract_json_user_text(svc, tmp_path):
    p = tmp_path / "a.jpg"
    p.write_bytes(b"x")
    with patch("app.services.media.mutimedia_processor.MEDIA_PARSE_WEBHOOK_URL", "https://x"), \
        patch("app.services.media.mutimedia_processor.requests.post", return_value=FakePostResponse(
             headers={"Content-Type": "application/json"},
             text='{"user_text":"hello"}',
             payload={"user_text": "hello"},
         )):
        assert svc._extract_user_text_via_webhook(p) == "hello"


@pytest.mark.asyncio
async def test_process_media_does_not_block_event_loop(svc, tmp_path):
    """底層 helper 是同步阻塞的，但 process_media 期間事件迴圈必須仍能推進。

    這是本檔案唯一真正的回歸守門員：helper 用的是同步 requests，一旦有人把
    `await asyncio.to_thread(...)` 改回直接呼叫，事件迴圈就會被鎖住最長
    WEBHOOK_TIMEOUT_SECONDS（120 秒），期間所有 LINE 訊息、LIFF API 與背景
    排程全部停擺，連 /health 都回不了。其他測試驗證的是回傳值，抓不到這件事。

    做法：讓 webhook 那一步同步睡 BLOCK_SECONDS，同時跑一個每 TICK 秒加一的
    計數器。事件迴圈沒被鎖住的話，計數器會在這段期間內持續前進。
    """
    BLOCK_SECONDS = 0.3
    TICK = 0.01

    ticks = 0

    async def _heartbeat() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(TICK)
            ticks += 1

    def _blocking_webhook(_path: Path) -> str:
        time.sleep(BLOCK_SECONDS)  # 模擬同步 requests.post 的阻塞
        return "辨識結果"

    token_mgr = MagicMock()
    token_mgr.get_token.return_value = "t"

    heartbeat = asyncio.create_task(_heartbeat())
    try:
        with patch("app.services.media.mutimedia_processor.TMP_DIR", tmp_path), \
             patch("app.dependencies.get_line_token_manager", return_value=token_mgr), \
             patch("app.services.media.mutimedia_processor.requests.get", return_value=FakeGetResponse(
                 headers={"Content-Type": "image/jpeg", "Content-Length": "3"},
                 chunks=[b"abc"],
             )), \
             patch.object(svc, "_extract_user_text_via_webhook", side_effect=_blocking_webhook):
            out = await svc.process_media("mid", "image", user_id="U1")
    finally:
        heartbeat.cancel()

    assert out == "辨識結果"
    # 阻塞期間至少該跑掉一半的 tick；保守取 1/3 以避免 CI 上的排程抖動誤判。
    assert ticks >= (BLOCK_SECONDS / TICK) / 3, (
        f"事件迴圈在 process_media 期間被阻塞：只前進了 {ticks} 個 tick"
    )
