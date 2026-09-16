"""看診錄音的逐字稿：整檔送 Gemini 轉錄，用語者分離切段，但**不標是誰在講**。

這支跟 `gemini_stt.py` 是兩回事，不要混用：

- `gemini_stt.py` 處理 LINE 傳來的語音訊息，十秒上下、單一講者，走 LangChain 的
  `chat_model`，目標是快（中位數 1.4 秒）。
- 這支處理一整段門診，十分鐘上下、三到五個講者（醫師、長輩、陪的家屬或看護、
  跟診護理師，教學醫院還有實習醫師），目標是正確與可讀，慢一點沒關係。

### 為什麼不沿用 `audio.split_on_pauses()`

那支把音檔切成 15～25 秒（`MAX_STT_CHUNK_SECONDS`，因為 flash-lite 吃 37 秒的音檔
後段會變成重複亂句）。但語者標籤只在**單一次請求內**有意義：第一段的 spk_1 和第二段
的 spk_1 不保證是同一個人，切了就對不起來。所以這裡整檔送，改用專門做轉錄的
`gemini-3.5-transcribe`，它本來就吃得下長音檔。

### 為什麼放棄熱詞

官方明寫 custom_vocabulary 不能和 speaker diarization 或詞級時間戳並用
（ai.google.dev/gemini-api/docs/transcribe）。兩者只能二選一，這裡選語者分離，
藥名改用**這位長輩自己的用藥清單**在事後標示（見
`app/services/clinic_transcript/drug_hints.py`）——母體從全庫 56,886 個品名縮到
個位數，比拿逐字稿去全庫模糊比對安全得多（全庫模糊比對換掉一個劑量數字約 2%
必然釘錯，見 `drug_catalog_service._match_by_fuzzy` 的實測註解）。

### 為什麼切了段卻不標「醫師」「病人」

官方寫「最多 8 位講者，3 位以上的講者歸屬是實驗性質」。診間預設就在實驗區，
實際會發生的錯誤是把一個人拆成兩個、或把兩個人併成一個。

標了「講者 2」而它其實混了護理師和家屬，讀的人會被那個編號誤導；只做段落分隔的話，
同一種錯誤最多是多一個換行，沒有人被誤導。所以 `Segment` 刻意不帶講者身分，
`speaker` 只用來決定哪裡換段，切完就丟。誰是醫師交給讀的人判斷——家人看到
「你最近有沒有頭暈」接著「有，晚上比較嚴重」，一秒就知道，而系統從頭到尾
沒有宣稱任何事，因此不可能生出一句假的醫囑。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from app.core.config import settings

logger = logging.getLogger(__name__)

# 專門做轉錄的模型（ai.google.dev/gemini-api/docs/transcribe）。語者分離只有這支有，
# `gemini_stt.py` 用的 gemini-3.5-flash-lite 是通用模型，沒有這個能力。
CLINIC_TRANSCRIBE_MODEL = "gemini-3.5-transcribe"

# 台灣華語為主；醫師講藥名、檢查名稱時常夾英文。
LANGUAGE_CODES = ("zh-TW", "en-US")

# 官方：一般請求吃得下 1 小時，但開了語者分離或詞級時間戳就降到 30 分鐘（同上）。
# 這裡是硬上限，超過就不送——先擋在前面，比讓 API 回錯誤好解釋。
MAX_AUDIO_SECONDS = 30 * 60

# 逐字模式。SMART 會刪掉「嗯」「那個」、自動修正、還會把內容重排成條列，
# 對邊講邊看的即時字幕是優點，對看診原文是缺點：原文的用途就是讓家人核對
# 摘要有沒有寫歪，被模型整理過就失去核對的意義。
TRANSCRIBE_MODE = "VERBATIM"

# 還沒量過。門診十分鐘的音檔要跑多久沒有實測數字，這個值只是「不要無限等」的閘門，
# 不是根據任何量測訂的。第一次真的跑過之後要回來改成有依據的值。
TRANSCRIBE_TIMEOUT_SECONDS = 300.0

_MIME_BY_SUFFIX = {
    ".m4a": "audio/m4a",
    ".mp4": "audio/m4a",
    ".aac": "audio/aac",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".webm": "audio/webm",
}
_DEFAULT_MIME = "audio/m4a"


class ClinicTranscribeError(RuntimeError):
    """轉錄失敗。呼叫端要把音檔留著讓使用者重試，不要當成「沒有內容」。"""


@dataclass(frozen=True)
class Segment:
    """一段連續、由同一個人講的話。

    刻意沒有 speaker 欄位，理由見模組開頭：語者身分不對外，只用來決定哪裡換段。
    """

    text: str
    start_seconds: float | None


@dataclass(frozen=True)
class ClinicTranscript:
    segments: tuple[Segment, ...]
    # 這次分離出幾個講者。不顯示給使用者，只進 log——用來事後回答
    # 「診間到底幾個人在講」這種只能靠實際資料回答的問題。
    speaker_count: int

    @property
    def text(self) -> str:
        return "\n".join(segment.text for segment in self.segments)


def _parse_offset_seconds(raw: Any) -> float | None:
    """把 `"0.100s"` 這種 protobuf Duration 的字面值轉成秒。

    SDK 會不會先幫忙轉成數字沒驗證過，所以兩種都收；看不懂就回 None，
    時間軸只用來顯示，缺了不影響逐字稿本身。
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw.removesuffix("s"))
        except ValueError:
            return None
    return None


