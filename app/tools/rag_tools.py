from langchain_core.tools import tool

from app.core.user_message import get_request_user_message

_rag_answer_service = None


def configure_rag_tool(rag_answer_service) -> None:
    """DI 初始化時呼叫，注入 RagAnswerService 實例。"""
    global _rag_answer_service
    _rag_answer_service = rag_answer_service


@tool
async def get_rag_answer(query: str) -> str:
    """當問題需要引用醫療知識庫（必要時會補充允許網域的公開網路資料）時呼叫。
    例如疾病照護、症狀處置、慢病管理，以及醫療詐騙／假藥／可疑醫療訊息查證。
    """
    if _rag_answer_service is None:
        return "RAG 服務未初始化，請稍後再試。"
    # 原文只由這支工具明確傳入：複合問題拆題要看使用者原句（見 app/core/user_message.py）
    return await _rag_answer_service.answer(
        query, original_message=get_request_user_message()
    )
