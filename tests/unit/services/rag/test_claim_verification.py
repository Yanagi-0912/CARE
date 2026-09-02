"""ClaimVerificationService 單元測試（依賴以 DI 注入，禁止 monkey patch）。"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.documents import Document

from app.services.rag.claim_verification.matcher import ClaimMatch
from app.services.rag.claim_verification.service import (
    NOT_ENOUGH_EVIDENCE,
    NOT_ENOUGH_EVIDENCE_SLUG,
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
async def test_verify_reasoning_degrades_to_neutral_sentence_when_rewrite_raises():
    """I2 finding：改寫失敗的 fallback 不得倒出報告原文——matcher 選到的
    chunk 系統性地是複述謠言的那段，直接摘要可能讀起來像在支持謠言。"""
    invoke_reasoning = AsyncMock(side_effect=RuntimeError("gemini timeout"))
    service = _make_service(
        match=_make_match("錯誤"), invoke_reasoning=invoke_reasoning
    )

    result = await service.verify(_USER_TEXT)

    assert result.reasoning == "完整查核說明請見下方來源連結。"
    assert "查核中心訪問多位醫師" not in result.reasoning


@pytest.mark.asyncio
async def test_verify_reasoning_degrades_to_neutral_sentence_when_rewrite_returns_blank():
    invoke_reasoning = AsyncMock(return_value="   ")
    service = _make_service(
        match=_make_match("錯誤"), invoke_reasoning=invoke_reasoning
    )

    result = await service.verify(_USER_TEXT)

    assert result.reasoning == "完整查核說明請見下方來源連結。"
    assert "查核中心訪問多位醫師" not in result.reasoning


@pytest.mark.asyncio
async def test_verify_degrades_when_neither_gemini_service_nor_invoke_reasoning_given():
    """比照 normalizer：兩個依賴都沒注入時，內部 RuntimeError 不得逸散到呼叫端。"""
    service = _make_service(match=_make_match("錯誤"))

    result = await service.verify(_USER_TEXT)

    assert result.verdict == "錯誤"
    assert result.reasoning == "完整查核說明請見下方來源連結。"


@pytest.mark.asyncio
async def test_reasoning_prompt_includes_verdict_as_stance_constraint():
    """I2 finding：理由改寫過去完全不知道判定是什麼，只能自己猜立場，容易
    寫成聽起來在附和謠言的中立轉述。這裡鎖住 verdict 字樣確實進了 prompt，
    讓模型知道該往哪個方向解釋，而不是自己猜。"""
    invoke_reasoning = AsyncMock(return_value="理由。")
    service = _make_service(
        match=_make_match("錯誤"), invoke_reasoning=invoke_reasoning
    )

    await service.verify(_USER_TEXT)

    prompt = invoke_reasoning.await_args.args[0]
    assert "錯誤" in prompt


@pytest.mark.asyncio
async def test_reasoning_prompt_does_not_leak_verdict_when_verdict_is_correct():
    """換一個不會與其他固定措辭字面重疊的判定值，確認立場約束確實是取自
    match.verdict 這個變數，而不是巧合命中 prompt 裡別的固定文字。"""
    invoke_reasoning = AsyncMock(return_value="理由。")
    service = _make_service(
        match=_make_match("事實釐清"), invoke_reasoning=invoke_reasoning
    )

    await service.verify(_USER_TEXT)

    prompt = invoke_reasoning.await_args.args[0]
    assert "事實釐清" in prompt


class _ListContentChatModel:
    """模擬 Gemini 在部分情境回傳 list-of-parts 而非純字串的 `.content`。"""

    def __init__(self, content: object) -> None:
        self._content = content

    async def ainvoke(self, messages):  # noqa: ANN001 - 對齊 chat_model 介面
        return SimpleNamespace(content=self._content)


class _GeminiServiceStub:
    """輕量替身：只需要 `.chat_model.ainvoke`，不需要真正的 GeminiService。"""

    def __init__(self, content: object) -> None:
        self.chat_model = _ListContentChatModel(content)


@pytest.mark.asyncio
async def test_reasoning_flattens_list_of_parts_content_instead_of_repr():
    """次要 finding 1：_call_reasoning 走 gemini_service（而非 invoke_reasoning
    這個 DI 捷徑）時，Gemini 的 `.content` 可能是 list-of-parts。舊寫法
    `str(content)` 會把 Python repr（例如「[{'type': 'text', ...}]」）整包
    印進理由段；改用與 agent.py 共用的 content_to_text 正確攤平。"""
    gemini = _GeminiServiceStub(
        content=[{"type": "text", "text": "查核報告確認此說法缺乏實證。"}]
    )
    service = ClaimVerificationService(
        _StaticNormalizer(),
        _StaticMatcher(_make_match("錯誤")),
        gemini_service=gemini,
    )

    result = await service.verify(_USER_TEXT)

    assert result.reasoning == "查核報告確認此說法缺乏實證。"
    assert "'type'" not in result.reasoning


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


# --- C1 finding：「相關衛教資訊」打的是與 matcher 相同的向量索引，未過濾時
# 幾乎必然把剛才被同一性驗證擋下的 TFC 查核報告本身撈回來，讓判定從呈現層
# 繞了回來。


@pytest.mark.asyncio
async def test_related_info_excludes_documents_with_verdict():
    """未命中已經是同一性驗證（或分數不足）擋下的結果；候選裡幾乎必然還
    包含剛才被擋下的那篇 TFC 報告本身——使用者的主張沒變，最相似的文件
    排序也不會變。這裡直接鎖住：帶 verdict 的文件不得進入 related_info。"""
    docs = [
        Document(
            page_content="查核中心訪問多位醫師，均表示無實證支持此說法。",
            metadata={"verdict": "錯誤", "url": "https://tfc.example/blocked"},
        ),
        Document(
            page_content="喝咖啡與骨質流失的實證回顧。",
            metadata={"verdict": None, "url": "https://edu.example/1"},
        ),
    ]
    service = _make_service(match=None, related_retriever=_StaticRelatedRetriever(docs))

    result = await service.verify("網傳喝咖啡會導致骨質疏鬆？")

    assert "查核中心訪問多位醫師" not in result.related_info
    assert "喝咖啡與骨質流失的實證回顧" in result.related_info


@pytest.mark.asyncio
async def test_related_info_dedupes_by_url_one_paragraph_per_article():
    docs = [
        Document(
            page_content="第一段（同一篇）。",
            metadata={"verdict": None, "url": "https://edu.example/1"},
        ),
        Document(
            page_content="第二段（同一篇，不該重複出現）。",
            metadata={"verdict": None, "url": "https://edu.example/1"},
        ),
        Document(
            page_content="另一篇的內容。",
            metadata={"verdict": None, "url": "https://edu.example/2"},
        ),
    ]
    service = _make_service(match=None, related_retriever=_StaticRelatedRetriever(docs))

    result = await service.verify("網傳喝咖啡會導致骨質疏鬆？")

    assert "第一段（同一篇）" in result.related_info
    assert "第二段（同一篇，不該重複出現）" not in result.related_info
    assert "另一篇的內容" in result.related_info


@pytest.mark.asyncio
async def test_related_info_includes_source_title():
    """未命中側過去是無來源、無標題的原始 chunk，與命中側「可獨立驗證」
    的呈現標準不一致——這裡鎖住來源標題確實被帶進 related_info。"""
    docs = [
        Document(
            page_content="咖啡因與骨密度的研究摘要。",
            metadata={
                "verdict": None,
                "url": "https://edu.example/1",
                "original_title": "咖啡與骨質疏鬆的迷思",
            },
        ),
    ]
    service = _make_service(match=None, related_retriever=_StaticRelatedRetriever(docs))

    result = await service.verify("網傳喝咖啡會導致骨質疏鬆？")

    assert "咖啡與骨質疏鬆的迷思" in result.related_info
    assert "咖啡因與骨密度的研究摘要" in result.related_info


@pytest.mark.asyncio
async def test_related_info_stops_at_top_k_after_filtering():
    """過濾與去重都要發生在「取前 3 筆」之前，而不是先切前 3 筆再過濾——
    否則排在前面的 TFC 文件會把真正的衛教資訊擠出候選名額之外。"""
    docs = [
        Document(
            page_content=f"應被排除的查核報告 {i}",
            metadata={"verdict": "錯誤", "url": f"https://tfc.example/{i}"},
        )
        for i in range(3)
    ] + [
        Document(
            page_content="真正的衛教資訊。",
            metadata={"verdict": None, "url": "https://edu.example/1"},
        )
    ]
    service = _make_service(match=None, related_retriever=_StaticRelatedRetriever(docs))

    result = await service.verify("網傳喝咖啡會導致骨質疏鬆？")

    assert "真正的衛教資訊" in result.related_info


class _NonListRelatedRetriever:
    """模擬 ainvoke 回傳非 list（介面變動、測試替身寫錯）。docs 的切片與
    迭代若還留在 try 外面，TypeError 會逸散出 verify()（次要 finding 2）。"""

    async def ainvoke(self, query: str):
        return None


@pytest.mark.asyncio
async def test_related_info_empty_when_retriever_returns_non_list():
    service = _make_service(match=None, related_retriever=_NonListRelatedRetriever())

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

    assert identity_verifier.calls == [(_USER_TEXT, match.title)]


@pytest.mark.asyncio
async def test_verify_checks_identity_against_user_text_not_normalized_claim():
    """I1 finding：identity 驗證過去比對的是 normalizer 的輸出，但卡片顯示
    的是 user_text 原文——兩者一旦因正規化漂移（例如否定詞被連同包裝詞一起
    剝除）而不同義，驗證比對到的就不是卡片實際顯示的那句話，形同沒擋。這裡
    用刻意不同的 normalized claim 與 user_text 鎖住：is_same_claim 收到的
    第一個引數必須是 user_text，不是 normalizer 的輸出。"""
    identity_verifier = _StaticIdentityVerifier(same=True)
    service = _make_service(
        match=_make_match("錯誤"),
        claim="這是刻意不同於 user_text 的正規化結果",
        identity_verifier=identity_verifier,
    )

    await service.verify(_USER_TEXT)

    assert identity_verifier.calls[0][0] == _USER_TEXT
    assert identity_verifier.calls[0][0] != "這是刻意不同於 user_text 的正規化結果"


@pytest.mark.asyncio
async def test_verify_combines_title_and_claim_for_identity_check_when_both_present():
    """I3 finding：決策 8 已用實測否定 claim 的可靠度（35% 裝的是結論句而非
    主張句），但過去 checked_claim 只在 claim 為空字串時才退回 title、claim
    有值時完全不看 title。這裡鎖住兩者都非空時，checked_claim 要同時包含
    title 與 claim，而不是只取 claim。"""
    identity_verifier = _StaticIdentityVerifier(same=True)
    match = _make_match("錯誤")  # _BASE_MATCH 的 title 與 claim 皆非空且不同
    service = _make_service(match=match, identity_verifier=identity_verifier)

    await service.verify(_USER_TEXT)

    checked_claim = identity_verifier.calls[0][1]
    assert match.title in checked_claim
    assert match.claim in checked_claim


# --- verdict_slug（I4 finding）：呈現層的配色表改以這個機器鍵為主要依據，
# service 層要在命中／未命中兩條路徑都正確填入，而不是留著預設空字串。


@pytest.mark.asyncio
async def test_verify_populates_verdict_slug_from_match_when_matched():
    invoke_reasoning = AsyncMock(return_value="理由。")
    match = _make_match("錯誤", verdict_slug="incorrect")
    service = _make_service(match=match, invoke_reasoning=invoke_reasoning)

    result = await service.verify(_USER_TEXT)

    assert result.verdict_slug == "incorrect"


@pytest.mark.asyncio
async def test_verify_populates_not_enough_evidence_slug_when_unmatched():
    service = _make_service(match=None)

    result = await service.verify(_USER_TEXT)

    assert result.verdict_slug == NOT_ENOUGH_EVIDENCE_SLUG


@pytest.mark.asyncio
async def test_verify_populates_not_enough_evidence_slug_when_identity_check_fails():
    identity_verifier = _StaticIdentityVerifier(same=False)
    service = _make_service(
        match=_make_match("錯誤"), identity_verifier=identity_verifier
    )

    result = await service.verify(_USER_TEXT)

    assert result.verdict_slug == NOT_ENOUGH_EVIDENCE_SLUG


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


# ── related_sources：未命中側的結構化出處 ────────────────────────────────


@pytest.mark.asyncio
async def test_related_info_carries_structured_sources():
    """未命中側的每一段都要能追回出處。label 沿用 RAG 答問路徑的規則
    （來源名優先、退回標題），index 由 1 起算並與段落順序一致。"""
    docs = [
        Document(
            page_content="咖啡因與骨密度的研究摘要。",
            metadata={
                "verdict": None,
                "url": "https://edu.example/1",
                "source_name": "食藥署闢謠專區",
                "original_title": "咖啡與骨質疏鬆的迷思",
            },
        ),
        Document(
            page_content="鈣質攝取建議。",
            metadata={
                "verdict": None,
                "url": "https://edu.example/2",
                "source_name": "衛福部闢謠網站",
                "original_title": "每日鈣質怎麼吃",
            },
        ),
    ]
    service = _make_service(match=None, related_retriever=_StaticRelatedRetriever(docs))

    result = await service.verify(_USER_TEXT)

    assert [(s.index, s.label, s.url) for s in result.related_sources] == [
        (1, "食藥署闢謠專區", "https://edu.example/1"),
        (2, "衛福部闢謠網站", "https://edu.example/2"),
    ]


@pytest.mark.asyncio
async def test_related_source_without_url_is_kept_not_dropped():
    """「食藥署公告」那批（scraper_api 的全站新聞稿 feed）上游結構上就沒有
    網址。rag-responses 要求缺 url 的來源仍須顯示，不得靜默丟棄——這裡鎖住
    它確實留在 related_sources 裡，url 為空字串而非整筆消失。"""
    docs = [
        Document(
            page_content="食藥署提醒民眾勿信偏方。",
            metadata={
                "verdict": None,
                "url": None,
                "source_name": "食藥署公告",
                "original_title": "偏方風險說明",
            },
        ),
    ]
    service = _make_service(match=None, related_retriever=_StaticRelatedRetriever(docs))

    result = await service.verify(_USER_TEXT)

    assert len(result.related_sources) == 1
    assert result.related_sources[0].label == "食藥署公告"
    assert result.related_sources[0].url == ""
    assert "食藥署提醒民眾勿信偏方。" in result.related_info


@pytest.mark.asyncio
async def test_related_info_dedupes_by_source_and_title_when_url_missing():
    """迴歸測試：舊版把整段去重包在 `if url:` 裡，沒有網址的來源因此完全
    不去重，同一篇的多個 chunk 可以佔滿全部三個名額——與 docstring 宣稱的
    「同一篇最多一段」相反，而沒有網址的正是 576 篇的一整個來源。"""
    docs = [
        Document(
            page_content="第一段（同一篇，無網址）。",
            metadata={
                "verdict": None,
                "url": "",
                "source_name": "食藥署公告",
                "original_title": "同一篇文章",
            },
        ),
        Document(
            page_content="第二段（同一篇，不該重複出現）。",
            metadata={
                "verdict": None,
                "url": "",
                "source_name": "食藥署公告",
                "original_title": "同一篇文章",
            },
        ),
        Document(
            page_content="另一篇的內容。",
            metadata={
                "verdict": None,
                "url": "",
                "source_name": "食藥署公告",
                "original_title": "另一篇文章",
            },
        ),
    ]
    service = _make_service(match=None, related_retriever=_StaticRelatedRetriever(docs))

    result = await service.verify(_USER_TEXT)

    assert "第一段（同一篇，無網址）" in result.related_info
    assert "第二段（同一篇，不該重複出現）" not in result.related_info
    assert "另一篇的內容" in result.related_info
    assert len(result.related_sources) == 2


@pytest.mark.asyncio
async def test_blank_chunk_does_not_consume_its_article_dedup_slot():
    """內容為空的 chunk 不該先佔掉那篇文章的去重名額，害同一篇真正有內容的
    下一段被當成重複而丟掉。"""
    docs = [
        Document(
            page_content="   ",
            metadata={
                "verdict": None,
                "url": "https://edu.example/1",
                "source_name": "食藥署闢謠專區",
            },
        ),
        Document(
            page_content="這一段才是真正的內容。",
            metadata={
                "verdict": None,
                "url": "https://edu.example/1",
                "source_name": "食藥署闢謠專區",
            },
        ),
    ]
    service = _make_service(match=None, related_retriever=_StaticRelatedRetriever(docs))

    result = await service.verify(_USER_TEXT)

    assert "這一段才是真正的內容。" in result.related_info
    assert len(result.related_sources) == 1


@pytest.mark.asyncio
async def test_matched_result_carries_no_related_sources():
    """命中側的來源走 source_url／source_note 那條路，related_sources 必須
    保持空——兩者互斥，否則卡片會同時出現查核報告與衛教來源兩組按鈕。"""
    service = _make_service(match=_make_match("錯誤"))

    result = await service.verify(_USER_TEXT)

    assert result.matched is True
    assert result.related_sources == ()


@pytest.mark.asyncio
async def test_related_sources_empty_when_retrieval_fails():
    service = _make_service(match=None, related_retriever=_FailingRelatedRetriever())

    result = await service.verify(_USER_TEXT)

    assert result.related_info == ""
    assert result.related_sources == ()


@pytest.mark.asyncio
async def test_worst_case_unmatched_card_stays_within_the_size_guard():
    """`_RELATED_INFO_TOP_K` 與判定卡的大小門檻是綁在一起的：取太多筆會讓
    卡片超過 `SAFE_BUBBLE_BYTES`，在無聲中退回純文字（退回不會有錯誤訊息，
    見 size_guard 模組 docstring）。

    這條測試把兩者的關係鎖住——最壞情況是每篇候選都是滿版 chunk
    （`chunk_size` 上限 500 字）加上長網址。實測取 3 筆時為 12,500 bytes，
    取 2 筆為 8,993 bytes；門檻 9,216。調高 TOP_K 會直接讓這條紅。
    """
    from app.services.line_messaging.flex.verdict_flex import build_verdict_flex
    from resources.flex_messages.size_guard import fits, wire_bytes

    docs = [
        Document(
            page_content="檸" * 500,
            metadata={
                "verdict": None,
                "source_name": "食藥署闢謠專區",
                "original_title": f"闢謠文章標題 {i}",
                "url": f"https://www.fda.gov.tw/TC/newsContent.aspx?cid=5049&id={31600 + i}",
            },
        )
        for i in range(5)
    ]
    service = _make_service(match=None, related_retriever=_StaticRelatedRetriever(docs))

    result = await service.verify(_USER_TEXT)
    bubble = build_verdict_flex(result).contents.to_dict()

    assert fits(bubble), (
        f"最壞情況的判定卡為 {wire_bytes(bubble):,} bytes，超過門檻——"
        "會在無聲中退回純文字。降低 _RELATED_INFO_TOP_K 或縮短區塊內容。"
    )
