"""把要念給使用者聽的華語回答，濃縮成重點並改寫成台語漢字，再交給台語 TTS。

改寫的理由：廠商文件把「華語句子」列為不建議的輸入：「可能導致台語發音錯誤」。
2026-09-14 實測把 TTS 念出來的音檔丟回台語 STT：華語「爺爺，你今天吃藥了沒有？」
聽回來是「野野，離近仔日，鐵藥了無有」，台語漢字版只錯一個語助詞。CARE 要念的
內容（Gemini 的回答、工具的 speechText、固定訊息）都是華語，所以念之前要先改寫。

濃縮的理由：Taigi TTS 要等整段念完才回，念稿越長等越久。2026-09-14 正式環境第一則
台語語音，362 字的回答改寫成 389 字，Taigi 念了 14.1 秒，當次從收到語音到回覆約
54 秒。文字回覆照原文完整送出，語音只負責把重點念給長輩聽。
"""

from __future__ import annotations

import asyncio
import logging

from langchain_core.messages import HumanMessage

from app.services.gemini.services.gemini_service import GeminiService
from app.services.gemini.shared.parser import content_to_text

logger = logging.getLogger(__name__)

# 延遲敏感、推理需求低，理由同 query_rewriter.REWRITE_THINKING_LEVEL。
TAIGI_TEXT_THINKING_LEVEL = "low"

# 改寫是在送出任何 LINE 訊息之前被 await 的（見 tts_service），模型卡住時整則回覆
# 都得陪著等。2026-09-14 實測 15 次 1.2～9.0 秒、正式環境一次 2.7 秒；超過就改念國語。
TAIGI_TEXT_TIMEOUT_SECONDS = 10.0

# 語音只念重點的字數。2026-09-14 實測 Taigi TTS：37～50 字 2.3～5.5 秒、110～127 字
# 5.4～7.7 秒、389 字 14.1 秒；110 字以預設語速念出來約 30 秒，長輩也聽得完。
TAIGI_SPEECH_MAX_CHARS = 120
# 模型不一定守字數；超過這個長度就截在句尾，免得念稿又拖回十幾秒。
TAIGI_SPEECH_HARD_MAX_CHARS = 180

_SENTENCE_ENDS = "。！？!?"

PROMPT = """把下面這段要念給長輩聽的華語回答，改寫成台語（台灣閩南語）的漢字口語稿，交給台語語音合成念出來。文字回覆會照原文另外送出，這份只給耳朵聽。

規則：
1. 只念重點，全部不超過 {max_chars} 字：最要緊的放最前面（要不要趕緊就醫或叫救護車、藥怎麼食、啥物時陣回診），細節、來源、補充說明可以省略。原文本來就在 {max_chars} 字以內的，照原意全部改寫，不要刪。
2. 用教育部《臺灣台語常用詞辭典》的推薦用字，例如：食、毋、袂、佮、欲、啥物、今仔日。不要寫羅馬字。
3. 留下來的內容意思不能變。藥名、劑量、次數、時間、日期、電話不能改。數字寫成台語的念法，例如「3 次」寫「三擺」、「500 毫克」寫「五百毫克」；119、電話這類號碼一個字一個字寫，例如「一一九」。
4. 一天的時段照原意，不可以換成別的時段：早上、上午寫「早起」，中午寫「中晝」，下午寫「下晡」，晚上寫「暗時」，半夜寫「半暝」。
5. 左右照原意，不可以對調：右邊寫「正爿」、右手寫「正手」，左邊寫「倒爿」、左手寫「倒手」。例如「右下腹」寫「正爿下腹」。
6. 人稱照原文：「你」「您」都寫「你」，不要寫成「恁」（你們）或「阮」（我們）；「家人」寫「厝內的人」。
7. 網址、表情符號、項目符號、Markdown 記號、資料來源都不要念；拿掉網址後只剩「可以參考」這類半句話的，整句一起刪掉。條列改成一句一句念得出來的話。英文縮寫與外文藥名照原樣保留。
8. 只輸出改寫後的台語文字，不要加說明、標題或引號。

華語：
{text}"""


def _cap_at_sentence(text: str, max_chars: int) -> str:
    """超過 `max_chars` 時截到上限內最後一個句尾；整段沒有句尾就硬切。"""
    if len(text) <= max_chars:
        return text
    head = text[:max_chars]
    cut = max(head.rfind(ch) for ch in _SENTENCE_ENDS)
    return head[: cut + 1] if cut > 0 else head


class TaigiTextConverter:
    def __init__(self, gemini_service: GeminiService) -> None:
        self._gemini = gemini_service

    async def to_taigi(self, text: str) -> str:
        """回傳要念的台語漢字稿：重點、不超過 TAIGI_SPEECH_HARD_MAX_CHARS 字。"""
        prompt = PROMPT.format(max_chars=TAIGI_SPEECH_MAX_CHARS, text=text)
        result = await asyncio.wait_for(
            self._gemini.chat_model.ainvoke([HumanMessage(content=prompt)]),
            timeout=TAIGI_TEXT_TIMEOUT_SECONDS,
        )
        converted = content_to_text(result.content).strip()
        if not converted:
            raise ValueError("台語改寫回傳空字串")
        capped = _cap_at_sentence(converted, TAIGI_SPEECH_HARD_MAX_CHARS)
        if capped != converted:
            logger.warning(
                "台語念稿 %d 字超過上限，截到 %d 字", len(converted), len(capped)
            )
        return capped
