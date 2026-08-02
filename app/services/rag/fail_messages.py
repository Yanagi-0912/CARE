"""RAG 失敗代碼：工具回傳與 log 共用，避免全部擠成同一句「無法提供」。"""

from __future__ import annotations

RAG_ERR_PREFIX = "[RAG_ERR:"


class RagFailCode:
    """穩定代碼；改文案時勿改 code 字串。"""

    KB_EMPTY = "KB_EMPTY"  # 知識庫無命中，且未走／未開 web
    WEB_EMPTY = "WEB_EMPTY"  # 知識庫不足後，官方網搜仍無可用內容
    WEB_ERROR = "WEB_ERROR"  # web fallback 例外
    MODEL_REFUSE = "MODEL_REFUSE"  # 有文件但模型判定無法回答


_MESSAGES: dict[str, str] = {
    RagFailCode.KB_EMPTY: (
        "知識庫目前沒有與此問題相符的資料。請換個方式描述，或必要時就醫。"
    ),
    RagFailCode.WEB_EMPTY: (
        "知識庫與官方網站目前都找不到相符說明。請換個方式描述，或必要時就醫。"
    ),
    RagFailCode.WEB_ERROR: "查詢官方資料時暫時失敗，請稍後再試。",
    RagFailCode.MODEL_REFUSE: (
        "找到的資料不足以安全回答此問題。請換個方式描述，或必要時就醫。"
    ),
}


def rag_fail(code: str) -> str:
    message = _MESSAGES.get(code) or _MESSAGES[RagFailCode.MODEL_REFUSE]
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


# 相容舊匯入名稱（語意對應最接近的代碼）
NO_HITS_MESSAGE = rag_fail(RagFailCode.KB_EMPTY)
NO_ANSWER_MESSAGE = rag_fail(RagFailCode.MODEL_REFUSE)
