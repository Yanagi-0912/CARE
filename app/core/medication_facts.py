"""本輪藥單問答的結構化登記資料，request-scoped ContextVar。

`ask_about_my_medications` 是 LangChain tool，回傳型別只能是字串。答案裡的
「以下是 CARE 裡登記的資料：…」那幾行是程式從資料庫算出來的事實，呈現層要把
它們做成卡片上獨立的一塊，就得拿到「一行一項」的原始清單——從最終文字反解是
另一個坑（理由與 `rag_sources.SourceRef` 的 docstring 相同：文字裡的分隔符
與內容本身可能撞在一起）。

寫入分成兩個動作、holder 是可變 list，理由完全同 `app/core/rag_sources.py`：
tool 跑在 LangGraph 複製出來的 context 裡，在那裡呼叫 `ContextVar.set()` 只
改到副本，外層讀回來永遠是空的。必須由最外層 `begin_*` 建立 holder，tool 內
就地改寫。
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Optional, Sequence


@dataclass(frozen=True)
class MedicationFacts:
    """這一輪答案所依據的登記資料。

    `lines` 一行一項（藥名清單、今天的時段、晚了多久、離下一頓剩多久…），
    順序就是呈現順序，卡片用它。`block` 是同一份內容在**答案文字裡**的樣子
    （含開頭那句「以下是 CARE 裡登記的資料：」）——呈現層要把它從卡片本文裡
    整段拿掉，用原字串比對才不會留下一句沒有內容的引言。`target_name` 為 None
    代表問的是自己。
    """

    lines: tuple[str, ...] = field(default_factory=tuple)
    block: str = ""
    target_name: Optional[str] = None


_request_medication_facts: ContextVar[list[MedicationFacts] | None] = ContextVar(
    "care_request_medication_facts",
    default=None,
)


def begin_request_medication_facts() -> Token:
    """每輪開場：放一個新的空 holder，回傳供 finally 還原的 token。"""
    return _request_medication_facts.set([])


def set_request_medication_facts(
    lines: Sequence[str], block: str, target_name: Optional[str]
) -> None:
    """就地寫入本輪的登記資料。

    沒有 holder 時靜默忽略：非 LINE 入口（純 API、eval 腳本）不會開場，那條
    路徑本來就沒有呈現層要拿這份資料，為此拋例外只會讓整個回答失敗。
    """
    holder = _request_medication_facts.get()
    if holder is None:
        return
    holder[:] = [
        MedicationFacts(lines=tuple(lines), block=block, target_name=target_name)
    ]


def get_request_medication_facts() -> Optional[MedicationFacts]:
    """本輪的登記資料；這一輪沒有走藥單問答時回 None。"""
    holder = _request_medication_facts.get()
    return holder[0] if holder else None


def reset_request_medication_facts(token: Token) -> None:
    _request_medication_facts.reset(token)
