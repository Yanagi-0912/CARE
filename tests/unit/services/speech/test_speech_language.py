import pytest

from app.services.speech.speech_language import choose_transcript, looks_like_taiwanese


# 台語 STT 聽台語寫出來的樣子：教育部推薦用字，華語逐字稿不會這樣寫。
@pytest.mark.parametrize(
    "text",
    [
        "阿公，你食飽未？",
        "我毋知影按怎食這个藥",
        "今仔日下晡欲去看醫生",
        "彼个囡仔規工咧哭",
        "袂記食藥矣",
        "頭眩，腹肚嘛真疼",
    ],
)
def test_taiwanese_transcripts_are_recognized(text):
    assert looks_like_taiwanese(text) is True


# 華語逐字稿不能被判成台語——誤判的話整段會改用台語模型的亂字，而且回覆會念台語。
@pytest.mark.parametrize(
    "text",
    [
        "爺爺，你今天吃藥了沒有？",
        "我最近血壓比較高，需要調整藥量嗎",
        "明天早上要回診，請問幾點",
        "經濟艙坐太久腳會腫嗎",  # 「濟」單獨出現在華語詞裡
        "小孩子發燒要看哪一科",
        "",
    ],
)
def test_mandarin_transcripts_are_not_taiwanese(text):
    assert looks_like_taiwanese(text) is False


# 2026-09-14 實測：華語音檔餵台語 STT 會寫出這種亂字。它含「仔」「無」，所以那些
# 常見字不能當特徵——這個案例就是門檻的來源。
def test_gibberish_from_mandarin_audio_is_not_taiwanese():
    assert looks_like_taiwanese("野野，離近仔日，鐵藥了無有") is False


def test_taiwanese_wins_when_taigi_transcript_has_taigi_words():
    assert choose_transcript("阿公你食飽未", "阿公你吃飽了嗎") == ("阿公你食飽未", "nan-TW")


# 2026-09-19 實測的華語音檔：台語模型不是寫出亂字，而是把同一句華語寫成台語用字
# （連「食藥」都寫出來了）。只看用字表會誤判成台語，看兩份像不像才判得對。
def test_mandarin_wins_when_both_heard_the_same_sentence():
    assert choose_transcript("我忘記早上有無食藥", "我 忘 記 早 上 有 沒 有 吃 藥 。") == (
        "我 忘 記 早 上 有 沒 有 吃 藥 。",
        "zh-TW",
    )


# 2026-09-19 實測的台語音檔：Gemini 整句聽錯，兩份對不起來。
def test_taiwanese_wins_when_gemini_heard_something_else():
    assert choose_transcript("後禮拜門診敢會使解時間", "百 盟 金 卡 沒 塞 開 時 間") == (
        "後禮拜門診敢會使解時間",
        "nan-TW",
    )


# 已知的失敗模式：兩邊都沒聽懂時會判成台語（那批 24 段裡 12 段華語有 1 段這樣）。
# 台語那份會被送進 agent、回覆也會用台語念。
def test_both_wrong_falls_to_taiwanese():
    _, language = choose_transcript(
        "我的血液猶算欲幾箍矣，猶怎物啉", "我 的 血 壓 藥 剩 沒 幾 顆 了"
    )
    assert language == "nan-TW"


def test_taiwanese_still_wins_when_gemini_failed():
    assert choose_transcript("阿公你食飽未", None) == ("阿公你食飽未", "nan-TW")


def test_gibberish_alone_is_dropped():
    """只剩台語那份又不像台語：寧可什麼都不回，讓呼叫端退 faster-whisper。"""
    assert choose_transcript("野野，離近仔日", None) == (None, "zh-TW")


def test_nothing_heard():
    assert choose_transcript(None, None) == (None, "zh-TW")
