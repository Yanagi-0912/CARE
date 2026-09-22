"""症狀比對中間帶：先問 Jev，Jev 不能用時退回 Gemini。"""

import asyncio

import httpx
import pytest

from app.services.medical.symptom_classification import jev
from app.services.medical.symptom_classification.jev import JevSymptomChooser
from app.services.medical.symptom_classification.normalizer import SymptomNormalizer
from app.services.medical.symptom_classification.vector_index import build_index

CANDIDATES = ("關節痛", "關節疼痛", "下肢痛")


class _FakeFallback:
    def __init__(self, answer):
        self.answer = answer
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    async def __call__(self, text, candidates):
        self.calls.append((text, tuple(candidates)))
        return self.answer


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _choice(choice, probabilities):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {
                    "term": {
                        "type": "choice",
                        "choice": choice,
                        "confidence": 0.5,
                        "probabilities": probabilities,
                    }
                },
            },
        )
    return handler


@pytest.mark.asyncio
async def test_choice_is_accepted_even_when_probability_is_split_among_synonyms():
    """同義條目分票時 confidence 很低，但 p(UNKNOWN) 低就該採用。"""
    fallback = _FakeFallback("下肢痛")
    handler = _choice("關節疼痛", {"關節痛": 0.35, "關節疼痛": 0.39, "下肢痛": 0.22, "UNKNOWN": 0.04})
    chooser = JevSymptomChooser(api_key="k", client=_client(handler))
    assert await chooser.choose("膝蓋痛", CANDIDATES, fallback) == "關節疼痛"
    assert fallback.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "choice, p_unknown",
    [("關節痛", 0.1), ("關節痛", 0.3), ("UNKNOWN", 0.4)],
)
async def test_unknown_probability_at_threshold_returns_none(choice, p_unknown):
    fallback = _FakeFallback("關節痛")
    handler = _choice(choice, {"關節痛": 0.5, "UNKNOWN": p_unknown})
    chooser = JevSymptomChooser(api_key="k", client=_client(handler))
    assert await chooser.choose("好冷", CANDIDATES, fallback) is None
    assert fallback.calls == []


@pytest.mark.asyncio
async def test_request_carries_key_pinned_model_and_candidates():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        seen["body"] = request.read()
        return _choice("關節痛", {"關節痛": 0.9, "UNKNOWN": 0.01})(request)

    chooser = JevSymptomChooser(api_key="k", client=_client(handler))
    await chooser.choose("膝蓋痛", CANDIDATES, _FakeFallback(None))
    assert seen["auth"] == "Bearer k"
    body = httpx.Response(200, content=seen["body"]).json()
    assert body["model"] == "jev-1.13.0"
    assert body["state"] == {"symptom": "膝蓋痛"}
    question = body["questions"]["term"]
    assert question["type"] == "choice"
    assert set(question["criteria"]) == {*CANDIDATES, "UNKNOWN"}


@pytest.mark.asyncio
async def test_choice_outside_candidates_falls_back_to_gemini():
    """查表會靜默落空，所以集合外的值等同 Jev 失敗。"""
    fallback = _FakeFallback("關節痛")
    handler = _choice("腹痛", {"腹痛": 0.9, "UNKNOWN": 0.01})
    chooser = JevSymptomChooser(api_key="k", client=_client(handler))
    assert await chooser.choose("膝蓋痛", CANDIDATES, fallback) == "關節痛"
    assert fallback.calls == [("膝蓋痛", CANDIDATES)]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 429, 500, 529])
async def test_http_error_falls_back_to_gemini(status):
    fallback = _FakeFallback("關節痛")
    chooser = JevSymptomChooser(api_key="k", client=_client(lambda r: httpx.Response(status)))
    assert await chooser.choose("x", CANDIDATES, fallback) == "關節痛"
    assert fallback.calls == [("x", CANDIDATES)]


