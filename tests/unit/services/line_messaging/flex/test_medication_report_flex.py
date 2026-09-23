"""聊天回報服藥的三張卡。

這三張都是為了按鈕才做成卡片：已記錄那張要掛「記錯了」，反問那張把候選時段
做成按鈕。所以斷言集中在「按鈕在不在、帶的參數對不對」。
"""

import json

from app.core.user_font_size import reset_request_font_size, set_request_font_size
from app.i18n.messages import t
from app.services.line_messaging.flex.medication_report_flex import (
    REPORT_SLOT_ACTION,
    UNDO_REPORT_ACTION,
    SlotChoice,
    as_payload,
    build_report_bubble,
    build_slot_choice_bubble,
)
from resources.flex_messages.size_guard import fits
from resources.flex_messages.theme import resolve_theme


def _ft():
    return resolve_theme("normal")


def _texts(node) -> list[str]:
    """攤平 bubble 裡所有 text 節點，供內容斷言。"""
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


def _actions(node) -> list[dict]:
    found = []
    if isinstance(node, dict):
        if isinstance(node.get("action"), dict):
            found.append(node["action"])
        for value in node.values():
            found.extend(_actions(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_actions(item))
    return found


def _report(**kw):
    defaults = dict(
        log_id="log-1",
        slot_type="morning",
        scheduled_time="08:00",
        taken_time="12:00",
        medication_names=["Amoxicillin 500mg 永信", "Nexium 40mg 耐適恩錠"],
        ft=_ft(),
        language="zh-TW",
    )
    return build_report_bubble(**{**defaults, "ft": _ft(), **kw})


# ── 已記錄 ──────────────────────────────────────────────────────────


def test_done_card_shows_the_slot_the_time_and_the_medicines():
    texts = _texts(_report())
    assert "早 08:00" in texts
    assert "12:00 服用" in texts
    assert "✓ Amoxicillin 500mg 永信" in texts
    assert "✓ Nexium 40mg 耐適恩錠" in texts


def test_done_card_carries_an_undo_button_with_the_log_id():
    """沒有這顆按鈕，模型誤判的那一頓就永遠改不回來。"""
    (action,) = [a for a in _actions(_report()) if a.get("type") == "postback"]
    assert action["data"] == f"action={UNDO_REPORT_ACTION}&log_id=log-1"
    assert action["label"] == t("flex.medreport.button.undo", "zh-TW")


# ── 已取消 ──────────────────────────────────────────────────────────


def test_reverted_card_has_no_button_and_says_reminders_continue():
    bubble = _report(reverted=True)
    assert [a for a in _actions(bubble) if a.get("type") == "postback"] == []
    texts = _texts(bubble)
    assert t("flex.medreport.back_to_unconfirmed", "zh-TW") in texts
    assert t("flex.medreport.hint.reverted", "zh-TW") in texts


def test_reverted_card_does_not_still_list_the_medicines_as_taken():
    """那一頓已經改回未確認，再列一次 ✓ 藥名會讓人以為還記著。"""
    assert not [
        text for text in _texts(_report(reverted=True)) if text.startswith("✓ ")
    ]


# ── 是哪一頓 ────────────────────────────────────────────────────────


def test_slot_choice_card_makes_one_button_per_candidate():
    bubble = build_slot_choice_bubble(
        choices=[
            SlotChoice("log-m", "morning", "08:00"),
            SlotChoice("log-e", "evening", "18:00"),
        ],
        taken_time="12:00",
        ft=_ft(),
        language="zh-TW",
    )
    actions = [a for a in _actions(bubble) if a.get("type") == "postback"]
    assert [a["label"] for a in actions] == ["早 08:00", "晚 18:00"]
    # 使用者說的服藥時刻跟著按鈕走：按下去的現在不是他講的那個事實。
    assert actions[0]["data"] == f"action={REPORT_SLOT_ACTION}&log_id=log-m&at=12:00"


def test_slot_choice_card_caps_the_buttons_at_the_four_daily_slots():
    bubble = build_slot_choice_bubble(
        choices=[SlotChoice(f"log-{i}", "noon", f"1{i}:00") for i in range(6)],
        taken_time="12:00",
        ft=_ft(),
        language="zh-TW",
    )
    assert len([a for a in _actions(bubble) if a.get("type") == "postback"]) == 4


# ── payload ────────────────────────────────────────────────────────


def test_payload_carries_the_speech_text_for_tts():
    payload = as_payload(_report(), "alt", speech_text="唸這段")
    assert payload["type"] == "flex"
    assert payload["altText"] == "alt"
    assert payload["speechText"] == "唸這段"
    # 工具回傳的是這個 dict 的 JSON，replier 要求整串以 { 開頭、} 結尾。
    text = json.dumps(payload, ensure_ascii=False)
    assert text.startswith("{") and text.endswith("}")


def test_payload_is_refused_when_the_bubble_is_too_big_for_line():
    """超過上限 LINE 會回 400，使用者什麼都收不到——比退回純文字糟得多。"""
    huge = _report(medication_names=[f"藥名{i}" * 40 for i in range(200)])
    assert not fits(huge)
    assert as_payload(huge, "alt") is None


def test_cards_fit_at_the_largest_font_size():
    """放大字級的長輩才是這張卡的主要讀者。"""
    token = set_request_font_size("xlarge")
    try:
        ft = resolve_theme()
        assert fits(build_report_bubble(
            log_id="log-1", slot_type="morning", scheduled_time="08:00",
            taken_time="12:00",
            medication_names=["Amoxicillin 500mg 永信"] * 5,
            ft=ft, language="zh-TW",
        ))
        assert fits(build_slot_choice_bubble(
            choices=[SlotChoice(f"log-{i}", "noon", "12:00") for i in range(4)],
            taken_time="12:00", ft=ft, language="zh-TW",
        ))
    finally:
        reset_request_font_size(token)
