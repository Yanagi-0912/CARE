"""華語 → 台語漢字改寫：送出的提示與回傳處理。用假模型，不打 Gemini。"""

import asyncio
from types import SimpleNamespace

import pytest

from app.services.speech import taigi_text as taigi_text_module
from app.services.speech.taigi_text import TaigiTextConverter


class FakeChatModel:
    def __init__(self, content):
        self.content = content
        self.calls = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return SimpleNamespace(content=self.content)


class FakeGemini:
    def __init__(self, content):
        self.chat_model = FakeChatModel(content)


async def test_to_taigi_sends_source_text_and_strips_result():
    gemini = FakeGemini("  阿公，你今仔日藥仔食矣未？\n")

    out = await TaigiTextConverter(gemini).to_taigi("爺爺，你今天吃藥了沒有？")

    assert out == "阿公，你今仔日藥仔食矣未？"
    prompt = gemini.chat_model.calls[0][0].content
    assert "爺爺，你今天吃藥了沒有？" in prompt
    # 要求只念重點的字數有真的寫進提示
    assert f"不超過 {taigi_text_module.TAIGI_SPEECH_MAX_CHARS} 字" in prompt
    # 9/14 Gemini 實測把「右下腹」寫成「倒手」（左）、「上午」寫成「下晡」（下午）；
    # 兩條對照規則拿掉就會再犯。
    assert "正爿下腹" in prompt and "上午寫「早起」" in prompt


# 模型沒守字數時截在句尾，念稿不會又拖回十幾秒（正式環境 389 字念了 14.1 秒）。
async def test_overlong_speech_is_capped_at_a_sentence_end():
    limit = taigi_text_module.TAIGI_SPEECH_HARD_MAX_CHARS
    sentence = "記得飯後食藥仔，若是有頭殼眩就愛緊去看醫生。"  # 22 字
    gemini = FakeGemini(sentence * 12)  # 264 字

    out = await TaigiTextConverter(gemini).to_taigi("很長的回答")

    assert len(out) <= limit
    assert out.endswith("。")
    assert out == sentence * (limit // len(sentence))


async def test_speech_within_hard_limit_is_not_cut():
    text = "記得飯後食藥仔。" * 10  # 80 字
    assert await TaigiTextConverter(FakeGemini(text)).to_taigi("回答") == text


# thinking 模型的 content 可能是 parts 清單而不是字串。
async def test_to_taigi_accepts_list_content():
    gemini = FakeGemini([{"type": "text", "text": "食飽未？"}])
    assert await TaigiTextConverter(gemini).to_taigi("吃飽了嗎？") == "食飽未？"


async def test_to_taigi_empty_result_raises():
    with pytest.raises(ValueError):
        await TaigiTextConverter(FakeGemini("  ")).to_taigi("吃飽了嗎？")


class SlowChatModel:
    async def ainvoke(self, messages):
        await asyncio.sleep(1)
        return SimpleNamespace(content="太慢")


# 模型卡住時要自己放棄，讓 tts_service 改念國語，而不是整則回覆陪著等。
async def test_to_taigi_times_out(monkeypatch):
    monkeypatch.setattr(taigi_text_module, "TAIGI_TEXT_TIMEOUT_SECONDS", 0.05)
    gemini = SimpleNamespace(chat_model=SlowChatModel())

    with pytest.raises(asyncio.TimeoutError):
        await TaigiTextConverter(gemini).to_taigi("吃飽了嗎？")
