import pytest
import requests

from app.core.request_context import reset_request_id, set_request_id
from app.services.line_messaging.reply import remote_tts_service as remote_module
from app.services.line_messaging.reply.remote_tts_service import RemoteTTSService


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._payload


class RecordingPost:
    """DI 用的假 requests.post，記錄呼叫參數。"""

    def __init__(self, response: FakeResponse):
        self.response = response
        self.calls: list[dict] = []

    def __call__(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return self.response


async def test_sends_voice_settings_and_returns_the_public_url():
    post = RecordingPost(
        FakeResponse({"audio_url": "https://care.example/tts/tts_a.mp3", "duration_ms": 2345})
    )
    service = RemoteTTSService("http://care-tts:8000/", post=post)

    token = set_request_id("abc12345")
    try:
        result = await service.synthesize(
            "記得吃藥", language="nan-TW", voice_rate="slow", voice_gender="male"
        )
    finally:
        reset_request_id(token)

    assert result == (b"", "https://care.example/tts/tts_a.mp3", 2345)
    assert post.calls == [
        {
            "url": "http://care-tts:8000/internal/synthesize",
            "json": {
                "text": "記得吃藥",
                "language": "nan-TW",
                "voice_rate": "slow",
                "voice_gender": "male",
            },
            # 兩個 pod 的 log 靠同一個 rid 串起來
            "headers": {"X-Request-ID": "abc12345"},
            "timeout": (
                remote_module.CONNECT_TIMEOUT_SECONDS,
                remote_module.READ_TIMEOUT_SECONDS,
            ),
        }
    ]


def test_read_timeout_is_not_shorter_than_the_slowest_local_path():
    """搬出去之前本地合成沒有整體逾時；遠端的上限至少要涵蓋 care-tts 內各段逾時加總。"""
    from app.services.line_messaging.reply import tts_service
    from app.services.speech import taigi_client, taigi_text

    slowest = (
        taigi_text.TAIGI_TEXT_TIMEOUT_SECONDS
        + taigi_client.TTS_TIMEOUT_SECONDS
        + tts_service.EDGE_TTS_CONNECT_TIMEOUT_SECONDS
        + tts_service.EDGE_TTS_RECEIVE_TIMEOUT_SECONDS
    )
    assert remote_module.READ_TIMEOUT_SECONDS >= slowest


async def test_http_error_propagates_so_the_reply_falls_back_to_text():
    service = RemoteTTSService(
        "http://care-tts:8000", post=RecordingPost(FakeResponse({}, status_code=502))
    )

    with pytest.raises(requests.HTTPError):
        await service.synthesize("記得吃藥")


@pytest.mark.parametrize(
    "payload",
    [{}, {"audio_url": ""}, {"audio_url": "app_data/tmp/tts_a.mp3", "duration_ms": 1000}],
    ids=["missing", "empty", "local-path"],
)
async def test_response_without_a_public_url_is_an_error(payload):
    service = RemoteTTSService(
        "http://care-tts:8000", post=RecordingPost(FakeResponse(payload))
    )

    with pytest.raises(RuntimeError, match="audio_url"):
        await service.synthesize("記得吃藥")
