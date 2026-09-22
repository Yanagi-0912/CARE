"""語音檔的解碼、切段與轉 mp3，給台語 STT／TTS 用。

用 PyAV（wheel 內附 FFmpeg 函式庫）而不是在映像裡 apt 裝 ffmpeg：後端映像沒有
ffmpeg，也不必呼叫外部程式。

PyAV 在用到時才載入（見 `_av`）：它會帶進 FFmpeg 函式庫，常駐記憶體多 20 MB 以上。
backend 與 scheduler 啟動時都會匯入這個模組（scheduler 從不處理音檔），而兩者平常
就吃到上限的九成（2026-09-14 實測 475／467 Mi，上限 512 Mi）；1600cca 在模組頂端
直接 import，兩個 pod 啟動約 30 秒就被 OOMKilled。

為什麼兩頭都要轉檔：
- 台語 STT 只吃 wav／mp3／ogg。2026-09-14 實測送 m4a、webm 都回 HTTP 500
  "Format not recognised"（廠商文件寫支援 m4a，實際不收），而 CARE 收到的
  LINE 錄音存成 .m4a（見 mutimedia_processor.MEDIA_EXTENSIONS）。
- 台語 TTS 回 WAV；LINE 語音訊息這邊一直送的是 edge-tts 的 mp3。LINE 能不能播
  WAV 查不到官方條文，照已經在線上跑的格式送最保險。
"""

from __future__ import annotations

import array
import io
import math
import sys
import wave
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

if TYPE_CHECKING:
    import av

# 廠商文件的建議格式：PCM WAV、單聲道、16 kHz。
STT_SAMPLE_RATE = 16_000

# 單段送 STT 的上限。2026-09-14 實測：37 秒的音檔兩次後段都變成重複的亂句
# （「攏毋著，攏毋著」），同一檔在 24.3 秒的停頓切開後兩段都辨識正確。
# 真正的門檻沒量到，取剛驗證過的 24 秒上下。
MAX_STT_CHUNK_SECONDS = 25.0
# 在上限前這麼長的範圍內找最安靜的地方下刀，所以每段介於 15～25 秒。同一份實測
# 音檔的句間停頓每 2～4 秒一次；那是合成語音，真人的停頓比較不規則，但 10 秒內
# 沒有任何換氣的長句很少見。真的找不到停頓也只是切在最小聲的地方。
PAUSE_SEARCH_SECONDS = 10.0
# 找停頓用的視窗。實測句間停頓（-35 dB 以下）至少 0.25 秒，50 毫秒的視窗放得進去。
PAUSE_WINDOW_SECONDS = 0.05

# 16-bit 單聲道：一個樣本 2 bytes。
_BYTES_PER_SAMPLE = 2


def _av():
    import av  # 延遲載入，理由見模組說明

    return av


def decode_to_pcm16_mono(
    source: str | Path | BinaryIO, sample_rate: int | None = None
) -> tuple[bytes, int]:
    """解碼 FFmpeg 認得的音檔，回傳 (16-bit 單聲道 PCM, 取樣率)。

    `sample_rate` 為 None 時保留原取樣率。
    """
    av = _av()
    with av.open(str(source) if isinstance(source, Path) else source) as container:
        stream = container.streams.audio[0]
        rate = sample_rate or stream.rate
        resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)
        pcm = bytearray()
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                pcm += _plane_bytes(out)
        for out in resampler.resample(None):
            pcm += _plane_bytes(out)
    return bytes(pcm), rate


def _plane_bytes(frame: av.AudioFrame) -> bytes:
    # 平面緩衝區可能帶對齊用的尾巴，只取實際樣本。
    return bytes(frame.planes[0])[: frame.samples * _BYTES_PER_SAMPLE]


def pcm16_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(_BYTES_PER_SAMPLE)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def pcm_duration_ms(pcm: bytes, sample_rate: int) -> int:
    return round(len(pcm) / _BYTES_PER_SAMPLE / sample_rate * 1000)


