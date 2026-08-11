"""
座標進來時的科別抽取：別名表查不到，但字面上確實指名了某一科的情形。

存在的理由見 nodes.py 的 _looks_like_department_mention 註解——這一層只判斷
「有沒有指名」，不判斷「是哪一科」，後者交給 medical_service 的兩層解析。
"""

import pytest
from langchain_core.messages import HumanMessage

from app.services.agent.utils.nodes import (
    _extract_department_from_history,
    _looks_like_department_mention,
)

SHARED_LOCATION = "這是我的目前位置：lat=24.7936, lng=121.0203"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 表裡沒有、但明確指名了某一科 → 原樣往下傳
        ("附近有腹腔鏡科嗎", "腹腔鏡科"),
        ("我要看腹腔鏡科", "腹腔鏡科"),
        ("請問哪裡有睡眠障礙科", "睡眠障礙科"),
        ("附近有沒有戒菸門診科", "戒菸門診科"),
        ("幫我找疼痛科門診", "疼痛科"),
        # 語助詞不可被吃進科別名稱裡
        ("我想看大腸科醫院", "大腸科"),
        ("大醫院的腸胃科", "腸胃科"),
        ("職業醫學科在哪", "職業醫學科"),
        # 科別名稱本身含「家」「學」，不可被切斷
        ("我想找家醫科", "家醫科"),
        # 連續兩個科不可黏成一個詞
        ("婦產科內科都可以", "婦產科"),
        # 字面有「科」但不是在指名科別
        ("附近有理科補習班嗎", None),
        ("他大學讀理科", None),
        ("這個科技產品很好", None),
        ("我要買科普書", None),
        ("我想問專科的事", None),
        ("我對這科不熟", None),
        ("科科", None),
        ("看科", None),
        # 完全沒提到科別
        ("附近有醫院嗎", None),
        ("今天天氣如何", None),
        ("", None),
    ],
)
def test_looks_like_department_mention(text, expected):
    assert _looks_like_department_mention(text) == expected


def test_history_prefers_alias_table_over_literal_mention():
    """表命中時要回表認得的說法，不可被字面比對搶走。"""
    messages = [
        HumanMessage(content="附近有腸胃科嗎"),
        HumanMessage(content=SHARED_LOCATION),
    ]
    assert _extract_department_from_history(messages) == "腸胃科"


def test_history_keeps_department_the_table_does_not_know():
    """
    這是本次修改的重點：表查不到時不可回 None。

    回 None 會讓上游把它當成「使用者沒指定科別」，強制改呼叫不分科搜尋，
    使用者說的科別就此靜默消失——拿到一份混著牙科、婦產科的清單卻以為
    系統聽懂了。原樣往下傳才能讓 service 層去 LLM 兜底，兜不出來也能誠實說。
    """
    messages = [
        HumanMessage(content="附近有腹腔鏡科嗎"),
        HumanMessage(content=SHARED_LOCATION),
    ]
    assert _extract_department_from_history(messages) == "腹腔鏡科"


def test_history_returns_none_when_no_department_mentioned():
    """沒指名科別時仍要回 None，否則不分科搜尋會被誤導成科別搜尋。"""
    messages = [
        HumanMessage(content="附近有醫院嗎"),
        HumanMessage(content=SHARED_LOCATION),
    ]
    assert _extract_department_from_history(messages) is None
