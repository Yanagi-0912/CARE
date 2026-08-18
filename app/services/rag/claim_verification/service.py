"""查核判定卡：串接主張正規化與已查核比對，產出結構化查核結果。

這裡是「正規化 → 比對」這條管線收斂成單一輸出的地方，也是整個查核判定卡
功能中唯一容許「判定值」成形的地方。`openspec/changes/claim-verdict-card/
specs/claim-verification/spec.md` 明文要求判定 SHALL NOT 由語言模型產生：
命中時逐字照抄 `ClaimMatch.verdict`（TFC 記者查核、編輯複核、總編輯審視
後的人工結論），未命中固定為 `NOT_ENOUGH_EVIDENCE`。

這不只是實作慣例，型別上也切斷了模型影響判定的路：`_rewrite_reasoning`
回傳的是 `str`（一段理由文字），`identity_verifier.is_same_claim` 回傳的是
`bool`，`verify()` 裡沒有任何一行把這兩者指派給 `verdict`——`verdict`
只會來自 `match.verdict` 或 `NOT_ENOUGH_EVIDENCE` 這兩個來源，兩者在呼叫
語言模型之前就已經定案，語言模型的輸出無論寫了什麼、`is_same_claim` 回
True 還是 False，都沒有管道能回頭覆寫它——`is_same_claim` 唯一的效果是
決定要不要「採用」`match`（見 `identity.py` 模組 docstring），一旦不採用，
就完全走未命中那條早已定案為 `NOT_ENOUGH_EVIDENCE` 的路徑。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage

from app.services.gemini import GeminiService
from app.services.rag.claim_verification.identity import ClaimIdentityVerifier
from app.services.rag.claim_verification.matcher import ClaimMatch, ClaimMatcher
from app.services.rag.claim_verification.normalizer import ClaimNormalizer

logger = logging.getLogger(__name__)

NOT_ENOUGH_EVIDENCE = "證據不足"

# 未命中時的固定理由：只說明「TFC 沒查過」這個事實，不得讀起來像任何判定推論。
_NO_MATCH_REASONING = (
    "台灣事實查核中心目前沒有針對這則說法的查核報告，因此無法給出判定。"
    "以下提供資料庫中相關的衛教資訊供參考，其內容並非本次說法的查核依據。"
)

# 理由改寫失敗（例外或空回應）時的降級長度：只是避免整篇報告塞進卡片的
# 粗略上限，數字本身不影響正確性。
_REASONING_FALLBACK_LIMIT = 200

# 未命中時的相關衛教資訊最多取檢索結果前幾筆內容，避免卡片過長。
_RELATED_INFO_TOP_K = 3


@dataclass(frozen=True)
class VerificationResult:
    user_question: str  # 使用者原本的問句（正規化前）——判定卡要顯示這個
    verdict: str  # 五種之一；未命中固定為 NOT_ENOUGH_EVIDENCE
    reasoning: str  # 白話理由
    source_title: str  # 命中時為查核報告標題，未命中為空字串
    source_url: str  # 同上
    matched: bool  # 是否命中已查核主張
    related_info: str  # 未命中時的相關衛教資訊；命中時為空字串


class RelatedInfoRetriever(Protocol):
    """未命中時用來找相關衛教資訊的檢索器；鴨子型別對齊 `MongoAtlasVectorRetriever`。"""

    async def ainvoke(self, query: str) -> list[Document]: ...


class ClaimVerificationService:
    """把 `ClaimNormalizer` 與 `ClaimMatcher` 串成一次查核，回傳 `VerificationResult`。"""

    def __init__(
        self,
        normalizer: ClaimNormalizer,
        matcher: ClaimMatcher,
        gemini_service: GeminiService | None = None,
        *,
        invoke_reasoning: Callable[[str], Awaitable[str]] | None = None,
        related_retriever: RelatedInfoRetriever | None = None,
        identity_verifier: ClaimIdentityVerifier | None = None,
    ) -> None:
        self._normalizer = normalizer
        self._matcher = matcher
        self._gemini = gemini_service
        self._invoke_reasoning = invoke_reasoning
        self._related_retriever = related_retriever
        # None 時跳過同一性驗證、直接採用比對命中（向後相容既有行為與既有
        # 測試）。正式環境 SHALL NOT 省略這個參數：dependencies.py 必須實際
        # 注入已配置好的驗證器，否則向量誤配（design.md 決策 9 量到的 65%）
        # 會原樣回到線上——省略不會被任何呼叫端的型別或既有測試攔下來，
        # 純粹是接線紀律的問題。
        self._identity_verifier = identity_verifier

    async def verify(self, user_text: str) -> VerificationResult:
        claim = await self._normalizer.normalize(user_text)
        match = await self._matcher.match(claim)

        if match is not None and self._identity_verifier is not None:
            # 向量比對命中不代表同一主張（design.md 決策 9）。這裡刻意不
            # catch identity_verifier 拋出的例外：GeminiClaimIdentityVerifier
            # 對「呼叫失敗」已經自己 fail-closed 回 False，會不帶例外地走到
            # 下面的判斷；唯一還會逸散到這裡的例外，是它自己刻意選擇不吞的
            # 「兩個依賴都沒注入」（見 identity.py 模組 docstring）——那是
            # dependencies.py 的接線疏漏，應該讓它大聲失敗，不該在這裡被
            # 再吞一次變成又一層靜默降級。
            checked_claim = match.claim or match.title
            same = await self._identity_verifier.is_same_claim(claim, checked_claim)
            if not same:
                match = None

        if match is None:
            return VerificationResult(
                user_question=user_text,
                verdict=NOT_ENOUGH_EVIDENCE,
                reasoning=_NO_MATCH_REASONING,
                source_title="",
                source_url="",
                matched=False,
                related_info=await self._fetch_related_info(claim),
            )

        # verdict 在這裡已經定案（逐字取自 match）；下一行呼叫語言模型純粹是
        # 為了把報告內容潤成白話理由，其回傳值不會、也沒有管道能回頭改動上面這行。
        reasoning = await self._rewrite_reasoning(user_text, match)
        return VerificationResult(
            user_question=user_text,
            verdict=match.verdict,
            reasoning=reasoning,
            source_title=match.title,
            source_url=match.url,
            matched=True,
            related_info="",
        )

    async def _rewrite_reasoning(self, user_text: str, match: ClaimMatch) -> str:
        prompt = (
            "以下是台灣事實查核中心針對一則說法的查核報告內容，"
            "請用白話文改寫成 2-3 句理由，說明查核報告怎麼看待這則說法。\n"
            "只寫理由本身：不要下結論、不要重複或改寫判定字樣"
            "（例如「這是錯誤的」「這是正確的」「判定為⋯」），也不要加開場白或結尾。\n\n"
            f"使用者的問題：{user_text}\n"
            f"查核報告內容：{match.content}"
        )
        try:
            reasoning = await self._call_reasoning(prompt)
        except Exception as exc:  # noqa: BLE001
            # fail-open：理由只是潤飾用的白話文字，抓不到不能讓整次查核失敗，
            # 降級為報告原文摘要，使用者仍看得到查核依據。
            logger.warning(
                "reasoning rewrite failed, degrading to raw report excerpt: %s",
                exc,
                exc_info=True,
            )
            return _preview(match.content)

        reasoning = (reasoning or "").strip()
        return reasoning or _preview(match.content)

    async def _call_reasoning(self, prompt: str) -> str:
        if self._invoke_reasoning is not None:
            return await self._invoke_reasoning(prompt)
        if self._gemini is None:
            raise RuntimeError(
                "ClaimVerificationService requires gemini_service or invoke_reasoning"
            )
        # 純文字回應：理由不是結構化資料，刻意不套 with_structured_output 的
        # schema——那會讓「判定」看起來像是這次呼叫的合法輸出欄位之一。
        result = await self._gemini.chat_model.ainvoke([HumanMessage(content=prompt)])
        content = result.content
        return content if isinstance(content, str) else str(content)

    async def _fetch_related_info(self, claim: str) -> str:
        if self._related_retriever is None:
            return ""
        try:
            docs = await self._related_retriever.ainvoke(claim)
        except Exception as exc:  # noqa: BLE001
            # 對齊 matcher/normalizer 的 fail-open：相關資訊只是附加參考，
            # 不是判定依據，抓不到就留白，不能讓查核流程中斷。
            logger.warning(
                "related info retrieval failed, degrading to empty: %s",
                exc,
                exc_info=True,
            )
            return ""

        excerpts = [
            doc.page_content.strip()
            for doc in docs[:_RELATED_INFO_TOP_K]
            if doc.page_content and doc.page_content.strip()
        ]
        return "\n\n".join(excerpts)


def _preview(text: str, limit: int = _REASONING_FALLBACK_LIMIT) -> str:
    normalized = " ".join((text or "").split())
    return normalized[:limit]
