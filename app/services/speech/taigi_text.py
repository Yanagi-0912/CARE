"""把要念給使用者聽的華語改寫成台語漢字，再交給台語 TTS。

廠商文件把「華語句子」列為不建議的輸入：「可能導致台語發音錯誤」。2026-09-14 實測
把 TTS 念出來的音檔丟回台語 STT：華語「爺爺，你今天吃藥了沒有？」聽回來是「野野，
離近仔日，鐵藥了無有」，台語漢字版只錯一個語助詞。CARE 要念的內容（Gemini 的回答、
工具的 speechText、固定訊息）都是華語，所以念之前要先改寫。
"""

from __future__ import annotations

import asyncio

from langchain_core.messages import HumanMessage

from app.services.gemini.services.gemini_service import GeminiService
from app.services.gemini.shared.parser import content_to_text

# 延遲敏感、推理需求低，理由同 query_rewriter.REWRITE_THINKING_LEVEL。
TAIGI_TEXT_THINKING_LEVEL = "low"

# 改寫是在送出任何 LINE 訊息之前被 await 的（見 tts_service），模型卡住時整則回覆
# 都得陪著等。新 VM 上一次 Gemini 呼叫約 10 秒（含 thinking，2026-09-14 實測）；
# 這裡是 low thinking，應該更快，超過就改念國語。改寫本身的耗時還沒量過。
TAIGI_TEXT_TIMEOUT_SECONDS = 10.0

PROMPT = """把下面這段要念給長輩聽的華語，改寫成台語（台灣閩南語）的漢字文本，交給台語語音合成念出來。

規則：
1. 用教育部《臺灣台語常用詞辭典》的推薦用字，例如：食、毋、袂、佮、欲、啥物、今仔日。不要寫羅馬字。
2. 意思不能變。藥名、劑量、次數、時間、日期、電話一個都不能少、不能改。數字寫成台語的念法，例如「3 次」寫「三擺」、「500 毫克」寫「五百毫克」；119、電話這類號碼一個字一個字寫，例如「一一九」。
3. 一天的時段照原意，不可以換成別的時段：早上、上午寫「早起」，中午寫「中晝」，下午寫「下晡」，晚上寫「暗時」，半夜寫「半暝」。
4. 人稱照原文：「你」「您」都寫「你」，不要寫成「恁」（你們）或「阮」（我們）；「家人」寫「厝內的人」。
5. 網址、表情符號、項目符號、Markdown 記號直接拿掉，不要換成網站名稱或其他內容；條列改成一句一句念得出來的話。英文縮寫與外文藥名照原樣保留。
6. 只輸出改寫後的台語文字，不要加說明、標題或引號。

華語：
{text}"""


class TaigiTextConverter:
    def __init__(self, gemini_service: GeminiService) -> None:
        self._gemini = gemini_service

    async def to_taigi(self, text: str) -> str:
        result = await asyncio.wait_for(
            self._gemini.chat_model.ainvoke([HumanMessage(content=PROMPT.format(text=text))]),
            timeout=TAIGI_TEXT_TIMEOUT_SECONDS,
        )
        converted = content_to_text(result.content).strip()
        if not converted:
            raise ValueError("台語改寫回傳空字串")
        return converted
