"""「看診錄音」關鍵字：整句才攔，句子裡還有別的內容就交給 agent。"""

import pytest

from app.services.line_messaging.clinic_recording_intent import is_clinic_recording_intent


@pytest.mark.parametrize(
    "text",
    ["看診錄音", "我要錄音", "錄音", "幫我錄看診", "陪診錄音！", "Record the visit", "診察を録音"],
)
def test_整句就是要錄看診(text):
    assert is_clinic_recording_intent(text)


@pytest.mark.parametrize(
    "text",
    ["看診錄音可以給醫生聽嗎", "錄音檔怎麼刪", "醫生說要錄音嗎", "", "我今天去看診"],
)
def test_句子裡還有別的內容不攔(text):
    assert not is_clinic_recording_intent(text)
