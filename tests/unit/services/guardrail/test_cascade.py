"""串接式 guardrail：本地模型兩端有把握才自己判，其餘交給 LLM。"""

import pytest

from app.services.guardrail.cascade import CascadeGuardrailService


class _FakeLocal:
    def __init__(self, probability, *, low=0.2, high=0.6, recognized=True):
        self._probability = probability
        self._recognized = recognized
        self.low = low
        self.high = high

    def probability(self, _text):
        return self._probability

    def recognizes(self, _text):
        return self._recognized


class _FakeFallback:
    def __init__(self, answer):
        self.answer = answer
        self.calls: list[str] = []

    async def allow_rag_tool(self, text):
        self.calls.append(text)
        return self.answer


def _service(local, fallback_answer):
    fallback = _FakeFallback(fallback_answer)
    return CascadeGuardrailService(local=local, fallback=fallback), fallback


@pytest.mark.asyncio
async def test_confident_ends_are_decided_locally():
    allow, fallback = _service(_FakeLocal(0.9), fallback_answer=False)
    assert await allow.allow_rag_tool("高血壓要注意什麼") is True
    deny, fallback_deny = _service(_FakeLocal(0.05), fallback_answer=True)
    assert await deny.allow_rag_tool("今天天氣如何") is False
    assert fallback.calls == [] and fallback_deny.calls == []


@pytest.mark.asyncio
async def test_middle_band_escalates():
    service, fallback = _service(_FakeLocal(0.4), fallback_answer=True)
    assert await service.allow_rag_tool("x") is True
    assert fallback.calls == ["x"]


@pytest.mark.asyncio
async def test_unrecognized_text_escalates_even_above_high():
    # 2026-09-17：「恥笑漸漸光，咱就大聲仔想著煞」只認得「就大」一個片段，拿到
    # 0.6046 越過門檻被放行，後面被強制送去知識庫。
    service, fallback = _service(_FakeLocal(0.6046, high=0.5989, recognized=False), False)
    assert await service.allow_rag_tool("恥笑漸漸光，咱就大聲仔想著煞") is False
    assert len(fallback.calls) == 1


@pytest.mark.asyncio
async def test_location_message_is_denied_without_any_model():
    service, fallback = _service(_FakeLocal(0.99), fallback_answer=True)
    assert await service.allow_rag_tool("這是我的目前位置：lat=25.03, lng=121.56") is False
    assert fallback.calls == []
