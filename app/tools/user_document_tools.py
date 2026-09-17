from langchain_core.tools import tool

from app.core.request_context import get_line_user_id
from app.core.user_language import SUPPORTED_LANGUAGES
from app.i18n.messages import t
from app.services.rag.user_document_answer_service import NO_DOCS_MESSAGE

SERVICE_UNAVAILABLE_MESSAGE = "上傳文件問答服務未初始化，請稍後再試。"
UNKNOWN_USER_MESSAGE = "無法取得使用者身分，請稍後再試。"

# 模型回空時 UserDocumentAnswerService 回的是 t("rag.generate_fallback")，依使用者
# 語言有六種。以前這裡沒列它，「抱歉，我目前找不到相關資料」就被當成文件答案、
# 做成一張文件卡送出。
_GENERATION_FALLBACKS: frozenset[str] = frozenset(
    t("rag.generate_fallback", lang) for lang in SUPPORTED_LANGUAGES
)


def is_document_answer_unavailable(text: str | None) -> bool:
    """這段工具輸出是不是「沒有內容可呈現」。

    上傳文件問答沒有走 fail_messages 的 [RAG_ERR:] 前綴機制（那是知識庫
    RAG 專用的），因此改以列舉固定訊息判斷：本模組的兩句、服務的
    NO_DOCS_MESSAGE、以及生成回空時的六語 generate_fallback。列舉而非模糊
    比對：這些字串是本模組、UserDocumentAnswerService 與 i18n 自己產生的，
    不是外部輸入。
    """
    stripped = (text or "").strip()
    if not stripped:
        return True
    if stripped in _GENERATION_FALLBACKS:
        return True
    return stripped in {
        NO_DOCS_MESSAGE,
        SERVICE_UNAVAILABLE_MESSAGE,
        UNKNOWN_USER_MESSAGE,
    }

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
        return SERVICE_UNAVAILABLE_MESSAGE

    line_user_id = get_line_user_id()
    if not line_user_id:
        return UNKNOWN_USER_MESSAGE

    return await _user_document_answer_service.answer(line_user_id, query)
