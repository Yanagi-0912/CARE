"""藥單問答卡：登記資料要與答案本文分開的那一塊。

分開不是美觀問題——那幾行是程式從資料庫算出來的事實（哪幾種藥、今天排幾點、
這一頓晚了多久），答案本文是模型依知識庫寫的。混成同一段落，使用者分不出
哪一句是「系統知道的他自己的資料」，而他要拿去問藥師的正是那幾句。
"""

from app.core.medication_facts import (
    begin_request_medication_facts,
    get_request_medication_facts,
    reset_request_medication_facts,
    set_request_medication_facts,
)
from app.core.rag_sources import SourceRef
from app.services.line_messaging.flex.rag_answer_flex import build_medication_answer_flex
from app.services.line_messaging.reply.reply import _strip_medication_facts
from resources.flex_messages.size_guard import fits
from resources.flex_messages.theme import resolve_theme

FACTS = [
    "目前登記 4 種藥：Amoxicillin 500mg 永信、Nexium 40mg 耐適恩錠",
    "今天排定早 08:00、晚 18:00",
    "12:00 到下一頓晚 18:00 只隔 6 小時，排定的間隔是 10 小時",
]


def _texts(node) -> list[str]:
    found = []
    if isinstance(node, dict):
        if node.get("type") == "text" and isinstance(node.get("text"), str):
            found.append(node["text"])
        for value in node.values():
            found.extend(_texts(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_texts(item))
    return found


def _card(body="牛奶不影響吸收。", facts=FACTS, sources=()):
    return build_medication_answer_flex(
        "我的藥可以配牛奶嗎", body, facts, sources, resolve_theme("normal")
    )


def test_card_has_its_own_header_not_the_general_health_one():
    """使用者要一眼看出這張在講他自己的藥，不是一般衛教。"""
    assert "您的用藥" in _texts(_card().to_dict()["contents"]["header"])


def test_facts_appear_as_their_own_labelled_block():
    contents = _card().to_dict()["contents"]
    texts = _texts(contents["body"])
    assert "CARE 裡登記的資料" in texts
    for line in FACTS:
        assert line in texts


def test_answer_body_is_still_shown():
    assert "牛奶不影響吸收。" in _texts(_card().to_dict()["contents"]["body"])


def test_sources_still_become_buttons():
    """答案本文仍是知識庫生成的，來源不能因為多了藥單就消失。"""
    card = _card(sources=[SourceRef(1, "食藥署", "https://ex/1")])
    footer = card.to_dict()["contents"]["footer"]
    assert "[1] 食藥署" in _texts(footer)


def test_no_facts_means_no_block():
    contents = _card(facts=[]).to_dict()["contents"]
    assert "CARE 裡登記的資料" not in _texts(contents["body"])


def test_card_fits_with_a_full_prescription():
    facts = [
        "目前登記 8 種藥：" + "、".join(f"藥品名稱 {i} 500mg 製造廠" for i in range(8)),
        "今天排定早 08:00、中 12:00、晚 18:00、睡前 21:30",
        "早 08:00 那一頓在 12:00 才確認，比排定時間晚 4 小時",
        "12:00 到下一頓中 12:00 只隔 6 小時，排定的間隔是 10 小時",
        "服藥時間或劑量要不要調整，請先問藥師或醫師",
    ]
    card = _card(body="答案本文。" * 60, facts=facts)
    assert fits(card.to_dict()["contents"])


# ── 本文裡的那一段要被拿掉 ──────────────────────────────────────────


def test_the_facts_paragraph_is_removed_from_the_card_body():
    """留著會整段重複一次：卡片上已經有自己的一塊。"""
    token = begin_request_medication_facts()
    try:
        set_request_medication_facts(FACTS, "以下是 CARE 裡登記的資料：\n" + "\n".join(FACTS), None)
        facts = get_request_medication_facts()
        card_text = "牛奶不影響吸收。\n\n" + facts.block
        body = _strip_medication_facts(card_text, facts.block)
    finally:
        reset_request_medication_facts(token)

    assert body == "牛奶不影響吸收。"
    # 引言也要一起走，否則卡片上會留下一句沒有內容的「以下是…：」
    assert "以下是" not in body


def test_stripping_keeps_the_answer_when_the_block_is_not_found():
    """卡片多一段重複的文字，比為了排版丟掉答案本文好。"""
    assert _strip_medication_facts("答案本文", "對不上的內容") == "答案本文"
    assert _strip_medication_facts("答案本文", "") == "答案本文"


def test_holder_is_empty_when_this_turn_did_not_use_the_tool():
    token = begin_request_medication_facts()
    try:
        assert get_request_medication_facts() is None
    finally:
        reset_request_medication_facts(token)


def test_writing_without_a_holder_is_ignored_instead_of_raising():
    """非 LINE 入口（純 API、eval 腳本）不會開場，不該因此整個回答失敗。"""
    set_request_medication_facts(FACTS, "block", None)
    assert get_request_medication_facts() is None
