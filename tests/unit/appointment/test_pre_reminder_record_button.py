"""出發提醒卡片上的看診錄音入口。

長輩在診間門口能完成的操作只有一兩下，這顆按鈕是整個看診錄音功能唯一可行的入口。
"""

import json

import pytest

from app.core.config import settings
from app.services.line_messaging.flex.appointment_flex import build_pre_reminder_flex


@pytest.fixture()
def liff_url(monkeypatch):
    monkeypatch.setattr(settings, "LIFF_URL", "https://liff.line.me/1234-abcd")


def _footer(**kwargs):
    message = build_pre_reminder_flex(
        reminder_id="a1", when_text="今天 10:30", hospital_name="台大醫院", **kwargs
    )
    return json.loads(message.contents.json())["footer"]["contents"]


def test_本人版有錄音按鈕且直接連到錄音頁(liff_url):
    actions = [button["action"] for button in _footer()]
    uris = [action["uri"] for action in actions if action["type"] == "uri"]
    assert len(uris) == 1
    assert "/clinic-visits/record" in uris[0]


def test_按鈕帶著掛號資訊讓紀錄知道是哪一次門診(liff_url):
    uri = next(b["action"]["uri"] for b in _footer() if b["action"]["type"] == "uri")
    assert "appointment_id=a1" in uri
    # 醫院名稱要 URL 編碼，中文不能直接塞進 query。
    assert "%E5%8F%B0%E5%A4%A7" in uri


def test_家屬版不放錄音按鈕(liff_url):
    """家屬版的收件人不一定會陪去，給他一顆按了也沒用的按鈕只會造成誤解。"""
    actions = [button["action"] for button in _footer(patient_name="王媽媽")]
    assert not [action for action in actions if action["type"] == "uri"]


def test_沒設定_LIFF_URL_時少一顆按鈕而不是壞掉(monkeypatch):
    monkeypatch.setattr(settings, "LIFF_URL", "")
    actions = [button["action"] for button in _footer()]
    assert [action["type"] for action in actions] == ["postback"]


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
