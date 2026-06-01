from langchain_core.tools import tool
from app.services.rag.retrieval.errors import RagNoHitsError
from app.services.consultation.context import get_current_consultation_context

_rag_answer_service = None
_consultation_service = None


def configure_rag_tool(rag_answer_service, consultation_service=None) -> None:
    """DI 初始化時呼叫，注入 RagAnswerService 實例。"""
    global _rag_answer_service, _consultation_service
    _rag_answer_service = rag_answer_service
    _consultation_service = consultation_service


@tool
async def get_rag_answer(query: str) -> str:
    """當問題需要引用內部醫療知識庫內容時呼叫。
    例如疾病照護建議、症狀處置原則、慢病管理等。
    """
    if _rag_answer_service is None:
        return "RAG 服務未初始化，請稍後再試。"
    try:
        answer = await _rag_answer_service.answer(query)
    except RagNoHitsError:
        return "知識庫中未找到相關資訊，請嘗試用不同方式描述問題。"

    if _consultation_service is not None:
        ctx = get_current_consultation_context()
        if ctx is not None and ctx.line_id:
            await _consultation_service.record_assistant_message(
                f"以下為 RAG 回應：\n{answer}"
            )

    return answer
