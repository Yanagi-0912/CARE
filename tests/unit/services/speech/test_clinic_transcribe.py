"""看診逐字稿的切段邏輯。

這裡驗的是「語者分離只拿來換段、不對外宣稱誰是誰」這條產品規則有沒有被程式守住，
以及分離出錯（把一個人拆成兩個、把兩個人併成一個、整段沒標）時不會壞掉。
"""

import pytest

from app.services.speech.clinic_transcribe import (
    ClinicTranscribeError,
    Segment,
    _extract_words,
    group_words_into_segments,
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
    assert words == [{"text": "你好", "speaker": "spk:0", "start_offset": "1.200s"}]


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
