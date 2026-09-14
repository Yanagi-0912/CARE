"""台語語音的轉檔與切段：用合成訊號，不讀外部檔案。"""

import array
import io
import math
import wave

import av

from app.services.speech import audio

RATE = 16_000


def _tone(seconds: float, rate: int = RATE) -> bytes:
    n = int(seconds * rate)
    return array.array(
        "h", (int(8000 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(n))
    ).tobytes()


def _silence(seconds: float, rate: int = RATE) -> bytes:
    return bytes(int(seconds * rate) * 2)


def _seconds(pcm: bytes, rate: int = RATE) -> float:
    return len(pcm) / 2 / rate


def test_short_audio_is_not_split():
    pcm = _tone(3)
    assert audio.split_on_pauses(pcm, RATE) == [pcm]


def test_long_audio_is_cut_inside_pauses():
    # 0–18 秒有聲、18–18.5 靜音、18.5–40 有聲、40–40.5 靜音、40.5–50.5 有聲
    pcm = _tone(18) + _silence(0.5) + _tone(21.5) + _silence(0.5) + _tone(10)

    chunks = audio.split_on_pauses(pcm, RATE)

    assert b"".join(chunks) == pcm
    assert len(chunks) == 3
    assert all(_seconds(c) <= audio.MAX_STT_CHUNK_SECONDS for c in chunks)
    first_cut = _seconds(chunks[0])
    second_cut = first_cut + _seconds(chunks[1])
    assert 18.0 <= first_cut <= 18.5
    assert 40.0 <= second_cut <= 40.5


def test_pcm16_to_wav_header_and_frames():
    pcm = _tone(0.2)
    with wave.open(io.BytesIO(audio.pcm16_to_wav(pcm, RATE))) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, RATE)
        assert w.readframes(w.getnframes()) == pcm


def _encode_m4a(pcm: bytes, rate: int) -> io.BytesIO:
    buf = io.BytesIO()
    with av.open(buf, mode="w", format="mp4") as container:
        stream = container.add_stream("aac", rate=rate, layout="mono")
        frame = av.AudioFrame(format="s16", layout="mono", samples=len(pcm) // 2)
        frame.planes[0].update(pcm)
        frame.sample_rate = rate
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    buf.seek(0)
    return buf


# CARE 收到的 LINE 錄音是 m4a，台語 STT 不收，必須先解得開。
def test_decode_m4a_to_16k_mono():
    m4a = _encode_m4a(_tone(2, rate=44_100), 44_100)

    pcm, rate = audio.decode_to_pcm16_mono(m4a, audio.STT_SAMPLE_RATE)

    assert rate == audio.STT_SAMPLE_RATE
    assert abs(_seconds(pcm, rate) - 2.0) < 0.1


# 1600cca 在模組頂端 import av，backend 與 scheduler 啟動時多吃的記憶體讓兩個 pod
# 超過 512 Mi 上限被 OOMKilled。啟動會匯入的模組都不可以順便載入 PyAV。
def test_importing_speech_callers_does_not_load_pyav():
    import subprocess
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[4]
    code = (
        "import sys\n"
        "import app.services.line_messaging.reply.tts_service\n"
        "import app.services.media.mutimedia_processor\n"
        "print('av' in sys.modules)\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=repo_root, capture_output=True, text=True, check=True
    )
    assert out.stdout.strip().splitlines()[-1] == "False"


def test_encode_mp3_with_fast_compression_level_decodes_back():
    pcm = _tone(1.0, rate=16_000)

    mp3 = audio.encode_mp3(pcm, 16_000, bit_rate=48_000, compression_level=7)
    back, rate = audio.decode_to_pcm16_mono(io.BytesIO(mp3))

    assert rate == 16_000
    assert abs(_seconds(back, rate) - 1.0) < 0.1


def test_encode_mp3_decodes_back_with_same_length():
    pcm = _tone(1.5, rate=22_050)

    mp3 = audio.encode_mp3(pcm, 22_050, bit_rate=48_000)
    back, rate = audio.decode_to_pcm16_mono(io.BytesIO(mp3))

    assert rate == 22_050
    assert abs(_seconds(back, rate) - 1.5) < 0.1
    assert audio.pcm_duration_ms(pcm, 22_050) == 1500
