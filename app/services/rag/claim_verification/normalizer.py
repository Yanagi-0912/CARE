"""查核判定卡：把使用者的查核型問句正規化成一句可查核的主張。

「網傳吃鳳梨心可以溶解血栓，是真的嗎？」裡的「網傳」「是真的嗎」對檢索是純噪音。
文獻（arXiv 2406.03239、2310.14338）量測過：正規化後的 claim 在 top-k
precision 上穩定優於原始問句，QA-based decontextualisation 讓
P@3/P@5/P@10 平均 +1.08。
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Protocol

from langchain_core.messages import HumanMessage

from app.services.gemini import GeminiService

logger = logging.getLogger(__name__)

CLAIM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "claim": {"type": "string"},
    },
    "required": ["claim"],
}


class ClaimNormalizer(Protocol):
    async def normalize(self, user_text: str) -> str: ...


class GeminiClaimNormalizer:
    """以 Gemini structured output，從查核型問句萃取出可查核的主張。"""

    def __init__(
        self,
        gemini_service: GeminiService | None = None,
        *,
        invoke_normalize: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
    ) -> None:
        self._gemini = gemini_service
        self._invoke_normalize = invoke_normalize

    async def normalize(self, user_text: str) -> str:
        stripped = (user_text or "").strip()
        if not stripped:
            # 空白輸入沒有主張可萃取，呼叫模型只是白花一次配額
            return user_text

        prompt = (
            "使用者傳來一句查核型問句，可能包含「網傳」「聽說」"
            "「是真的嗎」等與主張本身無關的包裝詞，也可能一次夾帶多個主張。\n"
            "請萃取其中最主要的一個主張，只輸出主張本身：去除包裝詞與疑問語氣，"
            "不要判斷真假、不要加任何說明。\n\n"
            f"使用者問句：{stripped}"
        )
        try:
            raw = await self._call(prompt)
            claim = raw.get("claim") if isinstance(raw, dict) else None
            claim = str(claim or "").strip()
        except Exception as exc:  # noqa: BLE001
            # fail-open：正規化只是檢索前的最佳化，失敗不能讓查核流程中斷，
            # 讓下游拿原問句繼續走即可。
            logger.warning(
                "claim normalization failed, falling back to raw text: %s",
                exc,
                exc_info=True,
            )
            return user_text

        return claim or user_text

    async def _call(self, prompt: str) -> dict[str, Any]:
        if self._invoke_normalize is not None:
            return await self._invoke_normalize(prompt)
        if self._gemini is None:
            raise RuntimeError(
                "GeminiClaimNormalizer requires gemini_service or invoke_normalize"
            )
        structured = self._gemini.chat_model.with_structured_output(
            CLAIM_SCHEMA,
            method="json_schema",
        )
        result = await structured.ainvoke([HumanMessage(content=prompt)])
        if not isinstance(result, dict):
            raise ValueError(f"unexpected claim payload: {type(result)}")
        return result
