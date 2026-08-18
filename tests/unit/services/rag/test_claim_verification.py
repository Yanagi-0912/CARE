"""ClaimVerificationService 單元測試（依賴以 DI 注入，禁止 monkey patch）。"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from langchain_core.documents import Document

from app.services.rag.claim_verification.matcher import ClaimMatch
from app.services.rag.claim_verification.service import (
    NOT_ENOUGH_EVIDENCE,
    ClaimVerificationService,
)

_USER_TEXT = "網傳吃鳳梨心可以溶解血栓，是真的嗎？"
_NORMALIZED_CLAIM = "吃鳳梨心可以溶解血栓"

_BASE_MATCH = ClaimMatch(
    claim=_NORMALIZED_CLAIM,
    verdict="錯誤",
    verdict_slug="incorrect",
    url="https://tfc.example/1",
    title="【錯誤】網傳吃鳳梨心可以溶解血栓？",
    content="查核中心訪問多位醫師，均表示無實證支持此說法，此為流傳已久的偏方迷思。",
    score=0.95,
)


def _make_match(verdict: str, **overrides: object) -> ClaimMatch:
    return replace(_BASE_MATCH, verdict=verdict, **overrides)


class _StaticNormalizer:
    """回傳固定 claim；用來確認 user_question 與正規化後的主張是兩回事。"""

    def __init__(self, claim: str = _NORMALIZED_CLAIM) -> None:
        self._claim = claim

    async def normalize(self, user_text: str) -> str:
        return self._claim


class _StaticMatcher:
    def __init__(self, match: ClaimMatch | None) -> None:
        self._match = match

    async def match(self, claim: str) -> ClaimMatch | None:
        return self._match


class _StaticRelatedRetriever:
    def __init__(self, docs: list[Document]) -> None:
        self._docs = docs

    async def ainvoke(self, query: str) -> list[Document]:
        return self._docs


class _FailingRelatedRetriever:
    async def ainvoke(self, query: str) -> list[Document]:
        raise RuntimeError("vector search unavailable")


def _make_service(
    *,
    match: ClaimMatch | None,
    claim: str = _NORMALIZED_CLAIM,
    invoke_reasoning: AsyncMock | None = None,
    related_retriever: object | None = None,
) -> ClaimVerificationService:
    return ClaimVerificationService(
        _StaticNormalizer(claim),
        _StaticMatcher(match),
        invoke_reasoning=invoke_reasoning,
        related_retriever=related_retriever,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "verdict", ["錯誤", "部分錯誤", "正確", "事實釐清", "證據不足"]
)
async def test_verify_copies_verdict_verbatim_from_match(verdict: str):
    """命中時判定逐字取自 ClaimMatch.verdict——五種可能值都要能原樣穿透。"""
    invoke_reasoning = AsyncMock(return_value="這是改寫後的白話理由。")
    service = _make_service(
        match=_make_match(verdict), invoke_reasoning=invoke_reasoning
    )

    result = await service.verify(_USER_TEXT)

    assert result.verdict == verdict
    assert result.matched is True


@pytest.mark.asyncio
async def test_verify_returns_not_enough_evidence_when_unmatched():
    service = _make_service(match=None)

    result = await service.verify(_USER_TEXT)

    assert result.verdict == NOT_ENOUGH_EVIDENCE
    assert result.matched is False


@pytest.mark.asyncio
async def test_verify_ignores_verdict_like_text_produced_by_llm():
    """核心規格：模型即使在理由裡宣稱別的判定，回傳的判定仍只認 matcher。"""
    invoke_reasoning = AsyncMock(
        return_value="這個說法完全正確，判定為『正確』，可以放心分享。"
    )
    service = _make_service(
        match=_make_match("錯誤"), invoke_reasoning=invoke_reasoning
    )

    result = await service.verify(_USER_TEXT)

    assert result.verdict == "錯誤"


@pytest.mark.asyncio
async def test_verify_reasoning_degrades_to_report_excerpt_when_rewrite_raises():
    invoke_reasoning = AsyncMock(side_effect=RuntimeError("gemini timeout"))
    service = _make_service(
        match=_make_match("錯誤"), invoke_reasoning=invoke_reasoning
    )

    result = await service.verify(_USER_TEXT)

    assert result.reasoning
    assert "查核中心訪問多位醫師" in result.reasoning


@pytest.mark.asyncio
async def test_verify_reasoning_degrades_to_report_excerpt_when_rewrite_returns_blank():
    invoke_reasoning = AsyncMock(return_value="   ")
    service = _make_service(
        match=_make_match("錯誤"), invoke_reasoning=invoke_reasoning
    )

    result = await service.verify(_USER_TEXT)

    assert "查核中心訪問多位醫師" in result.reasoning


@pytest.mark.asyncio
async def test_verify_degrades_when_neither_gemini_service_nor_invoke_reasoning_given():
    """比照 normalizer：兩個依賴都沒注入時，內部 RuntimeError 不得逸散到呼叫端。"""
    service = _make_service(match=_make_match("錯誤"))

    result = await service.verify(_USER_TEXT)

    assert result.verdict == "錯誤"
    assert "查核中心訪問多位醫師" in result.reasoning


@pytest.mark.asyncio
async def test_verify_user_question_is_raw_text_not_normalized_claim():
    invoke_reasoning = AsyncMock(return_value="理由。")
    service = _make_service(
        match=_make_match("錯誤"), invoke_reasoning=invoke_reasoning
    )

    result = await service.verify(_USER_TEXT)

    assert result.user_question == _USER_TEXT
    assert result.user_question != _NORMALIZED_CLAIM


@pytest.mark.asyncio
async def test_related_info_populated_when_unmatched():
    docs = [
        Document(page_content="喝咖啡與骨質流失的實證回顧。"),
        Document(page_content="鈣質攝取與骨密度的關聯衛教資訊。"),
    ]
    service = _make_service(match=None, related_retriever=_StaticRelatedRetriever(docs))

    result = await service.verify("網傳喝咖啡會導致骨質疏鬆？")

    assert "喝咖啡與骨質流失的實證回顧" in result.related_info


@pytest.mark.asyncio
async def test_related_info_empty_when_matched():
    """命中已查核主張時不附相關衛教資訊——那不是判定依據（design 決策 4）。"""
    docs = [Document(page_content="不應該出現在結果裡的內容")]
    invoke_reasoning = AsyncMock(return_value="理由。")
    service = _make_service(
        match=_make_match("錯誤"),
        invoke_reasoning=invoke_reasoning,
        related_retriever=_StaticRelatedRetriever(docs),
    )

    result = await service.verify(_USER_TEXT)

    assert result.related_info == ""


@pytest.mark.asyncio
async def test_related_info_empty_when_retriever_not_provided():
    service = _make_service(match=None, related_retriever=None)

    result = await service.verify("網傳喝咖啡會導致骨質疏鬆？")

    assert result.related_info == ""


@pytest.mark.asyncio
async def test_related_info_empty_when_retriever_raises():
    service = _make_service(match=None, related_retriever=_FailingRelatedRetriever())

    result = await service.verify("網傳喝咖啡會導致骨質疏鬆？")

    assert result.related_info == ""
