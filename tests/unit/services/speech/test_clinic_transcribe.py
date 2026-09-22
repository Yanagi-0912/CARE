"""看診逐字稿的切段邏輯。

這裡驗的是「語者分離只拿來換段、不對外宣稱誰是誰」這條產品規則有沒有被程式守住，
以及分離出錯（把一個人拆成兩個、把兩個人併成一個、整段沒標）時不會壞掉。
"""

import asyncio
import math
from array import array
from pathlib import Path

import pytest

from app.services.speech import audio
from app.services.speech.clinic_transcribe import (
    ClinicTranscribeError,
    ClinicTranscriber,
    Segment,
    TaigiPacer,
    _extract_words,
    drop_sparse_words,
    group_gaps_for_taigi,
    group_words_into_segments,
    plausible_taigi_text,
    uncovered_voiced_spans,
)


def _w(text: str, speaker: str | None = None, start: str | None = None) -> dict:
    word: dict = {"text": text}
    if speaker is not None:
        word["speaker"] = speaker
    if start is not None:
        word["start_offset"] = start
    return word


def test_換人就換段():
    transcript = group_words_into_segments(
        [
            _w("你最近", "spk_1", "0.1s"),
            _w("有沒有頭暈", "spk_1"),
            _w("有", "spk_2", "3.2s"),
            _w("晚上比較嚴重", "spk_2"),
        ]
    )
    assert [s.text for s in transcript.segments] == ["你最近有沒有頭暈", "有晚上比較嚴重"]
    assert transcript.speaker_count == 2


def test_段落不帶講者身分():
    """產品規則：切得出段，但不准說出是誰。"""
    assert set(Segment.__dataclass_fields__) == {"text", "start_seconds"}


def test_全程沒有講者標記時併成一段():
    """分離失敗或只有一個人講話：沒有證據換過人，就不該製造段落分隔。"""
    transcript = group_words_into_segments([_w("今天"), _w("血壓有點高")])
    assert [s.text for s in transcript.segments] == ["今天血壓有點高"]
    assert transcript.speaker_count == 0


def test_中英夾雜的藥名不會黏在一起():
    """藥名是中英夾雜最常出現的地方，接縫錯了整句就難讀。"""
    transcript = group_words_into_segments(
        [_w("我幫你把", "s1"), _w("Amlodipine", "s1"), _w("5", "s1"), _w("毫克", "s1")]
    )
    assert transcript.segments[0].text == "我幫你把Amlodipine 5毫克"


def test_同一個人被拆成兩個標籤只會多一個段落分隔():
    """三人以上的歸屬是實驗性質，這種錯誤要能安全吸收——最多多一個換行，不能掉字。"""
    transcript = group_words_into_segments(
        [_w("藥先", "spk_1"), _w("吃完", "spk_3"), _w("再回來", "spk_1")]
    )
    assert "".join(s.text for s in transcript.segments) == "藥先吃完再回來"
    assert len(transcript.segments) == 3


def test_時間戳取每段第一個詞():
    transcript = group_words_into_segments(
        [_w("第一段", "a", "0.5s"), _w("接著講", "a", "1.5s"), _w("第二段", "b", "9s")]
    )
    assert [s.start_seconds for s in transcript.segments] == [0.5, 9.0]


def test_看不懂的時間戳不影響逐字稿():
    transcript = group_words_into_segments([_w("有內容", "a", "不是時間")])
    assert transcript.segments[0].text == "有內容"
    assert transcript.segments[0].start_seconds is None


def test_空白詞被略過而不是產生空段():
    transcript = group_words_into_segments([_w("", "a"), _w("有字", "a")])
    assert [s.text for s in transcript.segments] == ["有字"]


def test_解析不到詞級註釋要拋錯而不是回空字串():
    """靜靜回空的話，使用者看到的是「這次沒錄到」，真正的原因卻是我們解析錯了。"""
    with pytest.raises(ClinicTranscribeError):
        _extract_words({"candidates": [{"content": {"parts": [{"text": "嗨"}]}}]})


