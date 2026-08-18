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


class _StaticIdentityVerifier:
    """回傳固定的同一性判斷，並記錄每次呼叫收到的 (user_claim, checked_claim)，
    供斷言 service 傳的 checked_claim 是否正確做了 claim→title 的 fallback。"""

    def __init__(self, same: bool) -> None:
        self._same = same
        self.calls: list[tuple[str, str]] = []

    async def is_same_claim(self, user_claim: str, checked_claim: str) -> bool:
        self.calls.append((user_claim, checked_claim))
        return self._same


def _make_service(
    *,
    match: ClaimMatch | None,
    claim: str = _NORMALIZED_CLAIM,
    invoke_reasoning: AsyncMock | None = None,
    related_retriever: object | None = None,
    identity_verifier: object | None = None,
) -> ClaimVerificationService:
    return ClaimVerificationService(
        _StaticNormalizer(claim),
        _StaticMatcher(match),
        invoke_reasoning=invoke_reasoning,
        related_retriever=related_retriever,
        identity_verifier=identity_verifier,
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


# --- 主張同一性驗證（design.md 決策 9）：向量比對命中後，還要通過這道 LLM
# 驗證才採用該篇判定；三種可能值（True／False／None＝未配置）都要各自釘住。


@pytest.mark.asyncio
async def test_verify_adopts_match_when_identity_verifier_confirms_same_claim():
    """同一性驗證回 True：採用比對到的 verdict，行為與沒有這道驗證時相同。"""
    invoke_reasoning = AsyncMock(return_value="理由。")
    identity_verifier = _StaticIdentityVerifier(same=True)
    service = _make_service(
        match=_make_match("錯誤"),
        invoke_reasoning=invoke_reasoning,
        identity_verifier=identity_verifier,
    )

    result = await service.verify(_USER_TEXT)

    assert result.verdict == "錯誤"
    assert result.matched is True


@pytest.mark.asyncio
async def test_verify_degrades_to_not_enough_evidence_when_identity_verifier_says_different():
    """同一性驗證回 False：完全走未命中路徑——verdict、matched、related_info
    三者都要一起變，不能只改 verdict 卻漏改其他兩個欄位。"""
    docs = [Document(page_content="鳳梨酵素相關衛教資訊。")]
    identity_verifier = _StaticIdentityVerifier(same=False)
    service = _make_service(
        match=_make_match("錯誤"),
        identity_verifier=identity_verifier,
        related_retriever=_StaticRelatedRetriever(docs),
    )

    result = await service.verify(_USER_TEXT)

    assert result.verdict == NOT_ENOUGH_EVIDENCE
    assert result.matched is False
    assert "鳳梨酵素相關衛教資訊" in result.related_info


@pytest.mark.asyncio
async def test_verify_skips_identity_check_when_verifier_not_configured():
    """identity_verifier 為 None 是向後相容的預設值：跳過驗證、直接採用比對
    命中，行為與導入這道驗證之前完全相同（既有測試不必為此改寫）。"""
    invoke_reasoning = AsyncMock(return_value="理由。")
    service = _make_service(
        match=_make_match("錯誤"),
        invoke_reasoning=invoke_reasoning,
        identity_verifier=None,
    )

    result = await service.verify(_USER_TEXT)

    assert result.verdict == "錯誤"
    assert result.matched is True


@pytest.mark.asyncio
async def test_verify_falls_back_to_title_when_match_claim_is_blank():
    """知識庫的 claim 欄位有 35% 裝的是查核結論、部分文章甚至沒有 claim
    （design.md 決策 8／identity.py 介面說明）。傳給 verifier 的 checked_claim
    要退回 title，不能傳空字串——空字串幾乎必然被判為「不同主張」，等同
    每次都誤殺命中。這個 fallback 屬於 service 的職責，不在 verifier 裡。"""
    identity_verifier = _StaticIdentityVerifier(same=True)
    match = _make_match("錯誤", claim="")
    service = _make_service(match=match, identity_verifier=identity_verifier)

    await service.verify(_USER_TEXT)

    assert identity_verifier.calls == [(_NORMALIZED_CLAIM, match.title)]


class _MisconfiguredIdentityVerifier:
    """模擬 dependencies.py 忘記傳 gemini_service／invoke_identity 給
    GeminiClaimIdentityVerifier 時，真正會拋出的例外（見 identity.py）。"""

    async def is_same_claim(self, user_claim: str, checked_claim: str) -> bool:
        raise RuntimeError(
            "GeminiClaimIdentityVerifier requires gemini_service or invoke_identity"
        )


@pytest.mark.asyncio
async def test_verify_does_not_swallow_identity_verifier_misconfiguration():
    """service 層刻意不 catch identity_verifier 拋出的例外（見 verify() 內的
    註解）。這裡直接證明：接線疏漏會以例外原樣穿透 verify()，不會被再吞一次
    變成一個看似正常的「不同主張」結果——否則 dependencies.py 忘記注入
    gemini_service 這件事就永遠不會被任何人發現。"""
    service = _make_service(
        match=_make_match("錯誤"),
        identity_verifier=_MisconfiguredIdentityVerifier(),
    )

    with pytest.raises(RuntimeError):
        await service.verify(_USER_TEXT)
