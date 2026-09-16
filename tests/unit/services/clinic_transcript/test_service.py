"""看診錄音的整條流程：驗音檔一定被刪、失敗不會讓紀錄消失、加值失敗不影響主線。"""

from pathlib import Path

import pytest

from app.models.clinic_transcript import ClinicVisitRecord
from app.services.clinic_transcript.service import (
    FAILURE_EMPTY,
    FAILURE_TRANSCRIBE,
    ClinicTranscriptService,
)
from app.services.speech.clinic_transcribe import (
    ClinicTranscribeError,
    ClinicTranscript,
    Segment,
)


class _FakeRepo:
    def __init__(self):
        self.ready = None
        self.failed = None

    async def create(self, record):
        return record.model_copy(update={"id": "rec1"})

    async def mark_ready(self, record_id, **kwargs):
        self.ready = (record_id, kwargs)

    async def mark_failed(self, record_id, reason, collection=None):
        self.failed = (record_id, reason)


class _FakeTranscriber:
    def __init__(self, transcript=None, exc=None):
        self.transcript, self.exc = transcript, exc

    async def transcribe(self, path):
        if self.exc:
            raise self.exc
        return self.transcript


class _FakeSummarizer:
    def __init__(self, summary=None, exc=None):
        self.summary, self.exc = summary, exc

    async def summarize(self, text):
        if self.exc:
            raise self.exc
        from app.services.clinic_transcript.summarizer import ClinicVisitSummary

        return self.summary or ClinicVisitSummary(main_points=("血壓還好",))


class _FakeMeds:
    def __init__(self, meds=None, exc=None):
        self.meds, self.exc = meds or [], exc

    async def list_active_by_user(self, user_id, date_str):
        if self.exc:
            raise self.exc
        return self.meds


class _Med:
    def __init__(self, name):
        self.name = name
        self.generic_name = None


def _record():
    return ClinicVisitRecord(
        id="rec1", user_id="u1", created_by_user_id="u1", consent="doctor_agreed"
    )


def _audio(tmp_path: Path) -> Path:
    path = tmp_path / "visit.m4a"
    path.write_bytes(b"fake")
    return path


def _transcript():
    return ClinicTranscript(
        segments=(
            Segment(text="你最近有沒有頭暈", start_seconds=0.1),
            Segment(text="思樂康持續性藥校錠先停", start_seconds=3.0),
        ),
        speaker_count=2,
    )


@pytest.mark.asyncio
async def test_成功時寫回逐字稿與藥名標示(tmp_path):
    repo = _FakeRepo()
    service = ClinicTranscriptService(
        _FakeTranscriber(_transcript()),
        _FakeSummarizer(),
        _FakeMeds([_Med("思樂康持續性藥效錠50毫克")]),
        repository=repo,
    )
    await service.process(_record(), _audio(tmp_path))

    record_id, payload = repo.ready
    assert record_id == "rec1"
    assert len(payload["segments"]) == 2
    assert payload["speaker_count"] == 2
    assert payload["drug_hints"][0]["medication_name"] == "思樂康持續性藥效錠50毫克"
    # 只標示不改寫：存的是逐字稿聽到的字，不是清單上的正確藥名。
    assert "藥校錠" in payload["drug_hints"][0]["heard"]


@pytest.mark.asyncio
async def test_存進去的段落沒有講者欄位(tmp_path):
    """整個功能的安全核心：切得出段，但不准說是誰。"""
    repo = _FakeRepo()
    service = ClinicTranscriptService(
        _FakeTranscriber(_transcript()), _FakeSummarizer(), repository=repo
    )
    await service.process(_record(), _audio(tmp_path))
    assert set(repo.ready[1]["segments"][0]) == {"text", "start_seconds"}


@pytest.mark.asyncio
async def test_轉錄失敗留下失敗紀錄而不是刪掉(tmp_path):
    repo = _FakeRepo()
    service = ClinicTranscriptService(
        _FakeTranscriber(exc=ClinicTranscribeError("逾時")), _FakeSummarizer(), repository=repo
    )
    await service.process(_record(), _audio(tmp_path))
    assert repo.failed == ("rec1", FAILURE_TRANSCRIBE)
    assert repo.ready is None


@pytest.mark.asyncio
async def test_轉錄爆炸也不讓例外逸散(tmp_path):
    """背景工作沒有人接例外，逸散出去就是 pod 的 log 裡一堆 traceback。"""
    repo = _FakeRepo()
    service = ClinicTranscriptService(
        _FakeTranscriber(exc=RuntimeError("boom")), _FakeSummarizer(), repository=repo
    )
    await service.process(_record(), _audio(tmp_path))
    assert repo.failed[1] == FAILURE_TRANSCRIBE


@pytest.mark.asyncio
async def test_沒聽到聲音給不一樣的說明(tmp_path):
    """「沒錄到聲音」和「轉錄壞了」對使用者的下一步不同。"""
    repo = _FakeRepo()
    service = ClinicTranscriptService(
        _FakeTranscriber(ClinicTranscript(segments=(), speaker_count=0)),
        _FakeSummarizer(),
        repository=repo,
    )
    await service.process(_record(), _audio(tmp_path))
    assert repo.failed == ("rec1", FAILURE_EMPTY)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "transcriber",
    [_FakeTranscriber(_transcript()), _FakeTranscriber(exc=ClinicTranscribeError("x"))],
)
async def test_不論成功失敗音檔都被刪掉(tmp_path, transcriber):
    """診間錄音留在 pod 的檔案系統上，重啟前沒有任何東西會清掉它。"""
    path = _audio(tmp_path)
    service = ClinicTranscriptService(transcriber, _FakeSummarizer(), repository=_FakeRepo())
    await service.process(_record(), path)
    assert not path.exists()


@pytest.mark.asyncio
async def test_讀不到用藥清單仍然產生逐字稿(tmp_path):
    """藥名標示是加值，清單讀失敗不該讓整份逐字稿跟著沒有。"""
    repo = _FakeRepo()
    service = ClinicTranscriptService(
        _FakeTranscriber(_transcript()),
        _FakeSummarizer(),
        _FakeMeds(exc=RuntimeError("mongo 掛了")),
        repository=repo,
    )
    await service.process(_record(), _audio(tmp_path))
    assert repo.ready is not None
    assert repo.ready[1]["drug_hints"] == []


@pytest.mark.asyncio
async def test_建立時是處理中狀態(tmp_path):
    service = ClinicTranscriptService(
        _FakeTranscriber(_transcript()), _FakeSummarizer(), repository=_FakeRepo()
    )
    record = await service.start(
        user_id="u1", created_by_user_id="u2", consent="self_recap"
    )
    assert record.status == "processing"
    assert record.consent == "self_recap"
