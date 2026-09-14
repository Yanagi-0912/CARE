"""用藥提醒卡的語氣版本：只換說明那一句，其他都不動。

control 是上線前的文字。不帶 tone 時就是 control，逐位元組不變已經由
test_medication_flex.py 的快照釘住；這裡釘住另外兩版確實換掉了那一句、而且只換
那一句，以及每一版都有六種語言。
"""

import pytest

from app.core.user_language import SUPPORTED_LANGUAGES
from app.i18n.messages import t
from app.services.line_messaging.flex.medication_flex import (
    build_patient_medication_flex,
    build_patient_urgent_reminder_flex,
)
from app.services.medication.reminder_variants import TONE_CONTROL, TONES

CONTROL_T0 = "請於 30 分鐘內服藥，並點擊下方按鈕確認。"
CONTROL_T20 = "您尚未點擊「我已用藥」。請即刻服藥並點擊下方按鈕確認。"
TONE_T0 = {
    "family": "吃完藥按一下下面的按鈕，家人就知道你吃過了。",
    "brief": "吃藥時間到了，吃完按下面的按鈕。",
}
TONE_T20 = {
    "family": "還沒收到你的確認。吃完按一下，家人就不用擔心。",
    "brief": "還沒按喔，吃完藥記得按下面的按鈕。",
}


def _t0(tone):
    return str(
        build_patient_medication_flex(
            log_id="L1", slot_type="morning", scheduled_time="08:00", language="zh-TW", tone=tone
        ).contents.to_dict()
    )


def _t20(tone):
    return str(
        build_patient_urgent_reminder_flex(
            log_id="L1", slot_type="morning", scheduled_time="08:00", language="zh-TW", tone=tone
        ).contents.to_dict()
    )


@pytest.mark.parametrize("tone", ["family", "brief"])
def test_reminder_card_uses_the_tone_wording(tone):
    rendered = _t0(tone)
    assert TONE_T0[tone] in rendered
    assert CONTROL_T0 not in rendered


@pytest.mark.parametrize("tone", ["family", "brief"])
def test_tone_changes_nothing_else_on_the_reminder_card(tone):
    """標題、時段、按鈕與 postback 都跟 control 相同：把那一句換回來就是同一張卡。"""
    assert _t0(tone).replace(TONE_T0[tone], CONTROL_T0) == _t0("control")


@pytest.mark.parametrize("tone", ["family", "brief"])
def test_urgent_card_uses_the_tone_wording(tone):
    rendered = _t20(tone)
    assert TONE_T20[tone] in rendered
    assert CONTROL_T20 not in rendered


@pytest.mark.parametrize("tone", ["family", "brief"])
def test_tone_changes_nothing_else_on_the_urgent_card(tone):
    assert _t20(tone).replace(TONE_T20[tone], CONTROL_T20) == _t20("control")


@pytest.mark.parametrize("tone", [tone for tone in TONES if tone != TONE_CONTROL])
def test_every_bandit_tone_has_its_own_wording_on_both_cards(tone):
    """卡片用字面值對照語氣名稱，不 import 拉霸模組；這裡確保拉霸新增或改名的
    語氣不會悄悄落回現行文字——那樣兩個版本送出去一模一樣，學到的差異是假的。"""
    assert CONTROL_T0 not in _t0(tone)
    assert CONTROL_T20 not in _t20(tone)


def test_unknown_tone_falls_back_to_the_current_wording():
    """資料庫裡出現不認得的語氣（例如之後拿掉的選項）時，照現行文字送。"""
    assert CONTROL_T0 in _t0("retired-tone")
    assert CONTROL_T20 in _t20("retired-tone")


@pytest.mark.parametrize(
    "key",
    [
        "flex.med.instruction.family",
        "flex.med.instruction.brief",
        "flex.med.urgent_body.family",
        "flex.med.urgent_body.brief",
    ],
)
@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_every_tone_is_translated_into_every_language(key, language):
    text = t(key, language)
    assert text and text != key
    if language != "zh-TW":
        assert text != t(key, "zh-TW")
