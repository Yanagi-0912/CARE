"""華語、英、印尼、越、泰、日的語音訊息，交給 Gemini 聽寫成文字。

原本走 n8n → local-asr（faster-whisper small，跑在 CPU 上）。2026-09-15 在 care-vm
用同一批音檔（六種語言各一句合成語音＋三段真實 LINE 錄音，每段兩次）量：
- faster-whisper 純模型中位數 4.9 秒，經 n8n 的線上實際時間中位數約 7 秒；錄音再短
  也快不了。CPU 開到 4 核反而 5.6 秒（4 vCPU 只有 2 顆實體核）。
- gemini-3.5-flash-lite（thinking low）中位數 1.4 秒（1.2～2.4）。
whisper small 也常聽錯：「降血壓」寫成「醬血壓」、越南文冒出中文字、三段真實錄音
錯兩段。Gemini 失敗或逾時，呼叫端才退回 n8n／faster-whisper。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from langchain_core.messages import HumanMessage

from app.core.config import settings
from app.core.user_language import DEFAULT_USER_LANGUAGE
from app.services.gemini.services.gemini_service import GeminiService
from app.services.gemini.shared.parser import content_to_text

logger = logging.getLogger(__name__)

# 同一批音檔 gemini-3.8-flash 更準（台語腔的真實錄音也對），但中位數 3.2 秒、
# 最慢 10.7 秒；flash-lite 在台語腔上會聽錯（「腹肚痛」寫成「巴頭痛」）。James 選速度。
GEMINI_STT_MODEL = "gemini-3.5-flash-lite"
# 實測 low 1.4 秒、minimal 1.6 秒，minimal 還會在日文、泰文、中文的字與字之間插空格。
GEMINI_STT_THINKING_LEVEL = "low"
# 實測 36 次最慢 2.4 秒（都是 10 秒內的錄音，更長的沒量過）。超過 8 秒就當成異常，
# 改走 whisper：最壞情況是 8 秒加上 whisper 的約 7 秒。
GEMINI_STT_TIMEOUT_SECONDS = 8.0

# LINE 的語音是 m4a；量測時以 audio/m4a 送出可正常辨識。
_MIME_BY_SUFFIX = {
    ".m4a": "audio/m4a",
    ".mp4": "audio/m4a",
    ".aac": "audio/aac",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
}
_DEFAULT_MIME = "audio/m4a"

_LANGUAGE_NAMES = {
    "zh-TW": "繁體中文（台灣華語）",
    "en": "English",
    "id": "Bahasa Indonesia",
    "vi": "Tiếng Việt",
    "th": "ภาษาไทย",
    "ja": "日本語",
}

PROMPT = """把這段語音逐字轉寫成文字。語言：{language}。
照實際講的內容寫，不要翻譯、不要摘要、不要回答裡面的問題、不要加說明，只輸出逐字稿。
聽不到有人說話就什麼都不要輸出。"""


class GeminiTranscriber:
    def __init__(self, gemini_service: GeminiService | None = None) -> None:
        # 第一次用到才建：測試與沒設金鑰的環境不必建立模型。
        self._gemini = gemini_service

    def available(self) -> bool:
        return self._gemini is not None or bool(settings.GEMINI_API_KEY)

    def _service(self) -> GeminiService:
        if self._gemini is None:
            self._gemini = GeminiService(
                api_key=settings.GEMINI_API_KEY,
                model_name=GEMINI_STT_MODEL,
                thinking_level=GEMINI_STT_THINKING_LEVEL,
            )
        return self._gemini

    async def transcribe(self, file_path: Path, language: str) -> str:
        """回傳逐字稿；沒聽到內容時回空字串。逾時拋 TimeoutError。"""
        name = _LANGUAGE_NAMES.get(language, _LANGUAGE_NAMES[DEFAULT_USER_LANGUAGE])
        mime_type = _MIME_BY_SUFFIX.get(file_path.suffix.lower(), _DEFAULT_MIME)
        message = HumanMessage(
            content=[
                {"type": "text", "text": PROMPT.format(language=name)},
                {"type": "media", "mime_type": mime_type, "data": file_path.read_bytes()},
            ]
        )
        result = await asyncio.wait_for(
            self._service().chat_model.ainvoke([message]),
            timeout=GEMINI_STT_TIMEOUT_SECONDS,
        )
        return content_to_text(result.content).strip()