def split_on_pauses(
    pcm: bytes,
    sample_rate: int,
    *,
    max_seconds: float = MAX_STT_CHUNK_SECONDS,
    search_seconds: float = PAUSE_SEARCH_SECONDS,
    window_seconds: float = PAUSE_WINDOW_SECONDS,
) -> list[bytes]:
    """把過長的 PCM 在停頓處切成每段不超過 `max_seconds` 的片段。

    停頓＝搜尋範圍內平均振幅最小的視窗。只用標準函式庫：正式映像沒有 numpy
    （見 pyproject.toml dev 群組的說明）。
    """
    samples = _samples(pcm)

    max_len = int(max_seconds * sample_rate)
    if len(samples) <= max_len:
        return [pcm]

    window = max(1, int(window_seconds * sample_rate))
    search_len = min(int(search_seconds * sample_rate), max_len - window)
    chunks: list[bytes] = []
    start = 0
    while len(samples) - start > max_len:
        lo = start + max_len - search_len
        hi = start + max_len - window
        quietest = min(
            range(lo, hi + 1, window),
            key=lambda i: sum(map(abs, samples[i : i + window])),
        )
        cut = quietest + window // 2
        chunks.append(pcm[start * _BYTES_PER_SAMPLE : cut * _BYTES_PER_SAMPLE])
        start = cut
    chunks.append(pcm[start * _BYTES_PER_SAMPLE :])
    return chunks


def _samples(pcm: bytes) -> array.array:
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % _BYTES_PER_SAMPLE])
    if sys.byteorder == "big":  # PCM 是 little-endian；只影響振幅計算
        samples.byteswap()
    return samples


# 有聲的門檻：比底噪（第 20 百分位的視窗音量）高 10 dB，但最多到 -35 dBFS。
# 只用底噪：壓縮過的錄音（podcast）幾乎沒有安靜的時候，2026-09-22 實測底噪
# -19 dBFS，門檻變成 -9 dBFS，30 分鐘說話只剩 1 秒被判有聲。-35 是上面
# PAUSE_WINDOW_SECONDS 那段實測的句間停頓音量。寧可多判有聲：多判的代價是
# 多送幾段給台語 STT，少判則是那段話從逐字稿消失。
VOICE_ABOVE_FLOOR_DB = 10.0
VOICE_MAX_THRESHOLD_DBFS = -35.0
# 兩段有聲之間的縫短於這個就併起來：換氣、字與字之間的停頓不算「沒在講話」。
VOICE_JOIN_SECONDS = 0.3


def voiced_spans(
    pcm: bytes, sample_rate: int, *, window_seconds: float = PAUSE_WINDOW_SECONDS
) -> list[tuple[float, float]]:
    """回傳有人在出聲的時段（秒）。只看音量，分不出說話和音樂。"""
    samples = _samples(pcm)
    window = max(1, int(window_seconds * sample_rate))
    levels: list[float] = []
    for i in range(0, len(samples) - window + 1, window):
        chunk = samples[i : i + window]
        rms = math.sqrt(sum(x * x for x in chunk) / window)
        levels.append(20 * math.log10(max(rms, 1.0) / 32768))
    if not levels:
        return []
    floor = sorted(levels)[len(levels) // 5]
    threshold = min(floor + VOICE_ABOVE_FLOOR_DB, VOICE_MAX_THRESHOLD_DBFS)

    spans: list[list[float]] = []
    for k, level in enumerate(levels):
        if level <= threshold:
            continue
        start, end = k * window_seconds, (k + 1) * window_seconds
        if spans and start - spans[-1][1] < VOICE_JOIN_SECONDS:
            spans[-1][1] = end
        else:
            spans.append([start, end])
    return [(start, end) for start, end in spans]


def slice_pcm(pcm: bytes, sample_rate: int, start: float, end: float) -> bytes:
    """取 [start, end) 秒那一段。"""
    lo = int(start * sample_rate) * _BYTES_PER_SAMPLE
    hi = int(end * sample_rate) * _BYTES_PER_SAMPLE
    return pcm[max(0, lo) : max(0, hi)]


def encode_mp3(
    pcm: bytes, sample_rate: int, *, bit_rate: int, compression_level: int | None = None
) -> bytes:
    """16-bit 單聲道 PCM → mp3。

    `compression_level` 是 LAME 的演算法品質（0 最好最慢、9 最快），None 用預設。
    """
    av = _av()
    options = {} if compression_level is None else {"compression_level": str(compression_level)}
    buf = io.BytesIO()
    with av.open(buf, mode="w", format="mp3") as container:
        stream = container.add_stream(
            "libmp3lame", rate=sample_rate, layout="mono", options=options
        )
        stream.bit_rate = bit_rate
        frame = av.AudioFrame(
            format="s16", layout="mono", samples=len(pcm) // _BYTES_PER_SAMPLE
        )
        frame.planes[0].update(pcm[: frame.samples * _BYTES_PER_SAMPLE])
        frame.sample_rate = sample_rate
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return buf.getvalue()
