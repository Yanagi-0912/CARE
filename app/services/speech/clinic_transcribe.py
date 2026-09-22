"""看診錄音的逐字稿：華語走 Gemini、台語走台語 STT，用語者分離切段，但**不標是誰在講**。

這支跟 `gemini_stt.py` 是兩回事，不要混用：

- `gemini_stt.py` 處理 LINE 傳來的語音訊息，十秒上下、單一講者，走 LangChain 的
  `chat_model`，目標是快（中位數 1.4 秒）。
- 這支處理一整段門診，十分鐘上下、三到五個講者（醫師、長輩、陪的家屬或看護、
  跟診護理師，教學醫院還有實習醫師），目標是正確與可讀，慢一點沒關係。

### 流程（2026-09-22 用 30 分鐘華語、台語 podcast 各一實測後定的）

1. 切 5 分鐘一段，平行送 `gemini-3.5-transcribe`（開語者分離與詞級時間戳）。
   華語 30 分鐘 19 秒轉完；整檔送要 96 秒。
2. 找「有聲音、但 Gemini 一個詞都沒轉出來」的時段。這個模型碰到台語幾乎不出字
   （台語那份 30 分鐘只出 327 字），所以這些空白多半就是台語。
3. 空白併成 25 秒以內的段落，排隊送台語 STT（每 10 秒一段，理由見 `TaigiPacer`）。
   台語那份補回 8,574 字；整檔送 Gemini 只轉出 6,576 字，還被截掉最後 5 分鐘。
4. 兩邊照時間排回一份。

所以不用先判斷整份是華語還是台語，醫師講華語、長輩回台語也各走各的路。
實測 30 分鐘：華語 25 秒（沒送台語 STT）；全台語 19 分鐘、送了九十多段，其中
三、四分鐘是節流算錯（見 `TaigiPacer.run`），修正後推估 16 分鐘上下。門診多半
5～10 分鐘，全台語推估 3～6 分鐘。

語者標籤只在**單一次請求內**有意義：第一段的 spk:0 和第二段的 spk:0 不保證是同一個
人，所以標籤前面加段號，跨段一定換段——反正不顯示講者，多一個換行而已。台語 STT
沒有語者分離，它的每一段自成一段落。

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
import bisect
import functools
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

from app.core.config import settings
from app.services.speech import audio

logger = logging.getLogger(__name__)

# 專門做轉錄的模型（ai.google.dev/gemini-api/docs/transcribe）。語者分離只有這支有，
# `gemini_stt.py` 用的 gemini-3.5-flash-lite 是通用模型，沒有這個能力。
CLINIC_TRANSCRIBE_MODEL = "gemini-3.5-transcribe"

# 台灣華語為主；醫師講藥名、檢查名稱時常夾英文。
LANGUAGE_CODES = ("zh-TW", "en-US")

# 只整理前 30 分鐘，超過的在逐字稿最後註明。不是 API 限制（切段後每段只有 5 分鐘），
# 是記憶體與等待時間：30 分鐘的 16 kHz PCM 約 58 MB，要在 backend pod 裡整份解碼；
# 全程台語的 30 分鐘要等 16 分鐘上下（見模組說明）。門診很少超過。
MAX_AUDIO_SECONDS = 30 * 60
TRUNCATED_NOTE = "（錄音超過 30 分鐘，之後的內容沒有整理。）"

# Gemini 那頭送 mp3：同樣 5 分鐘，WAV 9.6 MB、64 kbps mp3 2.4 MB。2026-09-22 改送
# WAV 後 Gemini 從 18.5 秒變 31 秒，多出來的是上傳。
GEMINI_MP3_BIT_RATE = 64_000

# 5 分鐘一段平行送。2026-09-22 實測 30 分鐘華語：整檔送 96 秒（兩次），切 6 段平行
# 18.5～19 秒、字數相同（8,690 對 8,584）。台語整檔送會 MAX_TOKENS 截斷，切段則
# 幾乎不出字——正好讓沒轉到的時段交給台語 STT。
CHUNK_SECONDS = 300.0
# 在每段結尾前這麼長的範圍內找最安靜處下刀，同 audio.PAUSE_SEARCH_SECONDS 的理由。
CHUNK_PAUSE_SEARCH_SECONDS = 10.0

# 詞的時間戳前後各放寬這麼多才算「已轉到」：時間戳只到 0.1 秒，字尾的殘響不是新的話。
COVERED_PAD_SECONDS = 0.4
# 沒轉到的有聲時段短於這個就不理：咳嗽、椅子聲、一聲「嗯」。
GAP_MIN_SECONDS = 0.3
# 相隔不到這麼久的 Gemini 詞算同一串。
WORD_CLUSTER_JOIN_SECONDS = 1.0
# 一串至少這麼多個詞才算真的轉到華語；更零星的詞多半是 Gemini 聽台語時硬猜出來的
# （台語那份 30 分鐘裡散落 327 個詞），連同那段時間一起交給台語 STT 重轉。離線用
# 同一批回應重算：門檻 3 時華語只丟 2 個詞、送台語 0 段；台語從 119 段降到 95 段。
WORD_CLUSTER_MIN_WORDS = 3
# 兩段空白相隔（且中間沒有 Gemini 的詞）短於這個就併成一段送台語 STT。台語原型
# 用 1.5 秒切出 123 段、送了 20 分鐘（每 10 秒一段）；段數就是等待時間，能併就併。
TAIGI_JOIN_SECONDS = 4.0
# 併完仍短於這個的就不送。2026-09-22 華語那份用 1 秒送了 13 段，回來全是「啊」「嗯」
# 「哈哈哈哈」與一句憑空的「我遮遮濟相思」（Gemini 句子中間的短停頓）；離線重算
# 3 秒時是 0 段。代價是台語一兩秒的短回答（「有啦」）收不到。
TAIGI_PIECE_MIN_SECONDS = 3.0
# 見 TaigiPacer。
TAIGI_INTERVAL_SECONDS = 10.0
# 429 之後等多久再試一次。速率窗口是分鐘級的（taigi_client 的實測）。
TAIGI_RATE_LIMIT_WAIT_SECONDS = 60.0
# 每秒至少幾個字才算在講話。台語原型：片頭音樂 23 秒回 2 個字（0.09），
# 真的在講話的片段每秒 2～5 字。
TAIGI_MIN_CHARS_PER_SECOND = 0.3

_PUNCTUATION = re.compile(r"[\s，。？！、,.?!;:；：~～「」『』（）()]+")

# 逐字模式。SMART 會刪掉「嗯」「那個」、自動修正、還會把內容重排成條列，
# 對邊講邊看的即時字幕是優點，對看診原文是缺點：原文的用途就是讓家人核對
# 摘要有沒有寫歪，被模型整理過就失去核對的意義。
TRANSCRIBE_MODE = "VERBATIM"

# 單段（5 分鐘）的逾時。2026-09-22 實測每段 16.5～19 秒；整檔 30 分鐘也才 96～110 秒，
# 一段超過 120 秒一定是卡住了，放掉這段交給台語 STT 補。
TRANSCRIBE_TIMEOUT_SECONDS = 120.0



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

    2026-09-22 實測 SDK 給的是字串（`WordInfo.start_offset: str`），數字也照收；
    看不懂就回 None，時間軸只用來顯示，缺了不影響逐字稿本身。
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


@functools.lru_cache(maxsize=1)
def _converters() -> tuple[Any, Any, Any]:
    # 延後 import，理由同 `_get_client`。
    import opencc

    return opencc.OpenCC("s2tw"), opencc.OpenCC("s2t"), opencc.OpenCC("t2s")


def _looks_simplified(text: str) -> bool:
    """整份逐字稿是不是簡體。

    轉錄模型指定了 zh-TW，華語仍回簡體、台語卻回正體（2026-09-22 實測）。正體字
    不能再丟進 s2tw：「干擾」會變「幹擾」、「了解」變「瞭解」。所以要先判斷。

    數「轉正體會變的字」對「轉簡體會變的字」。台、周、了這類簡繁共用字會被算進
    前者，所以不能逐段判斷——「台北周末」四個字就會誤判。整份一起數差距很大：
    實測華語 2,379 對 0、台語 25 對 2,148。
    """
    _, s2t, t2s = _converters()
    simplified = sum(1 for c in text if s2t.convert(c) != c and t2s.convert(c) == c)
    traditional = sum(1 for c in text if t2s.convert(c) != c)
    return simplified > traditional


def _to_traditional(text: str) -> str:
    """簡體轉台灣正體。

    用 s2tw 而不是 s2twp：只換字形（头发→頭髮），不換用語（软件→軟體）。
    逐字稿是給家人核對摘要的原文，用語被改寫就不是原話了。
    """
    return _converters()[0].convert(text)


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
    if _looks_simplified("".join(segment.text for segment in segments)):
        # 逐段轉而不是逐詞轉：简繁一對多（发→發／髮）要靠前後文決定。
        segments = [
            Segment(text=_to_traditional(segment.text), start_seconds=segment.start_seconds)
            for segment in segments
        ]
    return ClinicTranscript(segments=tuple(segments), speaker_count=len(speakers))


def _extract_words(response: Any) -> list[dict[str, Any]]:
    """從回應裡挖出詞級註釋，整理成 `group_words_into_segments` 吃的
    `text` / `speaker` / `start_offset`，並照時間排好。

    2026-09-22 用真的 API 跑 30 分鐘華語與台語 podcast 核對過形狀：

    - 詞在 `candidates[].content.parts[].audio_transcription.words`，欄位是
      `word` / `start_offset`（`"1.200s"` 字串）/ `end_offset`。
    - 講者標在 part 上（`audio_transcription.speaker_label`，如 `"spk:0"`），不在詞上；
      一個 part 是一段連續同一人講的話。只有一個人講時整份一個 part、沒有標籤。
    - **part 不照時間排**：台語那份第一個 part 從 1012 秒開始、第二個從 27 秒。
      不排序的話逐字稿前後顛倒。

    找不到就拋錯而不是靜靜回空字串——靜靜回空的話，使用者會看到「這次沒錄到」，
    但真正的原因是我們解析錯了，那是最難查的一種失敗。
    """
    turns: list[list[dict[str, Any]]] = []
    for candidate in _field(response, "candidates") or []:
        content = _field(candidate, "content")
        parts = _field(content, "parts") if content is not None else None
        for part in parts or []:
            transcription = _field(part, "audio_transcription")
            if transcription is None:
                continue
            speaker = _field(transcription, "speaker_label")
            turn = [
                {
                    "text": _field(word, "word"),
                    "speaker": speaker,
                    "start_offset": _field(word, "start_offset"),
                    "end_offset": _field(word, "end_offset"),
                }
                for word in _field(transcription, "words") or []
            ]
            if turn:
                turns.append(turn)
    if turns:
        # 以 part 為單位照開頭時間排，不拆開逐詞排：兩人搶話時時間會重疊，
        # 逐詞排會把兩個人的詞交錯插在一起，切出一堆一兩個字的段。
        # 看不懂的時間戳排最後；sort 是穩定的，同一時間保持原本順序。
        def turn_start(turn: list[dict[str, Any]]) -> float:
            offset = _parse_offset_seconds(turn[0]["start_offset"])
            return float("inf") if offset is None else offset

        turns.sort(key=turn_start)
        return [word for turn in turns for word in turn]
    raise ClinicTranscribeError(
        "轉錄回應裡找不到詞級註釋；欄位名稱可能跟文件不同，需要用一次真實請求核對"
    )


@dataclass(frozen=True)
class _GeminiChunk:
    """一段 5 分鐘音檔的 Gemini 結果。時間已換成整份錄音的絕對秒數。"""

    words: list[dict[str, Any]]
    speaker_count: int
    failed: bool = False


def drop_sparse_words(
    words: list[dict[str, Any]],
    *,
    join_seconds: float = WORD_CLUSTER_JOIN_SECONDS,
    min_words: int = WORD_CLUSTER_MIN_WORDS,
) -> list[dict[str, Any]]:
    """丟掉零星的 Gemini 詞（理由見 WORD_CLUSTER_MIN_WORDS）。詞要照時間排好。"""
    kept: list[dict[str, Any]] = []
    cluster: list[dict[str, Any]] = []
    last_end: float | None = None
    for word in words:
        start = word["start"]
        if start is None:
            kept.append(word)
            continue
        if cluster and last_end is not None and start - last_end >= join_seconds:
            if len(cluster) >= min_words:
                kept.extend(cluster)
            cluster = []
        cluster.append(word)
        end = start if word["end"] is None else word["end"]
        last_end = end if len(cluster) == 1 or last_end is None else max(last_end, end)
    if len(cluster) >= min_words:
        kept.extend(cluster)
    return kept


def uncovered_voiced_spans(
    voiced: list[tuple[float, float]],
    covered: list[tuple[float, float]],
    *,
    pad: float = COVERED_PAD_SECONDS,
    min_seconds: float = GAP_MIN_SECONDS,
) -> list[tuple[float, float]]:
    """有聲、但 Gemini 沒轉出任何詞的時段。

    `covered` 是每個詞的 (開始, 結束)，前後各放寬 `pad` 秒：詞級時間戳只到 0.1 秒，
    邊界一點點的殘響不該被當成一段沒轉到的話。
    """
    cover = sorted((start - pad, end + pad) for start, end in covered)
    gaps: list[tuple[float, float]] = []
    k = 0
    for start, end in voiced:
        cursor = start
        while k < len(cover) and cover[k][1] <= cursor:
            k += 1
        j = k
        while j < len(cover) and cover[j][0] < end:
            if cover[j][0] > cursor:
                gaps.append((cursor, cover[j][0]))
            cursor = max(cursor, cover[j][1])
            j += 1
        if cursor < end:
            gaps.append((cursor, end))
    return [(a, b) for a, b in gaps if b - a >= min_seconds]


def group_gaps_for_taigi(
    gaps: list[tuple[float, float]],
    covered_starts: list[float],
    *,
    max_seconds: float = audio.MAX_STT_CHUNK_SECONDS,
    join_seconds: float = TAIGI_JOIN_SECONDS,
    min_seconds: float = TAIGI_PIECE_MIN_SECONDS,
) -> list[tuple[float, float]]:
    """把相近的空白併成一段送台語 STT；併起來不超過 `max_seconds`。

    只在兩段之間沒有 Gemini 的詞時才併：中間夾著已經轉好的華語，併進來會讓
    那句華語被台語模型再寫一次，逐字稿出現兩份。
    """
    starts = sorted(covered_starts)

    def words_between(lo: float, hi: float) -> bool:
        return bisect.bisect_left(starts, hi) > bisect.bisect_right(starts, lo)

    pieces: list[list[float]] = []
    for start, end in gaps:
        if pieces:
            prev_start, prev_end = pieces[-1]
            if (
                start - prev_end < join_seconds
                and end - prev_start <= max_seconds
                and not words_between(prev_end, start)
            ):
                pieces[-1][1] = end
                continue
        # 單一空白就超過上限的，留給送出前在停頓處切（見 _fill_with_taigi）。
        pieces.append([start, end])
    return [(a, b) for a, b in pieces if b - a >= min_seconds]


def plausible_taigi_text(text: str, seconds: float) -> bool:
    """台語 STT 對音樂、雜音也會吐字（實測 23 秒的片頭音樂回「臺灣」兩個字）。

    講話每秒至少一兩個字，每秒不到 `TAIGI_MIN_CHARS_PER_SECOND` 就當成沒在講話。
    """
    visible = _PUNCTUATION.sub("", text)
    return bool(visible) and len(visible) / max(seconds, 1.0) >= TAIGI_MIN_CHARS_PER_SECOND


class TaigiPacer:
    """台語 STT 的節流：兩次呼叫至少相隔 `interval` 秒，同一時間只送一段。

    金鑰與 LINE 語音訊息共用，速率上限每分鐘 10 次（taigi_client 的 429 實測）。
    看診錄音一次要送幾十段，照每 10 秒一段送就是每分鐘 6 次，留 4 次給聊天室的
    語音訊息——那邊撞到 429 會直接放掉台語、改用華語逐字稿回答（見 taigi_client）。
    同時送也沒有比較快：2026-09-22 實測 10 段同時送全部逾時，廠商伺服器一次只處理一個。
    """

    def __init__(
        self,
        interval: float = TAIGI_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._interval = interval
        self._clock = clock
        self._sleep = sleep
        self._lock: asyncio.Lock | None = None
        self._last: float | None = None

    async def run(self, fn: Callable[[], Awaitable[Any]]) -> Any:
        # 用到才建：asyncio.Lock 綁在第一次使用它的 event loop 上。
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self._last is not None:
                wait = self._interval - (self._clock() - self._last)
                if wait > 0:
                    await self._sleep(wait)
            # 從送出那一刻算：速率上限數的是每分鐘送出幾次。2026-09-22 原本從回應回來
            # 才算，每段實際間隔變成 10 秒加上辨識的 2～4 秒，30 分鐘台語多等了好幾分鐘。
            self._last = self._clock()
            return await fn()


class ClinicTranscriber:
    def __init__(
        self,
        client: Any | None = None,
        taigi_client: Any | None = None,
        pacer: TaigiPacer | None = None,
    ) -> None:
        # 第一次用到才建：測試與沒設金鑰的環境不必建立 client。
        self._client = client
        self._taigi = taigi_client
        # 全 app 共用一個：兩段錄音同時在轉，也要一起排隊，速率上限是跟著金鑰走的。
        self._pacer = pacer or TaigiPacer()

    def available(self) -> bool:
        return self._client is not None or bool(settings.GEMINI_API_KEY)

    def _get_client(self) -> Any:
        if self._client is None:
            # 延後 import：`app/services/speech/audio.py` 開頭那段註解記著，頂端 import
            # 大套件會讓 backend／scheduler pod 啟動約 30 秒就被 OOMKilled。
            from google import genai

            self._client = genai.Client(api_key=settings.GEMINI_API_KEY)
        return self._client

    def _get_taigi(self) -> Any | None:
        if self._taigi is None:
            from app.services.speech.taigi_client import TaigiClient

            client = TaigiClient()
            if not client.available():
                return None
            self._taigi = client
        return self._taigi

    async def transcribe(self, file_path: Path) -> ClinicTranscript:
        started = time.monotonic()
        pcm, rate = await asyncio.to_thread(
            audio.decode_to_pcm16_mono, file_path, audio.STT_SAMPLE_RATE
        )
        duration = len(pcm) / 2 / rate
        truncated = duration > MAX_AUDIO_SECONDS
        if truncated:
            pcm = audio.slice_pcm(pcm, rate, 0, MAX_AUDIO_SECONDS)

        # 5 分鐘一段，在停頓處下刀，免得切在字中間。
        chunks = await asyncio.to_thread(
            audio.split_on_pauses,
            pcm,
            rate,
            max_seconds=CHUNK_SECONDS,
            search_seconds=CHUNK_PAUSE_SEARCH_SECONDS,
        )
        offsets: list[float] = []
        cursor = 0.0
        for chunk in chunks:
            offsets.append(cursor)
            cursor += len(chunk) / 2 / rate

        results = await asyncio.gather(
            *(
                self._transcribe_chunk(chunk, rate, offset, index)
                for index, (chunk, offset) in enumerate(zip(chunks, offsets))
            )
        )
        if all(result.failed for result in results):
            raise ClinicTranscribeError("每一段 Gemini 轉錄都失敗")
        gemini_done = time.monotonic()

        words = drop_sparse_words(
            sorted(
                (word for result in results for word in result.words),
                key=lambda w: float("inf") if w["start"] is None else w["start"],
            )
        )
        voiced = await asyncio.to_thread(audio.voiced_spans, pcm, rate)
        covered = [
            (word["start"], word["end"]) for word in words if word["start"] is not None
        ]
        gaps = uncovered_voiced_spans(voiced, covered)
        pieces = group_gaps_for_taigi(gaps, [start for start, _ in covered])
        taigi_segments = await self._fill_with_taigi(pcm, rate, pieces)

        # Gemini 的詞才需要判斷簡繁（台語 STT 本來就寫正體），所以先單獨成段再併。
        transcript = group_words_into_segments(words)
        segments = sorted(
            [*transcript.segments, *taigi_segments],
            key=lambda seg: float("inf") if seg.start_seconds is None else seg.start_seconds,
        )
        if truncated:
            segments.append(Segment(text=TRUNCATED_NOTE, start_seconds=None))

        logger.info(
            "stage=clinic_transcribe seconds=%.0f chunks=%d chunk_failed=%d "
            "gemini_sec=%.1f taigi_pieces=%d taigi_kept=%d total_sec=%.1f truncated=%s",
            duration,
            len(chunks),
            sum(result.failed for result in results),
            gemini_done - started,
            len(pieces),
            len(taigi_segments),
            time.monotonic() - started,
            truncated,
        )
        return ClinicTranscript(
            segments=tuple(segments),
            # 講者標籤只在同一段請求內有意義，所以取各段的最大值而不是加總。
            speaker_count=max((result.speaker_count for result in results), default=0),
        )

    async def _transcribe_chunk(
        self, pcm: bytes, rate: int, offset: float, index: int
    ) -> _GeminiChunk:
        """一段失敗不拖垮整份：那段的時間全算「沒轉到」，交給台語 STT 補。"""
        from google.genai import types

        config = types.GenerateContentConfig(
            audio_transcription_config=types.AudioTranscriptionConfig(
                language_codes=list(LANGUAGE_CODES),
                diarization=True,
                # 詞級時間戳是白拿的：它和語者分離一樣、都只跟 custom_vocabulary 衝突，
                # 而熱詞已經為了語者分離放棄了，所以開它不再多付任何代價。
                # 用途是找出沒轉到的時段，以及讓家人知道某句話在第幾分鐘。
                word_timestamp=True,
                mode=TRANSCRIBE_MODE,
            )
        )
        try:
            response = await asyncio.wait_for(
                self._get_client().aio.models.generate_content(
                    model=CLINIC_TRANSCRIBE_MODEL,
                    contents=[
                        types.Part.from_bytes(
                            data=await asyncio.to_thread(
                                audio.encode_mp3, pcm, rate, bit_rate=GEMINI_MP3_BIT_RATE
                            ),
                            mime_type="audio/mpeg",
                        )
                    ],
                    config=config,
                ),
                timeout=TRANSCRIBE_TIMEOUT_SECONDS,
            )
            raw_words = _extract_words(response)
        except ClinicTranscribeError:
            # 沒有詞級註釋＝這段一個字都沒轉出來（整段台語時就是這樣），不是錯誤。
            return _GeminiChunk(words=[], speaker_count=0)
        except Exception as exc:  # noqa: BLE001 - 單段失敗改由台語 STT 補
            logger.warning(
                "stage=clinic_transcribe 第 %d 段 Gemini 失敗：%s", index, type(exc).__name__
            )
            return _GeminiChunk(words=[], speaker_count=0, failed=True)

        candidates = _field(response, "candidates") or []
        finish_reason = _field(candidates[0], "finish_reason") if candidates else None
        finish = getattr(finish_reason, "value", finish_reason)
        if finish not in (None, "STOP"):
            # MAX_TOKENS 代表輸出被截斷、後段沒轉（2026-09-22 台語 30 分鐘整檔送實測）。
            # 沒轉到的部分會被當成空白交給台語 STT，所以這裡只記下來。
            logger.warning("stage=clinic_transcribe 第 %d 段 finish=%s", index, finish)

        words: list[dict[str, Any]] = []
        speakers: set[str] = set()
        for word in raw_words:
            start = _parse_offset_seconds(word["start_offset"])
            end = _parse_offset_seconds(word["end_offset"])
            if word["speaker"]:
                speakers.add(str(word["speaker"]))
            words.append(
                {
                    "text": word["text"],
                    # 講者標籤只在同一段請求內有意義：第一段的 spk:0 和第二段的
                    # spk:0 不保證是同一人，所以加上段號，跨段一定換段。
                    "speaker": f"{index}:{word['speaker']}",
                    "start": None if start is None else offset + start,
                    "end": None if end is None else offset + max(end, start or end),
                    "start_offset": None if start is None else offset + start,
                }
            )
        return _GeminiChunk(words=words, speaker_count=len(speakers))

    async def _fill_with_taigi(
        self, pcm: bytes, rate: int, pieces: list[tuple[float, float]]
    ) -> list[Segment]:
        """沒轉到的時段送台語 STT。任何一段失敗都只是那段沒有字，不影響其他。"""
        if not pieces:
            return []
        client = self._get_taigi()
        if client is None:
            logger.warning("stage=clinic_transcribe 沒有台語 STT 金鑰，%d 段沒補", len(pieces))
            return []

        segments: list[Segment] = []
        for start, end in pieces:
            # 超過台語 STT 上限的在最安靜處切開（同聊天室語音，audio.split_on_pauses）。
            cursor = start
            for part in audio.split_on_pauses(audio.slice_pcm(pcm, rate, start, end), rate):
                seconds = len(part) / 2 / rate
                text = await self._taigi_once(client, audio.pcm16_to_wav(part, rate))
                if text and plausible_taigi_text(text, seconds):
                    segments.append(Segment(text=text, start_seconds=cursor))
                cursor += seconds
        return segments

    async def _taigi_once(self, client: Any, wav: bytes) -> str:
        """撞到 429 就等窗口過去再試一次；聊天室那邊不等，這裡是背景工作等得起。"""
        for attempt in range(2):
            try:
                return await self._pacer.run(
                    lambda: asyncio.to_thread(client.transcribe_wav, wav)
                )
            except Exception as exc:  # noqa: BLE001 - 單段失敗只少那一段
                if attempt == 0 and "429" in str(exc):
                    await asyncio.sleep(TAIGI_RATE_LIMIT_WAIT_SECONDS)
                    continue
                logger.warning("stage=clinic_transcribe 台語 STT 失敗：%s", type(exc).__name__)
                return ""
        return ""
