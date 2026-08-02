"""CRAG：模糊檢索時的單次 query 改寫。"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage

from app.services.gemini import GeminiService

REWRITE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"query": {"type": "string"}},
    "required": ["query"],
}


class QueryRewriter(Protocol):
    async def rewrite(self, query: str, docs: list[Document]) -> str: ...


class GeminiQueryRewriter:
    def __init__(
        self,
        gemini_service: GeminiService | None = None,
        *,
        max_chars_per_doc: int = 200,
        invoke_rewrite: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
    ) -> None:
        self._gemini = gemini_service
        self._max_chars = max_chars_per_doc
        self._invoke_rewrite = invoke_rewrite

    async def rewrite(self, query: str, docs: list[Document]) -> str:
        snippets: list[str] = []
        for doc in docs[:3]:
            text = (doc.page_content or "").strip().replace("\n", " ")
            snippets.append(text[: self._max_chars])
        prompt = (
            "使用者以醫療知識庫查詢，但目前檢索結果不夠清楚。"
            "請把問題改寫成更具體、更利於向量檢索的一句繁體中文問句。"
            "不要回答問題，只輸出改寫後的 query。\n\n"
            f"原始問題：{query}\n\n"
            f"目前片段摘要：\n" + ("\n".join(snippets) if snippets else "(無)")
        )
        raw = await self._call(prompt)
        rewritten = ""
        if isinstance(raw, dict):
            rewritten = str(raw.get("query") or "").strip()
        elif isinstance(raw, str):
            rewritten = raw.strip()
        return rewritten or query

    async def _call(self, prompt: str) -> dict[str, Any]:
        if self._invoke_rewrite is not None:
            return await self._invoke_rewrite(prompt)
        if self._gemini is None:
            raise RuntimeError("GeminiQueryRewriter requires gemini_service or invoke_rewrite")
        structured = self._gemini.chat_model.with_structured_output(
            REWRITE_SCHEMA,
            method="json_schema",
        )
        result = await structured.ainvoke([HumanMessage(content=prompt)])
        if not isinstance(result, dict):
            raise ValueError(f"unexpected rewrite payload: {type(result)}")
        return result
