"""出發提醒卡片上的看診錄音入口。

長輩在診間門口能完成的操作只有一兩下，這顆按鈕是看診錄音最可行的入口。
2026-09-22 起在聊天室錄，按鈕是 postback，不再開 LIFF。
"""

import json
from urllib.parse import parse_qs

import pytest

from app.services.line_messaging.flex.appointment_flex import build_pre_reminder_flex


@pytest.fixture()
def liff_url():
    """舊測試沿用的名字；錄音按鈕已經不看 LIFF_URL。"""


def _footer(hospital_name_override=None, **kwargs):
    message = build_pre_reminder_flex(
        reminder_id="a1",
        when_text="今天 10:30",
        hospital_name=hospital_name_override or "台大醫院",
        **kwargs,
    )
    return json.loads(message.contents.json())["footer"]["contents"]


def _record_action(**kwargs) -> dict:
    return next(
        button["action"]
        for button in _footer(**kwargs)
        if "clinic_record_start" in button["action"].get("data", "")
    )


def test_本人版有錄音按鈕且在聊天室開始():
    action = _record_action()
    assert action["type"] == "postback"
    # .json() 輸出 snake_case。
    assert action["display_text"] == action["label"]


def test_按鈕帶著掛號資訊讓紀錄知道是哪一次門診():
    params = parse_qs(_record_action()["data"])
    assert params["appointment_id"] == ["a1"]
    assert params["hospital_name"] == ["台大醫院"]


def test_醫院名稱很長也不會超過_postback_上限():
    data = _record_action(hospital_name_override="國立臺灣大學醫學院附設醫院" * 3)["data"]
    assert len(data) <= 300


def test_家屬版不放錄音按鈕():
    """家屬版的收件人不一定會陪去，給他一顆按了也沒用的按鈕只會造成誤解。"""
    actions = [button["action"] for button in _footer(patient_name="王媽媽")]
    assert not [a for a in actions if "clinic_record_start" in a.get("data", "")]


def test_出發按鈕沒有被擠掉(liff_url):
    """加新按鈕不能動到原本的功能。"""
    actions = [button["action"] for button in _footer()]
    assert actions[0]["type"] == "postback"
    assert "action=depart" in actions[0]["data"] or "depart" in actions[0]["data"]


@pytest.mark.parametrize("language", ["zh-TW", "en", "id", "vi", "th", "ja"])
def test_六種語言都有按鈕文案(liff_url, language):
    labels = [b["action"]["label"] for b in _footer(language=language)]
    assert len(labels) == 2
    assert not any(label.startswith("flex.appt") for label in labels)