def _part(speaker: str | None, *words: tuple[str, str]) -> dict:
    """2026-09-22 真實 API 回應的形狀：講者標在 part 上，詞的欄位叫 `word`。"""
    transcription: dict = {
        "text": "".join(w for w, _ in words),
        "words": [
            {"word": w, "start_offset": start, "end_offset": start} for w, start in words
        ],
    }
    if speaker is not None:
        transcription["speaker_label"] = speaker
    return {"text": transcription["text"], "audio_transcription": transcription}


def _response(*parts: dict) -> dict:
    return {"candidates": [{"content": {"parts": list(parts)}}]}


def test_照真實回應形狀抽出詞與講者():
    words = _extract_words(_response(_part("spk:0", ("你好", "1.200s"))))
    assert words == [
        {"text": "你好", "speaker": "spk:0", "start_offset": "1.200s", "end_offset": "1.200s"}
    ]


def test_part_沒照時間排也要排回來():
    """真實回應：台語那份第一個 part 從 1012 秒開始、第二個從 27 秒。"""
    transcript = group_words_into_segments(
        _extract_words(
            _response(
                _part("spk:0", ("後來", "1012.2s")),
                _part("spk:1", ("一開始", "27.1s")),
            )
        )
    )
    assert [s.text for s in transcript.segments] == ["一開始", "後來"]


def test_兩人搶話時不會把詞交錯拆碎():
    """時間重疊的兩段各自保持完整，不逐詞排序。"""
    transcript = group_words_into_segments(
        _extract_words(
            _response(
                _part("spk:0", ("藥", "10.0s"), ("先", "10.4s"), ("停", "10.8s")),
                _part("spk:1", ("好", "10.2s"), ("喔", "10.6s")),
            )
        )
    )
    assert [s.text for s in transcript.segments] == ["藥先停", "好喔"]


def test_一個人講完全程時沒有標籤也只有一段():
    """真實回應：單人獨白整份一個 part、沒有 speaker_label。"""
    transcript = group_words_into_segments(
        _extract_words(_response(_part(None, ("今天", "0.1s"), ("看診", "0.5s"))))
    )
    assert [s.text for s in transcript.segments] == ["今天看診"]
    assert transcript.speaker_count == 0


def test_簡體轉成台灣正體():
    """指定 zh-TW 仍回簡體（實測）。發／髮一對多，要整段轉才靠得到前後文。"""
    transcript = group_words_into_segments(
        [_w("医生说", "a"), _w("头发", "a"), _w("掉", "a"), _w("跟", "a"), _w("发烧", "a")]
    )
    assert transcript.segments[0].text == "醫生說頭髮掉跟發燒"


def test_已經是正體的逐字稿不再轉():
    """台語實測回正體；再丟進 s2tw 會把「干擾」變「幹擾」、「了解」變「瞭解」。"""
    transcript = group_words_into_segments([_w("會干擾", "a"), _w("我了解啦", "b")])
    assert [s.text for s in transcript.segments] == ["會干擾", "我了解啦"]


def test_只換字形不換用語():
    """逐字稿是原話，不能把「软件」改寫成「軟體」。"""
    transcript = group_words_into_segments([_w("软件", "a")])
    assert transcript.segments[0].text == "軟件"


# ---- 找出 Gemini 沒轉到的時段，交給台語 STT ----


def test_有聲但沒被詞蓋到的時段才算空白():
    gaps = uncovered_voiced_spans([(0.0, 10.0)], [(1.0, 3.0), (6.0, 7.0)], pad=0.0)
    assert gaps == [(0.0, 1.0), (3.0, 6.0), (7.0, 10.0)]


def test_詞的邊界放寬之後太短的空白不理():
    """咳嗽、一聲「嗯」不值得佔一次台語 STT 的額度。"""
    assert uncovered_voiced_spans([(0.0, 3.3)], [(0.0, 3.0)], pad=0.1, min_seconds=0.3) == []


