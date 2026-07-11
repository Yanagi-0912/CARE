from langchain_core.tools import tool

_rag_answer_service = None


def configure_rag_tool(rag_answer_service) -> None:
    """DI 初始化時呼叫，注入 RagAnswerService 實例。"""
    global _rag_answer_service
    _rag_answer_service = rag_answer_service


@tool
async def get_rag_answer(query: str) -> str:
    """當問題需要引用內部醫療知識庫內容時呼叫。
    例如疾病照護建議、症狀處置原則、慢病管理等。
    """
    if _rag_answer_service is None:
        return "RAG 服務未初始化，請稍後再試。"
    return await _rag_answer_service.answer(query)
