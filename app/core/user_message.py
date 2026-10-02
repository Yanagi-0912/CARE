"""本輪使用者原文，request-scoped ContextVar。

agent 呼叫 `get_rag_answer` 時給的 query 常是改寫過的關鍵字串（「抗生素 咖啡
交互作用」），複合問題的拆題需要使用者的原句才看得出有幾個問題、各帶哪些條件。
LangChain tool 的參數只有模型給的那一個 query，原句沒有別的路徑傳進去。

方向與 user_language 相同：由最外層（`Agent.invoke`）設定、tool 內讀取，子
context 自然繼承，直接 `.set()` 即可（與 rag_sources 反方向的 holder 寫法不同）。

只有 `get_rag_answer` 會把它明確傳給 RAG 服務；RAG 服務自己不讀這個 ContextVar。
藥單問答、主張查核送進 RAG 的查詢帶有附加內容（登記藥名、主張本身），若 RAG
服務自行讀原句去拆題，附加內容會在改寫中遺失。
"""

from __future__ import annotations

from contextvars import ContextVar, Token

_request_user_message: ContextVar[str | None] = ContextVar(
    "care_request_user_message",
    default=None,
)


def set_request_user_message(text: str | None) -> Token:
    return _request_user_message.set(text or None)


def reset_request_user_message(token: Token) -> None:
    _request_user_message.reset(token)


def get_request_user_message() -> str | None:
    """本輪使用者原文；不在 agent 請求內（排程、腳本）時為 None。"""
    return _request_user_message.get()
