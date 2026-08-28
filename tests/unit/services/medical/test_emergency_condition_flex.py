"""緊急卡片：版面對模板、撥號按鈕可用、且不得出現任何門診科別。"""

import json
from pathlib import Path

import pytest
from linebot.v3.messaging import FlexContainer

from app.services.medical.symptom_classification.urgency import (
    EMERGENCY_HOTLINES,
    NOT_URGENT,
    URGENCY_EMERGENCY,
    Hotline,
    UrgencyVerdict,
)
from resources.flex_messages.medical_messages.emergency_condition_flex_message import (
    ALT_TEXT_EMERGENCY,
    build_emergency_condition_flex,
)

TEMPLATE_PATH = (
    Path(__file__).resolve().parents[4]
    / "resources"
    / "flex_messages"
    / "medical_messages"
    / "emergency_condition_flex_template.json"
)

_TEXT_KEYS = {"text", "uri", "label", "altText"}


@pytest.fixture(scope="module")
def template() -> dict:
    return json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))


def _skeleton(node):
    if isinstance(node, dict):
        return {
            key: (None if key in _TEXT_KEYS else _skeleton(value))
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_skeleton(item) for item in node]
    return node


def _verdict(display="你提到有人失去意識、叫不醒"):
    return UrgencyVerdict(level=URGENCY_EMERGENCY, display=display)


def _bubble(verdict=None):
    return build_emergency_condition_flex(verdict or _verdict())["contents"]


# --- 版面對模板 --------------------------------------------------------------


def test_card_matches_template(template):
    """模板是唯一事實來源，樣式漂掉就失敗。"""
    assert _skeleton(_bubble()) == _skeleton(template)


def test_alt_text_is_actionable():
    payload = build_emergency_condition_flex(_verdict())
    assert payload["altText"] == ALT_TEXT_EMERGENCY
    assert payload["type"] == "flex"


def test_card_passes_line_sdk_validation():
    FlexContainer.from_json(json.dumps(_bubble(), ensure_ascii=False))


# --- 撥號按鈕 ---------------------------------------------------------------


def _buttons(bubble):
    return [
        node
        for node in bubble["body"]["contents"]
        if isinstance(node, dict) and "action" in node
    ]


def test_primary_button_is_119():
    """主按鈕必須是 119。這張卡的使用者多半沒有餘裕在選項間比較。"""
    buttons = _buttons(_bubble())
    assert buttons
    assert buttons[0]["action"]["uri"] == "tel:119"


def test_every_hotline_gets_a_dialable_button():
    buttons = _buttons(_bubble())
    assert len(buttons) == len(EMERGENCY_HOTLINES)
    for button, hotline in zip(buttons, EMERGENCY_HOTLINES):
        assert button["action"]["type"] == "uri"
        assert button["action"]["uri"] == hotline.tel_uri


@pytest.mark.parametrize(
    "number, expected",
    [("119", "tel:119"), ("1 1 9", "tel:119"), ("１１９", "tel:119"), ("", "tel:")],
)
def test_tel_uri_keeps_digits_only(number, expected):
    """全形字元或分隔符會讓某些裝置撥不出去。"""
    assert Hotline("測試", number).tel_uri == expected


# --- 安全性質 ---------------------------------------------------------------


def test_card_contains_no_department():
    """
    並陳「可能是內科，但也留意是否需要急診」等於把判斷責任推回給正在不舒服
    的人，實質上就是導向門診。
    """
    payload = json.dumps(_bubble(), ensure_ascii=False)
    for department in ("內科", "外科", "家醫科", "婦產科", "神經內科", "牙科"):
        assert department not in payload


def test_card_avoids_diagnostic_phrasing():
    payload = json.dumps(_bubble(), ensure_ascii=False)
    for forbidden in ("你應該是", "確診", "診斷為", "你得了"):
        assert forbidden not in payload


def test_display_falls_back_when_classifier_returns_blank():
    """判斷器沒給說明時仍要有一句話，不能讓標題下方開天窗。"""
    payload = json.dumps(_bubble(_verdict(display="")), ensure_ascii=False)
    assert "可能需要立即處置" in payload


def test_not_urgent_verdict_exposes_no_hotlines():
    """非緊急的判斷不該帶出撥號按鈕——這是短路節點唯一的資料來源。"""
    assert NOT_URGENT.hotlines == ()
    assert NOT_URGENT.is_emergency is False
