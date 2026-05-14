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