def test_全程沒轉到就整段都是空白():
    """整段台語時 Gemini 幾乎不出字（實測 30 分鐘 327 字）。"""
    assert uncovered_voiced_spans([(0.0, 5.0), (6.0, 9.0)], []) == [(0.0, 5.0), (6.0, 9.0)]


def test_相近的空白併成一段送():
    pieces = group_gaps_for_taigi([(0.0, 3.0), (4.0, 8.0)], [], join_seconds=4.0)
    assert pieces == [(0.0, 8.0)]


def test_中間夾著已轉好的華語就不併():
    """併進來的話那句華語會被台語模型再寫一次，逐字稿出現兩份。"""
    pieces = group_gaps_for_taigi([(0.0, 3.0), (4.0, 8.0)], [3.5], join_seconds=4.0)
    assert pieces == [(0.0, 3.0), (4.0, 8.0)]


def test_併段不超過台語_STT_的長度上限():
    pieces = group_gaps_for_taigi(
        [(0.0, 10.0), (11.0, 20.0), (21.0, 30.0)], [], max_seconds=25.0, join_seconds=4.0
    )
    assert pieces == [(0.0, 20.0), (21.0, 30.0)]


def test_零星的_Gemini_詞不算轉到():
    """台語裡 Gemini 會散落幾個硬猜的詞；連續三個以上才算真的華語。"""
    def w(start: float) -> dict:
        return {"text": "x", "speaker": "0:a", "start": start, "end": start + 0.2, "start_offset": start}

    words = [w(0.0), w(5.0), w(5.3), w(5.6), w(20.0), w(20.4)]
    assert [x["start"] for x in drop_sparse_words(words)] == [5.0, 5.3, 5.6]


def test_台語_STT_對音樂吐的零星字不收():
    """實測 23 秒片頭音樂回「臺灣」兩個字。"""
    assert not plausible_taigi_text("臺灣", 23.0)
    assert plausible_taigi_text("我感覺伊足像一个足嚴格的師傅", 5.0)
    assert not plausible_taigi_text("，。", 2.0)


def test_台語_STT_排隊送且兩次之間隔開():
    """速率上限每分鐘 10 次、與聊天室語音共用；同時送實測全部逾時。"""
    now = [0.0]
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)
        now[0] += seconds

    pacer = TaigiPacer(interval=10.0, clock=lambda: now[0], sleep=fake_sleep)

    async def call() -> str:
        now[0] += 2.0  # 一次呼叫花 2 秒
        return "ok"

    async def run() -> list[str]:
        return [await pacer.run(call) for _ in range(3)]

    assert asyncio.run(run()) == ["ok", "ok", "ok"]
    # 第一次不等；之後從上一次「送出」算起補滿 10 秒（呼叫本身花掉的 2 秒算在裡面）。
    assert waits == [8.0, 8.0]


# ---- 整條流程：華語給 Gemini、台語給台語 STT ----

RATE = audio.STT_SAMPLE_RATE


def _tone(seconds: float) -> bytes:
    samples = array("h", (int(8000 * math.sin(2 * math.pi * 220 * i / RATE)) for i in range(int(seconds * RATE))))
    return samples.tobytes()


def _silence(seconds: float) -> bytes:
    return bytes(int(seconds * RATE) * 2)


class _FakeModels:
    def __init__(self, response: dict | Exception) -> None:
        self.response = response
        self.calls = 0

    async def generate_content(self, **_: object) -> dict:
        self.calls += 1
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class _FakeGenai:
    def __init__(self, response: dict | Exception) -> None:
        self.models = _FakeModels(response)
        self.aio = self


class _FakeTaigi:
    def __init__(self, text: str) -> None:
        self.text = text
        self.durations: list[float] = []

    def available(self) -> bool:
        return True

    def transcribe_wav(self, wav: bytes) -> str:
        self.durations.append((len(wav) - 44) / 2 / RATE)
        return self.text


