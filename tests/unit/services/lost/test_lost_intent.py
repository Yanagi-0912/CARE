import pytest

from app.services.lost.lost_intent import detect_lost_intent, is_place_search


@pytest.mark.parametrize(
    "text",
    [
        "我走丟了",
        "我迷路了",
        "我好像迷路了",
        "我現在找不到路回家",
        "我找不到回家的路",
        "我不知道怎麼回家",
        "救命！我迷路了",
        "迷路了",
        "走丟了怎麼辦",
        "糟糕 走失了",
        "我不知道我在哪裡",
        "不知道我在哪",
        "我不曉得自己現在在哪邊",
        "我不知道我在那裡",  # 哪／那 的常見錯字
        "這裡是哪裡？",
        "我迷路了，這裡是一個公園旁邊",
        "我迷路了 不知道怎麼辦",
        # 語音轉文字常見的語助詞開頭
        "誒誒我不知道我人在哪裡",
        "欸 迷路了啦",
        "嗯那個 這裡是哪裡",
        "喂 我不知道我在哪",
        # 台語用字
        "阿嬤我揣無路",
        "我毋知影我佇佗位",
        # 其他語言的短句
        "I'm lost",
        "Help! I don't know where I am",
        "Saya tersesat",
        "Tôi bị lạc rồi",
        "ฉันหลงทางค่ะ",
        "迷子になった",
        "ここはどこ？",
    ],
)
def test_elder_saying_they_are_lost(text):
    assert detect_lost_intent(text) == "lost"


@pytest.mark.parametrize(
    "text",
    [
        "傳位置給家人",
        "分享我的位置給我女兒",
        "把位置傳給兒子",
        "告訴家人我在哪裡",
        "讓我的家人知道我在哪",
        "可以傳我的定位給老婆嗎",
    ],
)
def test_elder_wants_family_to_have_their_location(text):
    assert detect_lost_intent(text) == "share"


@pytest.mark.parametrize(
    "text",
    [
        # 別人走丟了：通報的對象不對
        "我媽走丟了",
        "我爸爸迷路了怎麼辦",
        "阿公走失了",
        # 假設、照護知識
        "如果我迷路了怎麼辦",
        "萬一走失要怎麼處理",
        "怎麼預防失智長輩走失",
        "我常常迷路是不是失智",
        "老人容易迷路嗎",
        # 找地點，不是找自己
        "不知道診所在哪裡",
        "不知道在哪裡看醫生",
        "附近藥局在哪裡",
        # 附近院所的舊說法，交給院所搜尋
        "分享位置",
        "傳送位置",
        # 問功能怎麼用
        "怎麼傳位置給家人",
        "分享位置給家人的功能怎麼用",
        # 外語長句交給分類器，關鍵字不攔
        "I'm lost with this app, where is the settings button?",
        "My mom is lost",
        # 一般問題
        "高血壓可以吃香蕉嗎",
        "",
    ],
)
def test_other_sentences_are_left_to_the_agent(text):
    assert detect_lost_intent(text) is None


@pytest.mark.parametrize(
    "text",
    [
        # 2026-09-30 實際案例：這句讓回覆下方多出「我迷路了，通知家人」
        "幫我找附近診所",
        "幫我找附近醫院",
        "幫我找 附近 的 藥局。",
        "幫我找醫院",
        "請問附近有藥局嗎？",
        "這附近有沒有牙醫",
        "附近哪裡有耳鼻喉科",
        "可以幫我找附近的診所嗎",
        "幫我搜尋附近診所",
        "幫忙找一下附近的醫院",
        "幫我找離我最近的診所，謝謝",
        "幫我找現在有開的藥局",
        "幫我找附近的診所或藥局",
        "最近的醫院在哪",
        "我想掛眼科",
        "我要看腸胃科",
    ],
)
def test_whole_sentence_asking_for_a_nearby_facility(text):
    assert is_place_search(text)


@pytest.mark.parametrize(
    "text",
    [
        # 句子裡有院所，但人是走丟的：必須照舊交給關鍵字與分類器
        "我在醫院附近迷路了",
        "幫我找附近診所我迷路了",
        "這裡是哪裡附近只有一間藥局",
        "我在藥局前面不知道怎麼回家",
        "診所附近我不認識路",
        "找不到路去診所",
        "醫院在哪我找不到路",
        # 找的不是院所
        "幫我找路",
        "幫我找家人",
        "幫我找回家的路",
        "幫我找我女兒她在醫院",
        "幫我找附近",
        "幫我找最近的派出所",
        "",
    ],
)
def test_anything_beyond_a_facility_request_is_not_a_place_search(text):
    assert not is_place_search(text)
