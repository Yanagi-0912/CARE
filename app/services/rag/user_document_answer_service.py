"""Answer questions from user-uploaded document chunks."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.documents import Document

from app.i18n.messages import t
from app.services.gemini import GeminiService
from app.services.rag.answer_prompts import build_user_document_prompt

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
            question=question, context=context
        )
        result = await self.gemini_service.chat_model.ainvoke(messages)
        answer_text = result.content or t("rag.generate_fallback")
        if not isinstance(answer_text, str):
            answer_text = str(answer_text)
        return answer_text
