"""分享卡的關鍵字判斷：只攔整句就是在要分享入口的短句，其餘交給 agent。"""

import pytest

from app.services.line_messaging.share_intent import is_share_intent


@pytest.mark.parametrize(
    "text",
    [
        "加好友",
        "加好友連結",
        "我要加 CARE 好友",
        "加官方帳號",
        "分享給朋友",
        "分享 CARE",
        "分享這個系統",
        "推薦給朋友",
        "怎麼邀請朋友",
        "邀請朋友一起用",
        "給我 QR code",
        "CARE 的 QRcode",
        "邀請家人",
        "我要加家人",
        "加入家庭",
        "怎麼新增家人？",
        # 全形英文、驚嘆號與波浪號都是 LINE 上常見的打法
        "ＣＡＲＥ加好友！",
        "分享給朋友～",
    ],
)
def test_share_phrases_are_intercepted(text):
    assert is_share_intent(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        # 院所搜尋的說法：分享位置是找附近醫院的流程，推薦醫院是要找院所
        "分享位置",
        "推薦醫院",
        "推薦附近診所",
        "我想分享我的血壓",
        # 句子裡還有別的內容就交給 agent，由它決定要不要叫 share_care
        "我朋友說加好友可以查藥嗎",
        "藥袋上的 QR code 是什麼",
        "家人生病了怎麼辦",
        "加入健保",
        "打開官網",
    ],
)
def test_other_messages_go_to_the_agent(text):
    assert not is_share_intent(text)
