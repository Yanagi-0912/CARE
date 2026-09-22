"""看診錄音從上傳到可讀的整條流程。

上傳那個請求只做兩件事：把音檔寫到暫存檔、建一筆 `processing` 的紀錄，然後立刻回傳。
轉錄、摘要、藥名標示都在背景跑（見 `app/models/clinic_transcript.py` 的 `RecordStatus`）。

### 音檔的生命週期

寫進暫存檔 → 背景轉錄讀它 → 不論成功失敗都刪掉。刪除放在 `finally`，因為
「轉錄失敗」和「音檔留在磁碟上」是兩個獨立的問題，後者比前者嚴重：診間錄音留在
pod 的檔案系統裡，重啟前沒有任何東西會清掉它。

音檔不重試。轉錄失敗就是失敗，紀錄標 `failed` 讓使用者知道這次沒了。留著音檔等重試，
等於把一段診間對話無限期存在磁碟上，換取一個不確定會不會成功的第二次機會。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Protocol

from app.models.clinic_transcript import (
    ClinicVisitRecord,
    ClinicVisitSummaryModel,
    ConsentMode,
    DrugHintNote,
    TranscriptSegment,
)
from app.repositories.clinic_transcript_repository import ClinicTranscriptRepository
from app.services.clinic_transcript import drug_hints as hints_module
from app.services.clinic_transcript.summarizer import ClinicVisitSummarizer
from app.services.speech.clinic_transcribe import (
    ClinicTranscribeError,
    ClinicTranscriber,
)

logger = logging.getLogger(__name__)

# 給使用者看的失敗說明。不放技術細節：他能做的只有「知道這次沒了」和「下次再錄」。
FAILURE_TRANSCRIBE = "這次的錄音沒有轉成文字，可能是聲音太小或收訊中斷。下次看診可以再錄一次。"
FAILURE_EMPTY = "這段錄音裡沒有聽到說話的聲音。"


class MedicationLister(Protocol):
    async def list_active_by_user(self, user_id: str, date_str: str) -> list: ...


class Notifier(Protocol):
    async def notify_ready(self, record: ClinicVisitRecord) -> None: ...

    async def notify_failed(self, record: ClinicVisitRecord) -> None: ...


class ClinicTranscriptService:
    def __init__(
        self,
        transcriber: ClinicTranscriber,
        summarizer: ClinicVisitSummarizer,
        medication_repository: Optional[object] = None,
        repository: type[ClinicTranscriptRepository] = ClinicTranscriptRepository,
        notifier: Optional[Notifier] = None,
    ) -> None:
        self._transcriber = transcriber
        self._summarizer = summarizer
        self._medications = medication_repository
        self._repository = repository
        # 沒注入時不推播（測試與沒有 LINE 的環境）。見 notifier.py 的收件人規則。
        self._notifier = notifier

    async def start(
        self,
        *,
        user_id: str,
        created_by_user_id: str,
        consent: ConsentMode,
        appointment_id: Optional[str] = None,
        hospital_name: str = "",
        department: str = "",
    ) -> ClinicVisitRecord:
        """建一筆 `processing` 的紀錄。實際的轉錄由 `process()` 在背景做。"""
        record = ClinicVisitRecord(
            user_id=user_id,
            created_by_user_id=created_by_user_id,
            consent=consent,
            appointment_id=appointment_id,
            hospital_name=hospital_name,
            department=department,
        )
        return await self._repository.create(record)

    async def _medication_names(self, user_id: str) -> list[str]:
        """拿這位長輩當日仍有效的用藥清單。

        拿不到就回空清單：藥名標示是加值，清單讀失敗不該讓整份逐字稿跟著沒有。
        """
        if self._medications is None:
            return []
        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            medications = await self._medications.list_active_by_user(user_id, today)
        except Exception as exc:  # noqa: BLE001 - 加值路徑，失敗不得影響主線
            logger.warning("讀不到用藥清單，這次不標藥名：%s", type(exc).__name__)
            return []
        return hints_module.medication_names(medications)

    async def process(self, record: ClinicVisitRecord, audio_path: Path) -> None:
        """背景工作：轉錄 → 摘要 → 標藥名 → 寫回。音檔一律刪掉。"""
        record_id = record.id
        assert record_id is not None, "process() 只接受已經存進資料庫的紀錄"
        try:
            try:
                transcript = await self._transcriber.transcribe(audio_path)
            except ClinicTranscribeError as exc:
                logger.warning("stage=clinic_process 轉錄失敗 record=%s：%s", record_id, exc)
                await self._fail(record, FAILURE_TRANSCRIBE)
                return
            except Exception as exc:  # noqa: BLE001 - 背景工作，例外不得逸散
                logger.exception("stage=clinic_process 轉錄爆炸 record=%s", record_id)
                del exc
                await self._fail(record, FAILURE_TRANSCRIBE)
                return

            if not transcript.segments:
                await self._fail(record, FAILURE_EMPTY)
                return

            text = transcript.text
            # 摘要失敗會回空摘要而不是拋錯，所以這裡不必再包一層：逐字稿本身
            # 已經夠有用，摘要沒有只是少一層加值。
            summary = await self._summarizer.summarize(text)
            names = await self._medication_names(record.user_id)
            found = hints_module.find_hints(text, names)

            segments = [
                TranscriptSegment(text=segment.text, start_seconds=segment.start_seconds)
                for segment in transcript.segments
            ]
            summary_model = ClinicVisitSummaryModel(
                main_points=list(summary.main_points),
                medication_changes=[
                    {"description": change.description, "quote": change.quote}
                    for change in summary.medication_changes
                ],
                next_visit=summary.next_visit,
                reminders=list(summary.reminders),
                unclear=list(summary.unclear),
                truncated=summary.truncated,
            )
            hints = [
                DrugHintNote(
                    medication_name=hint.medication_name,
                    heard=hint.heard,
                    start=hint.start,
                    score=hint.score,
                )
                for hint in found
            ]
            await self._repository.mark_ready(
                record_id,
                segments=[segment.model_dump() for segment in segments],
                summary=summary_model.model_dump(),
                drug_hints=[hint.model_dump() for hint in hints],
                speaker_count=transcript.speaker_count,
            )
            # 推播卡片直接放摘要（2026-09-22 起），要交出寫回之後的樣子。
            record = record.model_copy(
                update={
                    "status": "ready",
                    "segments": segments,
                    "summary": summary_model,
                    "drug_hints": hints,
                    "speaker_count": transcript.speaker_count,
                }
            )
            logger.info(
                "stage=clinic_process 完成 record=%s segments=%d hints=%d",
                record_id,
                len(transcript.segments),
                len(found),
            )
            if self._notifier is not None:
                await self._notifier.notify_ready(record)
        finally:
            # 「轉錄失敗」和「音檔留在磁碟上」是兩個問題，後者比較嚴重：
            # 沒有任何東西會在 pod 重啟前清掉它。
            try:
                audio_path.unlink(missing_ok=True)
            except OSError as exc:
                logger.error("刪不掉暫存音檔 %s：%s", audio_path, exc)

    async def _fail(self, record: ClinicVisitRecord, reason: str) -> None:
        assert record.id is not None
        await self._repository.mark_failed(record.id, reason)
        if self._notifier is not None:
            await self._notifier.notify_failed(record)

    async def list_records(self, user_id: str, limit: int = 20) -> list[ClinicVisitRecord]:
        return await self._repository.list_for_user(user_id, limit)

    async def get_record(self, record_id: str, user_id: str) -> Optional[ClinicVisitRecord]:
        return await self._repository.get(record_id, user_id)

    async def delete_record(self, record_id: str, user_id: str) -> bool:
        return await self._repository.delete(record_id, user_id)