async def _no_sleep(_: float) -> None:
    return None


def _wav_file(tmp_path: Path, pcm: bytes) -> Path:
    path = tmp_path / "visit.wav"
    path.write_bytes(audio.pcm16_to_wav(pcm, RATE))
    return path


def test_華語給_Gemini_其餘有聲時段給台語_STT(tmp_path: Path):
    # 0–4 秒醫師講華語（Gemini 轉到），5–9 秒長輩講台語（Gemini 沒轉到）。
    path = _wav_file(tmp_path, _tone(4) + _silence(1) + _tone(4) + _silence(2))
    gemini = _FakeGenai(
        _response(_part("spk:0", ("血压", "0.2s"), ("有点高", "0.8s"), ("药继续吃", "1.4s")))
    )
    # _part 的 end_offset 與 start 相同；把最後一個詞拉到 3.9 秒，蓋滿前 4 秒。
    gemini.models.response["candidates"][0]["content"]["parts"][0]["audio_transcription"][
        "words"
    ][-1]["end_offset"] = "3.9s"
    taigi = _FakeTaigi("好，我知影矣，藥仔會照時食")
    transcriber = ClinicTranscriber(
        client=gemini, taigi_client=taigi, pacer=TaigiPacer(interval=0, sleep=_no_sleep)
    )

    transcript = asyncio.run(transcriber.transcribe(path))

    assert [s.text for s in transcript.segments] == ["血壓有點高藥繼續吃", "好，我知影矣，藥仔會照時食"]
    assert len(taigi.durations) == 1
    assert 3.0 < taigi.durations[0] < 5.0


def test_全華語就不叫台語_STT(tmp_path: Path):
    path = _wav_file(tmp_path, _tone(3))
    gemini = _FakeGenai(_response(_part(None, ("今天", "0.0s"), ("看診", "0.5s"), ("順利", "1.0s"))))
    gemini.models.response["candidates"][0]["content"]["parts"][0]["audio_transcription"][
        "words"
    ][-1]["end_offset"] = "3.0s"
    taigi = _FakeTaigi("不該被叫")
    transcriber = ClinicTranscriber(client=gemini, taigi_client=taigi)

    transcript = asyncio.run(transcriber.transcribe(path))

    assert [s.text for s in transcript.segments] == ["今天看診順利"]
    assert taigi.durations == []


def test_Gemini_整段失敗就拋錯(tmp_path: Path):
    path = _wav_file(tmp_path, _tone(2))
    transcriber = ClinicTranscriber(
        client=_FakeGenai(RuntimeError("503")), taigi_client=_FakeTaigi("x")
    )
    with pytest.raises(ClinicTranscribeError):
        asyncio.run(transcriber.transcribe(path))


def test_Gemini_一個字都沒轉出來時全部交給台語_STT(tmp_path: Path):
    """整段台語：Gemini 回的 part 沒有 audio_transcription，不是錯誤。"""
    path = _wav_file(tmp_path, _tone(4))
    taigi = _FakeTaigi("今仔日先生講血壓傷懸")
    transcriber = ClinicTranscriber(
        client=_FakeGenai({"candidates": [{"content": {"parts": [{"text": ""}]}}]}),
        taigi_client=taigi,
        pacer=TaigiPacer(interval=0, sleep=_no_sleep),
    )

    transcript = asyncio.run(transcriber.transcribe(path))

    assert [s.text for s in transcript.segments] == ["今仔日先生講血壓傷懸"]


def test_找有聲時段不受壓縮過的錄音影響():
    """podcast 底噪 -19 dBFS：只看「底噪 + 10 dB」會把整段說話判成安靜。"""
    loud_floor = array("h", [3500] * RATE * 2)  # 約 -19 dBFS 的持續聲音
    spans = audio.voiced_spans(loud_floor.tobytes() + _silence(1), RATE)
    assert spans and spans[0][1] >= 1.9