def _field(word: Any, name: str) -> Any:
    """詞級註釋可能是 SDK 物件，也可能是 REST 回來的 dict，兩種都要讀得到。"""
    if isinstance(word, dict):
        return word.get(name)
    return getattr(word, name, None)


class _Missing:
    """`None` 是合法的 speaker 值（模型沒標），所以「還沒開始」要用別的哨兵。"""


_MISSING = _Missing()


def _join(parts: list[str]) -> str:
    """把詞接回句子。

    中文詞之間不加空白，英文詞之間要加，否則「Amlodipine 5 毫克」會黏成
    「Amlodipine5毫克」。判斷方式是看接縫兩側：兩邊都是 ASCII 的字母或數字才補空白。
    藥名常是中英夾雜，這個接縫剛好最容易出錯。
    """
    out = ""
    for part in parts:
        if out and out[-1].isascii() and out[-1].isalnum():
            if part[:1].isascii() and part[:1].isalnum():
                out += " "
        out += part
    return out


def group_words_into_segments(words: Iterable[Any]) -> ClinicTranscript:
    """把詞級註釋按「講者換人」併成段。

    分離失敗或模型沒給 speaker（例如全程只有一個人講）時，所有詞會落在同一段，
    這是正確行為：沒有證據說換過人，就不要製造段落分隔。
    """
    segments: list[Segment] = []
    speakers: set[str] = set()
    current_speaker: Any = _MISSING
    current_words: list[str] = []
    current_start: float | None = None

    def flush() -> None:
        if not current_words:
            return
        text = _join(current_words).strip()
        if text:
            segments.append(Segment(text=text, start_seconds=current_start))

    for word in words:
        text = _field(word, "text")
        if not text:
            continue
        speaker = _field(word, "speaker")
        if speaker:
            speakers.add(str(speaker))
        if speaker != current_speaker:
            flush()
            current_speaker = speaker
            current_words = []
            current_start = _parse_offset_seconds(_field(word, "start_offset"))
        current_words.append(text)

    flush()
    return ClinicTranscript(segments=tuple(segments), speaker_count=len(speakers))


def _extract_words(response: Any) -> list[Any]:
    """從回應裡挖出詞級註釋。

    回應形狀還沒用真的 API 驗證過（要花錢，等 James 同意跑一次）。目前照文件寫的
    `text` / `speaker` / `start_offset` 三個欄位找，且把幾種可能的巢狀位置都試過，
    找不到就拋錯而不是靜靜回空字串——靜靜回空的話，使用者會看到「這次沒錄到」，
    但真正的原因是我們解析錯了，那是最難查的一種失敗。
    """
    candidates = ("words", "word_annotations", "transcription_words")
    for name in candidates:
        words = _field(response, name)
        if words:
            return list(words)
    for attr in ("transcription", "result"):
        nested = _field(response, attr)
        if nested is None:
            continue
        for name in candidates:
            words = _field(nested, name)
            if words:
                return list(words)
    raise ClinicTranscribeError(
        "轉錄回應裡找不到詞級註釋；欄位名稱可能跟文件不同，需要用一次真實請求核對"
    )


class ClinicTranscriber:
    def __init__(self, client: Any | None = None) -> None:
        # 第一次用到才建：測試與沒設金鑰的環境不必建立 client。
        self._client = client

    def available(self) -> bool:
        return self._client is not None or bool(settings.GEMINI_API_KEY)

    def _get_client(self) -> Any:
        if self._client is None:
            # 延後 import：`app/services/speech/audio.py` 開頭那段註解記著，頂端 import
            # 大套件會讓 backend／scheduler pod 啟動約 30 秒就被 OOMKilled。
            from google import genai

            self._client = genai.Client(api_key=settings.GEMINI_API_KEY)
        return self._client

    async def transcribe(self, file_path: Path) -> ClinicTranscript:
        from google.genai import types

        audio = file_path.read_bytes()
        mime_type = _MIME_BY_SUFFIX.get(file_path.suffix.lower(), _DEFAULT_MIME)
        config = types.GenerateContentConfig(
            audio_transcription_config=types.AudioTranscriptionConfig(
                language_codes=list(LANGUAGE_CODES),
                diarization=True,
                # 詞級時間戳是白拿的：它和語者分離一樣、都只跟 custom_vocabulary 衝突，
                # 而熱詞已經為了語者分離放棄了，所以開它不再多付任何代價。
                # 用途是讓家人知道某句話在第幾分鐘，音檔本身轉完就刪。
                word_timestamp=True,
                mode=TRANSCRIBE_MODE,
            )
        )
        try:
            response = await asyncio.wait_for(
                self._get_client().aio.models.generate_content(
                    model=CLINIC_TRANSCRIBE_MODEL,
                    contents=[types.Part.from_bytes(data=audio, mime_type=mime_type)],
                    config=config,
                ),
                timeout=TRANSCRIBE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            raise ClinicTranscribeError(
                f"轉錄超過 {TRANSCRIBE_TIMEOUT_SECONDS:.0f} 秒"
            ) from exc

        transcript = group_words_into_segments(_extract_words(response))
        logger.info(
            "stage=clinic_transcribe segments=%d speakers=%d chars=%d",
            len(transcript.segments),
            transcript.speaker_count,
            len(transcript.text),
        )
        return transcript
