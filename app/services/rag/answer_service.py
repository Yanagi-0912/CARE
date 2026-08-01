from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate

from app.services.gemini import GeminiService
from app.services.rag.retriever import MongoAtlasVectorRetriever

RETRIEVAL_TOP_K = 10
CITE_TOP_K = 3
NO_HITS_MESSAGE = "知識庫中未找到相關資訊，請嘗試用不同方式描述問題。"
NO_ANSWER_MESSAGE = "目前無法提供相關資訊，請稍後再試或換一種方式描述問題。"
CANNOT_ANSWER_MARKERS: tuple[str, ...] = (
    "不知道",
    "無法",
    "未找到",
    "找不到相關",
)

RAG_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "human",
            "請根據以下提供的醫療知識內容回答問題。\n\n"
            "規則：\n"
            "1. 請在回答中適當引用內容來源的編號，例如：『...這是常見的症狀 [1]。』\n"
            "2. 回覆中不要使用「根據檢索內容」這類字眼，改用「根據 RAG 資訊」等說法。\n"
            "3. 請使用一般純文字，不要使用 Markdown 格式符號。\n"
            "4. 若內容不足，請明確說明不知道，勿捏造。\n\n"
            "使用者問題：{question}\n\n"
            "RAG 內容：\n"
            "{context}",
        )
    ]
)


class RagAnswerService:
    def __init__(
        self,
        gemini_service: GeminiService,
        retriever: MongoAtlasVectorRetriever,
    ) -> None:
        self.gemini_service = gemini_service
        self.retriever = retriever

    async def answer(self, user_text: str) -> str:
        docs = await self.retriever.ainvoke(user_text)
        if not docs:
            return NO_HITS_MESSAGE

        kb_answer = await self._generate_answer(user_text, docs)
        if self._is_cannot_answer(kb_answer):
            return NO_ANSWER_MESSAGE

        return self._append_sources(kb_answer, docs)

    async def _generate_answer(self, question: str, docs: list[Document]) -> str:
        context = "\n".join(
            f"{idx}. {doc.page_content}" for idx, doc in enumerate(docs, start=1)
        )
        messages = RAG_PROMPT.format_messages(question=question, context=context)
        rag_result = await self.gemini_service.chat_model.ainvoke(messages)
        answer_text = rag_result.content or "抱歉，我目前找不到相關資料，請稍後再試。"
        if not isinstance(answer_text, str):
            answer_text = str(answer_text)
        return answer_text

    @staticmethod
    def _is_cannot_answer(text: str) -> bool:
        normalized = (text or "").strip()
        if not normalized:
            return True
        return any(marker in normalized for marker in CANNOT_ANSWER_MARKERS)

    @staticmethod
    def _append_sources(answer_text: str, docs: list[Document]) -> str:
        source_lines: list[str] = []
        seen_urls: set[str] = set()

        for doc in docs:
            if len(source_lines) >= CITE_TOP_K:
                break
            source_name = str(doc.metadata.get("source_name") or "").strip()
            url = str(doc.metadata.get("url") or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            display_idx = len(source_lines) + 1
            if source_name:
                source_lines.append(f"[{display_idx}] {source_name}：{url}")
            else:
                source_lines.append(f"[{display_idx}] {url}")

        if not source_lines:
            return answer_text
        return f"{answer_text}\n\n參考資料來源：\n" + "\n".join(source_lines)
