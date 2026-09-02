"""本輪 RAG 回答的結構化參考來源，request-scoped ContextVar。

與 user_language、user_font_size 採同一套模式，理由也相同：`get_rag_answer`
是 LangChain tool，回傳型別只能是字串，結構化資料沒有別的路徑傳到呈現層。

呈現層要把來源做成可點的 URI action 按鈕，需要 (label, url)。若改從最終
文字反解 `[1] 食藥署：https://...`，分隔符是全形冒號、而來源名本身也可能
含冒號，解析很脆；`_append_sources` 內部本來就握有編號與對應的 Document，
直接在那裡取 metadata 可靠得多。
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class SourceRef:
    """一筆參考來源。

    index 是重編號後的顯示編號，必須與文字清單中的 [n] 完全一致——答案本文
    裡的引用標記指的就是這個編號，兩者漂移會讓使用者點錯來源。

    url 可能為空字串：rag-responses 明文要求缺少 url 的來源仍須顯示（以
    「來源名｜標題」呈現），不得靜默丟棄。呈現層負責決定空 url 時不產生按鈕。
    """

    index: int
    label: str
    url: str


_request_rag_sources: ContextVar[tuple[SourceRef, ...]] = ContextVar(
    "care_request_rag_sources",
    default=(),
)


def get_request_rag_sources() -> tuple[SourceRef, ...]:
    return _request_rag_sources.get()


def set_request_rag_sources(sources: Iterable[SourceRef]) -> Token:
    return _request_rag_sources.set(tuple(sources))


def reset_request_rag_sources(token: Token) -> None:
    _request_rag_sources.reset(token)
