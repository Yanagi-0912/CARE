"""Answer questions from user-uploaded document chunks."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.documents import Document

from app.i18n.messages import t
from app.services.gemini import GeminiService
from app.services.gemini.shared.parser import content_to_text
from app.services.rag.answer_prompts import (
    build_user_document_prompt,
    wrap_context,
)

logger = logging.getLogger(__name__)

NO_DOCS_MESSAGE = "目前沒有可查閱的上傳文件，請先上傳 PDF 或檔案後再詢問。"


class UserDocumentAnswerService:
    def __init__(
        self,
        gemini_service: GeminiService,
        retriever: Any,
    ) -> None:
        self.gemini_service = gemini_service
        self.retriever = retriever

    async def answer(self, line_user_id: str, query: str) -> str:
        docs = await self.retriever.ainvoke(query, line_user_id=line_user_id)
        if not docs:
            return NO_DOCS_MESSAGE
        return await self._generate_answer(query, docs)

    async def _generate_answer(self, question: str, docs: list[Document]) -> str:
        context_lines: list[str] = []
        for idx, doc in enumerate(docs, start=1):
            source_name = str(doc.metadata.get("source_name") or "").strip()
            document_id = str(doc.metadata.get("document_id") or "").strip()
            heading_parts = [f"[{idx}]"]
            if source_name:
                heading_parts.append(source_name)
            elif document_id:
                heading_parts.append(document_id)
            heading = " ".join(heading_parts)
            context_lines.append(f"{heading}\n{doc.page_content}")

        context = "\n\n".join(context_lines)
        messages = build_user_document_prompt().format_messages(
            question=question, context=wrap_context(context)
        )
        result = await self.gemini_service.chat_model.ainvoke(messages)
        # `content_to_text` 而非 `str()`：Gemini 開著 thinking 時 `.content`
        # 回的是 list-of-parts（`[{"type": "text", "text": "…", "extras":
        # {"signature": "<數千字 base64>"}}]`），`str()` 會把整個 Python
        # repr 連同簽章一起變成「答案」。實測一則 400 字的衛教回覆會被包成
        # 4,600~7,000 字，之後全程當作答案文字傳遞——進 agent 的 context、
        # 進引用解析、也會進卡片。
        answer_text = content_to_text(result.content) or t("rag.generate_fallback")
        return answer_text
