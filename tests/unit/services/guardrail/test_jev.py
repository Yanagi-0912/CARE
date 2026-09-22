"""guardrail 升級那一步：先問 Jev，Jev 不能用時退回 Gemini。"""

import asyncio

import httpx
import pytest

from app.services.guardrail import jev
from app.services.guardrail.jev import JevGuardrailService


class _FakeFallback:
    def __init__(self, answer):
        self.answer = answer
        self.calls: list[str] = []

    async def allow_rag_tool(self, text):
        self.calls.append(text)
        return self.answer


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _noul(p):
    def handler(request):
        return httpx.Response(
            200,
            json={"model": "jev-1.13.0", "answers": {"health": {"type": "noul", "noul": p}}},
        )
    return handler


@pytest.mark.asyncio
@pytest.mark.parametrize("p, expected", [(0.93, True), (0.5, True), (0.07, False)])
async def test_threshold_decides_without_fallback(p, expected):
    fallback = _FakeFallback(not expected)
    service = JevGuardrailService(api_key="k", fallback=fallback, client=_client(_noul(p)))
    assert await service.allow_rag_tool("我血壓很高要掛哪科") is expected
    assert fallback.calls == []


@pytest.mark.asyncio
async def test_request_carries_key_pinned_model_and_message():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        seen["body"] = request.read()
        return _noul(0.9)(request)

    service = JevGuardrailService(api_key="k", fallback=_FakeFallback(False), client=_client(handler))
    await service.allow_rag_tool("頭暈")
    assert seen["auth"] == "Bearer k"
    body = httpx.Response(200, content=seen["body"]).json()
    assert body["model"] == "jev-1.13.0"
    assert body["state"] == {"message": "頭暈"}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 429, 500, 529])
async def test_http_error_falls_back_to_gemini(status):
    fallback = _FakeFallback(True)
    service = JevGuardrailService(
        api_key="k", fallback=fallback, client=_client(lambda r: httpx.Response(status))
    )
    assert await service.allow_rag_tool("x") is True
    assert fallback.calls == ["x"]


@pytest.mark.asyncio
async def test_timeout_falls_back_to_gemini(monkeypatch):
    monkeypatch.setattr(jev, "TIMEOUT_SECONDS", 0.01)

    async def slow(request):
        await asyncio.sleep(1)
        return _noul(0.9)(request)

    fallback = _FakeFallback(False)
    service = JevGuardrailService(api_key="k", fallback=fallback, client=_client(slow))
    assert await service.allow_rag_tool("x") is False
    assert fallback.calls == ["x"]


@pytest.mark.asyncio
async def test_missing_key_goes_straight_to_gemini():
    def never(request):
        raise AssertionError("沒有金鑰不該打 Jev")

    fallback = _FakeFallback(True)
    service = JevGuardrailService(api_key="", fallback=fallback, client=_client(never))
    assert await service.allow_rag_tool("x") is True
    assert fallback.calls == ["x"]


@pytest.mark.asyncio
async def test_location_message_is_denied_without_any_model():
    def never(request):
        raise AssertionError("位置訊息不該問模型")

    fallback = _FakeFallback(True)
    service = JevGuardrailService(api_key="k", fallback=fallback, client=_client(never))
    assert await service.allow_rag_tool("這是我的目前位置 lat=25.03") is False
    assert fallback.calls == []
