"""RAG 失敗代碼：工具回傳與 log 共用，避免全部擠成同一句「無法提供」。"""

from __future__ import annotations

from app.core.user_language import get_request_language
from app.i18n.messages import t

RAG_ERR_PREFIX = "[RAG_ERR:"


class RagFailCode:
    """穩定代碼；改文案時勿改 code 字串。"""

    KB_EMPTY = "KB_EMPTY"  # 知識庫無命中，且未走／未開 web
    WEB_EMPTY = "WEB_EMPTY"  # 知識庫不足後，官方網搜仍無可用內容
    WEB_ERROR = "WEB_ERROR"  # 網搜服務失敗（逾時、5xx、連不上），不是查無資料
    WEB_RATE_LIMITED = "WEB_RATE_LIMITED"  # 網搜服務回 429：額度用完，稍後再問才有用
    MODEL_REFUSE = "MODEL_REFUSE"  # 有文件但模型判定無法回答
    TIMEOUT = "TIMEOUT"  # 整條管線超過總逾時（answer_service.DEFAULT_RAG_ANSWER_TIMEOUT_SECONDS）


_FAIL_CODE_TO_KEY: dict[str, str] = {
    RagFailCode.KB_EMPTY: "rag.fail.KB_EMPTY",
    RagFailCode.WEB_EMPTY: "rag.fail.WEB_EMPTY",
    RagFailCode.WEB_ERROR: "rag.fail.WEB_ERROR",
    RagFailCode.WEB_RATE_LIMITED: "rag.fail.WEB_RATE_LIMITED",
    RagFailCode.MODEL_REFUSE: "rag.fail.MODEL_REFUSE",
    RagFailCode.TIMEOUT: "rag.fail.TIMEOUT",
}


def rag_fail(code: str, language: str | None = None) -> str:
    key = _FAIL_CODE_TO_KEY.get(code) or _FAIL_CODE_TO_KEY[RagFailCode.MODEL_REFUSE]
    lang = language if language is not None else get_request_language()
    message = t(key, lang)
    return f"{RAG_ERR_PREFIX}{code}] {message}"


def is_rag_fail(text: str) -> bool:
    return (text or "").strip().startswith(RAG_ERR_PREFIX)


def parse_rag_fail_code(text: str) -> str | None:
    raw = (text or "").strip()
    if not raw.startswith(RAG_ERR_PREFIX):
        return None
    rest = raw[len(RAG_ERR_PREFIX) :]
    end = rest.find("]")
    if end <= 0:
        return None
    return rest[:end]


def rag_fail_user_text(text: str) -> str:
    """去掉 `[RAG_ERR:CODE]` 標記，只留給使用者看的那句。不是失敗訊息時原樣回傳。"""
    raw = (text or "").strip()
    if not raw.startswith(RAG_ERR_PREFIX):
        return raw
    end = raw.find("]")
    return raw[end + 1 :].strip() if end > 0 else raw


# 相容舊匯入名稱（語意對應最接近的代碼）
NO_HITS_MESSAGE = rag_fail(RagFailCode.KB_EMPTY)
NO_ANSWER_MESSAGE = rag_fail(RagFailCode.MODEL_REFUSE)
