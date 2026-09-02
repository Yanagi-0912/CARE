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
from app.core.user_language import SUPPORTED_LANGUAGES
from app.i18n.messages import t
from resources.flex_messages.medical_messages.emergency_condition_flex_message import (
    alt_text,
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


# 模板以預設語言與預設字級產生；比對時必須指定同一組，否則測的是
# 「跑測試的人剛好套用了哪一檔字級」。
TEMPLATE_LANGUAGE = "zh-TW"
TEMPLATE_FONT_SIZE = "large"


def _payload(verdict=None, *, language=TEMPLATE_LANGUAGE, font_size=TEMPLATE_FONT_SIZE):
    return build_emergency_condition_flex(
        verdict or _verdict(), language=language, font_size=font_size
    )


def _bubble(verdict=None, *, language=TEMPLATE_LANGUAGE, font_size=TEMPLATE_FONT_SIZE):
    return _payload(verdict, language=language, font_size=font_size)["contents"]


# --- 版面對模板 --------------------------------------------------------------


def test_card_matches_template(template):
    """模板是唯一事實來源，樣式漂掉就失敗。"""
    assert _skeleton(_bubble()) == _skeleton(template)


def test_alt_text_is_actionable():
    payload = _payload()
    assert payload["altText"] == alt_text(TEMPLATE_LANGUAGE)
    assert payload["type"] == "flex"


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
@pytest.mark.parametrize("font_size", ["normal", "large", "xlarge"])
def test_card_passes_line_sdk_validation(language, font_size):
    bubble = _bubble(language=language, font_size=font_size)
    FlexContainer.from_json(json.dumps(bubble, ensure_ascii=False))


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


# --- 多語言 ------------------------------------------------------------------
#
# 這張卡是急救指示。副標（急迫度判斷器產生的 display）本來就依語言產生，
# 若其餘文案寫死中文就會變成混語言——使用者以為系統支援他的語言，卻看不懂
# 最關鍵的行動指示。混語言在這裡比全中文更糟。


def _all_text(node, out=None):
    out = [] if out is None else out
    if isinstance(node, dict):
        if node.get("type") == "text":
            out.append(node["text"])
        for value in node.values():
            _all_text(value, out)
    elif isinstance(node, list):
        for item in node:
            _all_text(item, out)
    return out


# ja 排除在外：日文本來就用漢字，落在同一個 Unicode 區間，用字元範圍分不出
# 「日文漢字」與「沒翻到的中文」。日文版改由 test_every_language_has_real_copy
# 與人工審閱把關。
@pytest.mark.parametrize(
    "language", [l for l in SUPPORTED_LANGUAGES if l not in ("zh-TW", "ja")]
)
def test_no_chinese_ui_copy_leaks_into_other_languages(language):
    """
    display 由判斷器依語言產生，這裡固定成非中文，其餘文字若還出現中文，
    就是有哪一段文案沒走 t()。
    """
    bubble = _bubble(_verdict(display="unconscious"), language=language)
    leaked = [
        text
        for text in _all_text(bubble)
        if any("\u4e00" <= ch <= "\u9fff" for ch in text)
    ]
    assert leaked == [], f"{language} 版仍殘留中文：{leaked}"


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_every_language_has_real_copy_not_i18n_keys(language):
    """t() 查不到 key 會回傳 key 本身，那會直接印在急救卡上。"""
    for text in _all_text(_bubble(language=language)):
        assert not text.startswith("emergency."), f"未翻譯的 key：{text}"


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_hotline_numbers_are_never_translated(language):
    """號碼是台灣的固定值。單位名稱要翻，號碼不能翻。"""
    bubble = _bubble(language=language)
    assert [b["action"]["uri"] for b in _buttons(bubble)] == ["tel:119", "tel:110"]


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_alt_text_follows_language(language):
    """altText 是通知列上唯一看得到的字，不跟著語言走等於通知看不懂。"""
    assert _payload(language=language)["altText"] == t("emergency.alt_text", language)


# --- 字級 --------------------------------------------------------------------


@pytest.mark.parametrize(
    "font_size, expected_headline",
    [("normal", "xl"), ("large", "xxl"), ("xlarge", "3xl")],
)
def test_font_size_setting_scales_the_card(font_size, expected_headline):
    """
    這張卡的使用者多半處於不好操作手機的狀態，字級設定在這裡比任何一張卡
    都重要。寫死 size 時，把字級調到 xlarge 的長輩看到的卡完全沒有變化。
    """
    bubble = _bubble(font_size=font_size)
    assert bubble["header"]["contents"][0]["size"] == expected_headline


def test_larger_font_setting_changes_every_text_node():
    """任何一個節點漏掉 ft 都會讓版面比例在放大時跑掉。"""
    small = _bubble(font_size="normal")
    large = _bubble(font_size="xlarge")

    def sizes(node, out=None):
        out = [] if out is None else out
        if isinstance(node, dict):
            if node.get("type") == "text":
                out.append(node["size"])
            for value in node.values():
                sizes(value, out)
        elif isinstance(node, list):
            for item in node:
                sizes(item, out)
        return out

    assert all(a != b for a, b in zip(sizes(small), sizes(large)))

