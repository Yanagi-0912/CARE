"""CRAG：檢索充足性分級（correct / ambiguous / incorrect）。"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Awaitable, Callable, Protocol

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage

from app.services.gemini import GeminiService

logger = logging.getLogger(__name__)

GRADE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "grade": {
            "type": "string",
            "enum": ["correct", "ambiguous", "incorrect"],
        }
    },
    "required": ["grade"],
}


class Grade(str, Enum):
    CORRECT = "correct"
    AMBIGUOUS = "ambiguous"
    INCORRECT = "incorrect"


class RetrievalGrader(Protocol):
    async def grade(self, query: str, docs: list[Document]) -> Grade: ...


def parse_grade(value: str) -> Grade:
    normalized = (value or "").strip().lower()
    try:
        return Grade(normalized)
    except ValueError as exc:
        raise ValueError(f"unknown grade: {value!r}") from exc


def _summarize_docs(docs: list[Document], *, max_chars_per_doc: int) -> str:
    parts: list[str] = []
    for idx, doc in enumerate(docs, start=1):
        text = (doc.page_content or "").strip().replace("\n", " ")
        if max_chars_per_doc > 0:
            text = text[:max_chars_per_doc]
        parts.append(f"{idx}. {text}")
    return "\n".join(parts)


class GeminiRetrievalGrader:
    """以 Gemini structured output 判斷檢索是否足以回答問題。"""

    def __init__(
        self,
        gemini_service: GeminiService | None = None,
        *,
        max_chars_per_doc: int = 400,
        invoke_grade: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
    ) -> None:
        self._gemini = gemini_service
        self._max_chars = max_chars_per_doc
        self._invoke_grade = invoke_grade

    async def grade(self, query: str, docs: list[Document]) -> Grade:
        summary = _summarize_docs(docs, max_chars_per_doc=self._max_chars)
        prompt = (
            "你是醫療知識庫檢索評分器。根據「使用者問題」與「檢索片段」，"
            "判斷這些片段是否足以回答問題。\n"
            "只輸出 grade：\n"
            "- correct：片段直接相關且足以回答重點\n"
            "- ambiguous：有關但資訊不足或含混，值得改寫查詢再試\n"
            "- incorrect：無關或明顯答不了\n\n"
            f"使用者問題：{query}\n\n"
            f"檢索片段：\n{summary or '(無)'}"
        )
        raw = await self._call(prompt)
        grade_value = raw.get("grade") if isinstance(raw, dict) else raw
        return parse_grade(str(grade_value))

    async def _call(self, prompt: str) -> dict[str, Any]:
        if self._invoke_grade is not None:
            return await self._invoke_grade(prompt)
        if self._gemini is None:
            raise RuntimeError("GeminiRetrievalGrader requires gemini_service or invoke_grade")
        structured = self._gemini.chat_model.with_structured_output(
            GRADE_SCHEMA,
            method="json_schema",
        )
        result = await structured.ainvoke([HumanMessage(content=prompt)])
        if not isinstance(result, dict):
            raise ValueError(f"unexpected grade payload: {type(result)}")
        return result
