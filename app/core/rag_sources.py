"""本輪 RAG 回答的結構化參考來源，request-scoped ContextVar。

`get_rag_answer` 是 LangChain tool，回傳型別只能是字串，結構化資料沒有別的
路徑傳到呈現層。若改從最終文字反解 `[1] 食藥署：https://...`，分隔符是全形
冒號、而來源名本身也可能含冒號，解析很脆；`_append_sources` 內部本來就握有
編號與對應的 Document，直接在那裡取 metadata 可靠得多。

ContextVar 裡放的是一個**可變的 list**，不是每次覆寫的 tuple——這點與
user_language、user_font_size 不同，因為傳遞方向相反。那兩者由最外層設定、
tool 內讀取，父 context 的值本來就會被子 context 繼承；來源是反過來的，由
tool 寫、最外層讀。而 `get_rag_answer` 由 LangGraph 的 ToolNode 執行，節點
跑在 copy 出來的 context 裡，在那裡呼叫 `ContextVar.set()` 只改到副本，外層
讀回來的永遠是設定前的值（實測：tool 內 set 後，圖跑完外層仍是空的），來源
按鈕因此一顆都不會出現。子 context 繼承的是同一個 list 物件參照，就地改寫
（`holder[:] = ...`）外層才看得見。

因此寫入分成兩個動作：`begin_request_rag_sources` 由請求最外層呼叫，建立
holder；`set_request_rag_sources` 在 tool 內就地覆寫內容，不得改用 `.set()`。
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


_request_rag_sources: ContextVar[list[SourceRef] | None] = ContextVar(
    "care_request_rag_sources",
    default=None,
)


def begin_request_rag_sources() -> Token:
    """每輪開場：放一個新的空 holder，回傳供 finally 還原的 token。

    必須由請求最外層（tool 執行之前的 context）呼叫，tool 那一層才改得到
    同一個 list 物件。每輪換新的 list，上一輪的來源不會殘留成這一輪卡片上
    不屬於這個問題的按鈕。
    """
    return _request_rag_sources.set([])


def set_request_rag_sources(sources: Iterable[SourceRef]) -> None:
    """就地覆寫本輪來源。

    沒有 holder 時靜默忽略：非 LINE 入口（純 API 呼叫、eval 腳本）不會開場，
    這條路徑本來就沒有呈現層要拿來源，為此拋例外只會讓 RAG 整個失敗。
    """
    holder = _request_rag_sources.get()
    if holder is None:
        return
    holder[:] = list(sources)


def get_request_rag_sources() -> tuple[SourceRef, ...]:
    """回傳本輪來源的不可變快照。"""
    holder = _request_rag_sources.get()
    return tuple(holder) if holder else ()


def reset_request_rag_sources(token: Token) -> None:
    _request_rag_sources.reset(token)
