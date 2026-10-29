import pytest

from app.services.line_messaging.feature_guide_intent import is_feature_guide_intent


@pytest.mark.parametrize(
    "text",
    [
        "功能說明",
        "使用說明",
        "功能",
        "有什麼功能？",
        "你有哪些功能",
        "請問有什麼功能",
        "你會做什麼？",
        "你可以幫我做什麼",
        "CARE 能做什麼",
        "CARE 怎麼用",
        "新手教學",
        "Features",
        "What can you do?",
        "fitur",
        "tính năng",
        "Bạn có thể làm gì?",
        "ฟีเจอร์",
        "機能一覧",
        "何ができるの？",
    ],
)
def test_asking_what_care_can_do(text):
    assert is_feature_guide_intent(text)


@pytest.mark.parametrize(
    "text",
    [
        # 追問上一則：吸入器怎麼用、剛才那段再解釋一次。
        "怎麼用？",
        "請說明",
        "說明",
        "這個怎麼用",
        "怎麼用血壓計",
        # 在問自己的病情，不是問 CARE。
        "我可以做什麼",
        "血壓高可以做什麼運動",
        # 也是求救的字，交給走失與急迫度判斷。
        "help",
        "救命",
        "bantuan",
        "trợ giúp",
        "ช่วยเหลือ",
        "ヘルプ",
        "我迷路了",
        "",
    ],
)
def test_other_messages_go_to_the_usual_flow(text):
    assert not is_feature_guide_intent(text)
