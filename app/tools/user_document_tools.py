from langchain_core.tools import tool

from app.core.request_context import get_line_user_id

_user_document_answer_service = None


def configure_user_document_tool(user_document_answer_service) -> None:
    """DI 初始化時呼叫，注入 UserDocumentAnswerService 實例。"""
    global _user_document_answer_service
    _user_document_answer_service = user_document_answer_service


@tool
async def answer_from_uploaded_document(query: str) -> str:
    """當使用者詢問先前上傳的 PDF 或檔案內容時呼叫。
    僅檢索該使用者未過期的上傳文件，不查詢官方知識庫。
    """
    if _user_document_answer_service is None:
        return "上傳文件問答服務未初始化，請稍後再試。"

    line_user_id = get_line_user_id()
    if not line_user_id:
        return "無法取得使用者身分，請稍後再試。"

    return await _user_document_answer_service.answer(line_user_id, query)
