"""同一性驗證：先問 Jev，Jev 不能用時退回 Gemini 版。"""

import asyncio
import logging

import httpx
import pytest

from app.services.rag.claim_verification import identity_jev
from app.services.rag.claim_verification.identity_jev import JevClaimIdentityVerifier

_USER = "吃完柿子不能喝牛奶"
_CHECKED = "【錯誤】網傳「吃完柿子／柚子千萬别喝優酪乳，也不能吃香蕉，會中毒」？"
_LOGGER = "app.services.rag.claim_verification.identity_jev"


class _FakeFallback:
    def __init__(self, answer):
        self.answer = answer
        self.calls: list[tuple[str, str]] = []

    async def is_same_claim(self, user_claim, checked_claim):
        self.calls.append((user_claim, checked_claim))
        return self.answer


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _noul(p):
    def handler(request):
        return httpx.Response(
            200,
            json={"model": "jev-1.13.0", "answers": {"same": {"type": "noul", "noul": p}}},
        )
    return handler


@pytest.mark.asyncio
@pytest.mark.parametrize("p, expected", [(0.97, True), (0.8, True), (0.7, False), (0.05, False)])
async def test_threshold_decides_without_fallback(p, expected):
    fallback = _FakeFallback(not expected)
    verifier = JevClaimIdentityVerifier(api_key="k", fallback=fallback, client=_client(_noul(p)))
    assert await verifier.is_same_claim(_USER, _CHECKED) is expected
    assert fallback.calls == []


@pytest.mark.asyncio
async def test_request_carries_key_pinned_model_and_both_claims():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        seen["body"] = request.read()
        return _noul(0.9)(request)

    verifier = JevClaimIdentityVerifier(
        api_key="k", fallback=_FakeFallback(False), client=_client(handler)
    )
    await verifier.is_same_claim(_USER, _CHECKED)
    assert seen["auth"] == "Bearer k"
    body = httpx.Response(200, content=seen["body"]).json()
    assert body["model"] == "jev-1.13.0"
    assert body["state"] == {"user_claim": _USER, "report_claim": _CHECKED}
    assert set(body["questions"]) == {"same"}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 429, 500, 529])
async def test_http_error_falls_back_to_gemini(status):
    fallback = _FakeFallback(True)
    verifier = JevClaimIdentityVerifier(
        api_key="k", fallback=fallback, client=_client(lambda r: httpx.Response(status))
    )
    assert await verifier.is_same_claim(_USER, _CHECKED) is True
    assert fallback.calls == [(_USER, _CHECKED)]


@pytest.mark.asyncio
async def test_malformed_response_falls_back_to_gemini():
    """缺欄位不能被當成「不同」或「相同」——要換 Gemini 判。"""
    fallback = _FakeFallback(True)
    verifier = JevClaimIdentityVerifier(
        api_key="k", fallback=fallback,
        client=_client(lambda r: httpx.Response(200, json={"answers": {}})),
    )
    assert await verifier.is_same_claim(_USER, _CHECKED) is True
    assert fallback.calls == [(_USER, _CHECKED)]


@pytest.mark.asyncio
async def test_timeout_falls_back_to_gemini(monkeypatch):
    monkeypatch.setattr(identity_jev, "TIMEOUT_SECONDS", 0.01)

    async def slow(request):
        await asyncio.sleep(1)
        return _noul(0.99)(request)

    fallback = _FakeFallback(False)
    verifier = JevClaimIdentityVerifier(api_key="k", fallback=fallback, client=_client(slow))
    assert await verifier.is_same_claim(_USER, _CHECKED) is False
    assert fallback.calls == [(_USER, _CHECKED)]


@pytest.mark.asyncio
async def test_missing_key_goes_straight_to_gemini():
    def never(request):
        raise AssertionError("沒有金鑰不該打 Jev")

    fallback = _FakeFallback(True)
    verifier = JevClaimIdentityVerifier(api_key="", fallback=fallback, client=_client(never))
    assert await verifier.is_same_claim(_USER, _CHECKED) is True
    assert fallback.calls == [(_USER, _CHECKED)]


@pytest.mark.asyncio
async def test_logs_judgement_and_fallback_separately(caplog):
    """Jev 判「不同」與 Jev 掛掉要分得開；後者之後由 Gemini 自己記 claim_identity。"""
    judged = JevClaimIdentityVerifier(
        api_key="k", fallback=_FakeFallback(True), client=_client(_noul(0.3))
    )
    broken = JevClaimIdentityVerifier(
        api_key="k", fallback=_FakeFallback(True),
        client=_client(lambda r: httpx.Response(500)),
    )
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        await judged.is_same_claim(_USER, _CHECKED)
        await broken.is_same_claim(_USER, _CHECKED)

    stages = [r.getMessage() for r in caplog.records if r.name == _LOGGER]
    assert len(stages) == 2
    assert "stage=claim_identity_jev" in stages[0] and "outcome=different" in stages[0]
    assert "p=0.3" in stages[0]
    assert "outcome=fallback" in stages[1] and "error=HTTPStatusError" in stages[1]