@pytest.mark.asyncio
async def test_timeout_falls_back_to_gemini(monkeypatch):
    monkeypatch.setattr(jev, "TIMEOUT_SECONDS", 0.01)

    async def slow(request):
        await asyncio.sleep(1)
        return _choice("關節痛", {"關節痛": 0.9, "UNKNOWN": 0.0})(request)

    fallback = _FakeFallback(None)
    chooser = JevSymptomChooser(api_key="k", client=_client(slow))
    assert await chooser.choose("x", CANDIDATES, fallback) is None
    assert fallback.calls == [("x", CANDIDATES)]


@pytest.mark.asyncio
async def test_missing_key_goes_straight_to_gemini():
    def never(request):
        raise AssertionError("沒有金鑰不該打 Jev")

    fallback = _FakeFallback("關節痛")
    chooser = JevSymptomChooser(api_key="", client=_client(never))
    assert await chooser.choose("x", CANDIDATES, fallback) == "關節痛"
    assert fallback.calls == [("x", CANDIDATES)]


# --- 接進 SymptomNormalizer ---------------------------------------------------

_TERMS = ("腹痛", "咳嗽", "青光眼", "高血壓")
_VECTORS = ([1.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, 1.0, 0], [0, 0, 0, 1.0])


def _normalizer(query_vector, *, chooser, invoke):
    async def embed(_text):
        return query_vector

    return SymptomNormalizer(
        table_terms=_TERMS,
        vector_index=build_index(_TERMS, _VECTORS, embedding_model="test-embedding-model"),
        embed_query=embed,
        invoke=invoke,
        top_k=2,
        chooser=chooser,
    )


@pytest.mark.asyncio
async def test_normalizer_mid_band_asks_jev_with_recalled_candidates_only():
    seen = {}

    def handler(request):
        seen["body"] = httpx.Response(200, content=request.read()).json()
        return _choice("青光眼", {"青光眼": 0.9, "高血壓": 0.05, "UNKNOWN": 0.05})(request)

    async def never(_prompt):
        raise AssertionError("Jev 有答案時不該問 Gemini")

    normalizer = _normalizer(
        [0.0, 0.0, 0.436, 0.900],
        chooser=JevSymptomChooser(api_key="k", client=_client(handler)),
        invoke=never,
    )
    assert await normalizer.resolve("眼壓高") == "青光眼"
    assert set(seen["body"]["questions"]["term"]["criteria"]) == {"高血壓", "青光眼", "UNKNOWN"}


@pytest.mark.asyncio
async def test_normalizer_falls_back_to_gemini_prompt_when_jev_fails():
    async def invoke(_prompt):
        return {"symptom": "青光眼"}

    normalizer = _normalizer(
        [0.0, 0.0, 0.436, 0.900],
        chooser=JevSymptomChooser(api_key="k", client=_client(lambda r: httpx.Response(500))),
        invoke=invoke,
    )
    assert await normalizer.resolve("眼壓高") == "青光眼"


@pytest.mark.asyncio
async def test_normalizer_high_and_low_scores_never_ask_jev():
    def never(request):
        raise AssertionError("中間帶以外不該問 Jev")

    async def no_llm(_prompt):
        raise AssertionError("中間帶以外不該問 LLM")

    chooser = JevSymptomChooser(api_key="k", client=_client(never))
    assert await _normalizer([1.0, 0, 0, 0], chooser=chooser, invoke=no_llm).resolve("肚子痛") == "腹痛"
    assert await _normalizer([1.0, 1.0, 1.0, 1.0], chooser=chooser, invoke=no_llm).resolve("天氣") is None


@pytest.mark.asyncio
async def test_full_table_fallback_still_uses_gemini_not_jev():
    """沒有索引時是全表 882 選 1，沒量過 Jev，照舊問 Gemini。"""

    def never(request):
        raise AssertionError("全表兜底不該問 Jev")

    async def invoke(_prompt):
        return {"symptom": "腹痛"}

    normalizer = SymptomNormalizer(
        table_terms=_TERMS,
        invoke=invoke,
        chooser=JevSymptomChooser(api_key="k", client=_client(never)),
    )
    assert await normalizer.resolve("肚子痛") == "腹痛"
