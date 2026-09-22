"""CRAG 分級：先問 Jev，Jev 不能用時退回 Gemini。"""

import asyncio

import httpx
import pytest
from langchain_core.documents import Document

from app.services.rag import jev_grader
from app.services.rag.jev_grader import JevRetrievalGrader, grade_from_probabilities
from app.services.rag.retrieval_grader import Grade

DOCS = [
    Document(page_content="高血壓患者應減少鹽分攝取。\n每日鈉不超過 2400 毫克。", metadata={"original_title": "限鹽"}),
    Document(page_content="x" * 1000, metadata={}),
]


class _FakeFallback:
    def __init__(self, answer):
        self.answer = answer
        self.calls: list[str] = []

    async def grade(self, query, docs):
        self.calls.append(query)
        return self.answer


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _choice(correct, ambiguous, incorrect):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {
                    "grade": {
                        "type": "choice",
                        "choice": "correct",
                        "probabilities": {
                            "correct": correct, "ambiguous": ambiguous, "incorrect": incorrect,
                        },
                    }
                },
            },
        )
    return handler


@pytest.mark.parametrize(
    "probs, expected",
    [
        ((0.95, 0.04, 0.01), Grade.CORRECT),
        ((0.8, 0.15, 0.05), Grade.CORRECT),
        # argmax 會判 correct，但沒過門檻：Jev 在 argmax 下比 Gemini 寬鬆。
        ((0.7, 0.2, 0.1), Grade.AMBIGUOUS),
        ((0.3, 0.2, 0.5), Grade.INCORRECT),
    ],
)
def test_threshold_on_p_correct(probs, expected):
    c, a, i = probs
    assert grade_from_probabilities({"correct": c, "ambiguous": a, "incorrect": i}) is expected


@pytest.mark.asyncio
async def test_jev_decides_without_fallback():
    fallback = _FakeFallback(Grade.INCORRECT)
    grader = JevRetrievalGrader(api_key="k", fallback=fallback, client=_client(_choice(0.9, 0.08, 0.02)))
    assert await grader.grade("高血壓要注意什麼", DOCS) is Grade.CORRECT
    assert fallback.calls == []


@pytest.mark.asyncio
async def test_request_carries_key_pinned_model_and_truncated_docs():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        seen["body"] = request.read()
        return _choice(0.9, 0.05, 0.05)(request)

    grader = JevRetrievalGrader(api_key="k", fallback=_FakeFallback(Grade.INCORRECT), client=_client(handler))
    await grader.grade("高血壓要注意什麼", DOCS)
    assert seen["auth"] == "Bearer k"
    body = httpx.Response(200, content=seen["body"]).json()
    assert body["model"] == "jev-1.13.0"
    assert body["questions"]["grade"]["type"] == "choice"
    assert body["state"]["query"] == "高血壓要注意什麼"
    first, second = body["state"]["documents"]
    assert first == {"title": "限鹽", "text": "高血壓患者應減少鹽分攝取。 每日鈉不超過 2400 毫克。"}
    assert second["title"] == ""
    assert len(second["text"]) == jev_grader.MAX_CHARS_PER_DOC


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 429, 500, 529])
async def test_http_error_falls_back_to_gemini(status):
    fallback = _FakeFallback(Grade.AMBIGUOUS)
    grader = JevRetrievalGrader(
        api_key="k", fallback=fallback, client=_client(lambda r: httpx.Response(status))
    )
    assert await grader.grade("q", DOCS) is Grade.AMBIGUOUS
    assert fallback.calls == ["q"]


@pytest.mark.asyncio
async def test_malformed_response_falls_back_to_gemini():
    fallback = _FakeFallback(Grade.CORRECT)
    grader = JevRetrievalGrader(
        api_key="k", fallback=fallback,
        client=_client(lambda r: httpx.Response(200, json={"answers": {}})),
    )
    assert await grader.grade("q", DOCS) is Grade.CORRECT
    assert fallback.calls == ["q"]


@pytest.mark.asyncio
async def test_timeout_falls_back_to_gemini(monkeypatch):
    monkeypatch.setattr(jev_grader, "TIMEOUT_SECONDS", 0.01)

    async def slow(request):
        await asyncio.sleep(1)
        return _choice(0.9, 0.05, 0.05)(request)

    fallback = _FakeFallback(Grade.INCORRECT)
    grader = JevRetrievalGrader(api_key="k", fallback=fallback, client=_client(slow))
    assert await grader.grade("q", DOCS) is Grade.INCORRECT
    assert fallback.calls == ["q"]


@pytest.mark.asyncio
async def test_missing_key_goes_straight_to_gemini():
    def never(request):
        raise AssertionError("沒有金鑰不該打 Jev")

    fallback = _FakeFallback(Grade.CORRECT)
    grader = JevRetrievalGrader(api_key="", fallback=fallback, client=_client(never))
    assert await grader.grade("q", DOCS) is Grade.CORRECT
    assert fallback.calls == ["q"]
