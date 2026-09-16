from __future__ import annotations

import json
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pymongo.errors import PyMongoError

from app.models.appointment import AppointmentReminder
from app.models.consultation import (
    ConsultationSummary,
    ConsultationSummarizeRequest,
)
from app.models.chat_message import ChatMessage
from app.models.medication import (
    Medication,
    MedicationReminderWithMedications,
    ReminderEntry,
    TAIPEI_TZ,
)
from app.services.consultation.consultation_service import ConsultationService
from app.services.consultation.scheduler import ConsultationDailySummaryScheduler
from app.services.medical.symptom_classification.urgency import (
    URGENCY_EMERGENCY,
    UrgencyVerdict,
)
from app.schemas import MedicalFacility
from app.services.medical.symptom_classification.symptom_department_service import (
    RESULT_FALLBACK,
    RESULT_SUGGESTION,
    SymptomTriageResult,
)
from app.services.medical.symptom_classification.symptom_table import (
    DepartmentCandidate,
)
from app.services.rag.claim_verification.service import VerificationResult
from app.tools.claim_tools import _to_flex_message_text
from app.tools.medical_tools import request_location_quick_reply
from resources.flex_messages.medical_messages.emergency_condition_flex_message import (
    build_emergency_condition_flex,
)
from resources.flex_messages.medical_messages.facility_brief_flex_message import (
    generate_facility_list_flex_message,
)
from resources.flex_messages.medical_messages.facility_detail_flex_message import (
    generate_facility_detail_flex_message,
)
from resources.flex_messages.medical_messages.symptom_department_flex_message import (
    build_symptom_department_flex,
)
from resources.flex_messages.official_site_flex_message import (
    OFFICIAL_SITE_KEY,
    generate_official_site_flex_message,
)


class FakeChatHistoryRepository:
    def __init__(self) -> None:
        self.messages: dict[str, list[ChatMessage]] = {}

    async def append_message(self, line_id: str, message: ChatMessage) -> None:
        self.messages.setdefault(line_id, []).append(message)

    async def list_messages(self, line_id: str) -> list[ChatMessage]:
        return list(self.messages.get(line_id, []))

    async def list_line_ids(self) -> list[str]:
        return sorted(self.messages)


class FakeRepository:
    def __init__(self) -> None:
        self.summary: ConsultationSummary | None = None
        self.summaries: list[ConsultationSummary] = []
        self.by_date: dict[tuple[str, date], ConsultationSummary] = {}

    async def get_summary_by_date(self, line_id: str, target_date: date):
        if (line_id, target_date) in self.by_date:
            return self.by_date[(line_id, target_date)]
        if (
            self.summary
            and self.summary.line_id == line_id
            and self.summary.summary_date == target_date
        ):
            return self.summary
        return None

    async def get_latest_summary(self, line_id: str):
        return (
            self.summary if self.summary and self.summary.line_id == line_id else None
        )

    async def upsert_summary(self, summary: ConsultationSummary):
        self.summary = summary
        self.by_date[(summary.line_id, summary.summary_date)] = summary
        return summary

    async def get_all_summaries(self, line_id: str):
        return [summary for summary in self.summaries if summary.line_id == line_id]


class FakeUserProfileService:
    def __init__(self, language: str | None = "zh-TW") -> None:
        self.language = language

    async def get_user_settings(self, line_id: str) -> dict:
        return {"language": self.language}


class FakeMedicationService:
    def __init__(self) -> None:
        self.reminders: list[MedicationReminderWithMedications] = []

    async def get_user_reminders_with_medications(self, user_id: str):
        return [reminder for reminder in self.reminders if reminder.user_id == user_id]


class FakeAppointmentRepository:
    def __init__(self) -> None:
        self.appointments: list[AppointmentReminder] = []
        self.day_end_after: datetime | None = None
        self.error: Exception | None = None

    async def list_by_user(self, user_id: str, *, day_end_after=None):
        if self.error is not None:
            raise self.error
        self.day_end_after = day_end_after
        return [a for a in self.appointments if a.user_id == user_id]


@pytest.fixture
def consultation_service() -> ConsultationService:
    fake_store = FakeChatHistoryRepository()
    fake_repo = FakeRepository()
    fake_user_profile_service = FakeUserProfileService()
    fake_gemini = SimpleNamespace(chat_model=SimpleNamespace(ainvoke=AsyncMock()))
    return ConsultationService(
        chat_history_repository=fake_store,
        repository=fake_repo,
        gemini_service=fake_gemini,
        user_profile_service=fake_user_profile_service,
        medication_service=FakeMedicationService(),
        appointment_repository=FakeAppointmentRepository(),
    )


# 測試當資料庫有該日期的諮詢摘要時，get_view 可以正確回傳該摘要內容。
@pytest.mark.asyncio
async def test_get_view_prefers_summary(
    consultation_service: ConsultationService,
):
    summary = ConsultationSummary(
        line_id="U123",
        summary_date=date(2026, 5, 17),
        summary="今天主要是腸胃不適",
        language="zh-TW",
        created_at=datetime.now(timezone.utc),
    )
    consultation_service._repository.summary = summary

    summary_res = await consultation_service.get_view("U123", date(2026, 5, 17))

    assert summary_res is not None
    assert summary_res.summary == "今天主要是腸胃不適"


# 測試當資料庫沒有該日期的諮詢摘要時，get_view 會回傳 None，而不會 fallback 到原始訊息。
@pytest.mark.asyncio
async def test_get_view_without_summary_does_not_fallback_to_raw(
    consultation_service: ConsultationService,
):
    summary_res = await consultation_service.get_view("U123")

    assert summary_res is None


# 測試能正確過濾出指定 line_id 的所有歷史摘要，而不會混入其他人的資料。
@pytest.mark.asyncio
async def test_list_summary_history_returns_repository_data(
    consultation_service: ConsultationService,
):
    consultation_service._repository.summaries = [
        ConsultationSummary(
            line_id="U123",
            summary_date=date(2026, 5, 26),
            summary="5/26 摘要",
            language="zh-TW",
            created_at=datetime.now(timezone.utc),
        ),
        ConsultationSummary(
            line_id="U999",
            summary_date=date(2026, 5, 26),
            summary="別人摘要",
            language="en",
            created_at=datetime.now(timezone.utc),
        ),
    ]

    summaries = await consultation_service.get_all_summaries("U123")

    assert len(summaries) == 1
    assert summaries[0].summary == "5/26 摘要"


# 驗證 get_raw_view 能正確從對話紀錄庫（Chat History）撈出特定使用者的原始對話內容
@pytest.mark.asyncio
async def test_get_raw_view_returns_messages(
    consultation_service: ConsultationService,
):
    msg = ChatMessage(
        line_id="U123",
        message_type="text",
        content="肚子痛",
        timestamp=datetime(2026, 5, 17, 8, 0, tzinfo=timezone.utc),
    )
    await consultation_service._chat_history_repository.append_message("U123", msg)

    messages = await consultation_service.get_raw_view(
        "U123", date(2026, 5, 17), language="zh-TW"
    )

    assert len(messages) == 1
    assert messages[0].content == "肚子痛"


# 呼叫 summarize 時，會使用經由 LLM 生成的文字，並成功寫入 Repo。
@pytest.mark.asyncio
async def test_summarize_uses_generated_text(
    consultation_service: ConsultationService,
):
    msg = ChatMessage(
        line_id="U123",
        message_type="text",
        content="今天肚子痛",
        timestamp=datetime(2026, 5, 17, 8, 0, tzinfo=timezone.utc),
    )
    await consultation_service._chat_history_repository.append_message("U123", msg)

    with patch.object(
        consultation_service,
        "_generate_summary",
        new=AsyncMock(return_value="摘要完成"),
    ):
        summary = await consultation_service.summarize(
            "U123", ConsultationSummarizeRequest(target_date=date.today())
        )

    assert summary.summary == "摘要完成"
    assert summary.language == "zh-TW"
    assert consultation_service._repository.summary is not None
    assert consultation_service._repository.summary.summary_date == date(2026, 5, 17)


@pytest.mark.asyncio
async def test_summarize_passes_user_language_into_prompt(
    consultation_service: ConsultationService,
):
    msg = ChatMessage(
        line_id="U123",
        message_type="text",
        content="我今天頭痛",
        timestamp=datetime(2026, 5, 17, 8, 0, tzinfo=timezone.utc),
    )
    await consultation_service._chat_history_repository.append_message("U123", msg)

    # Mock invoke_structured_output 回傳一個含有必要欄位的 dict
    async def mock_structured(prompt: str, json_schema: dict) -> dict:
        return {key: "測試摘要" for key in json_schema.get("properties", {}).keys()}

    consultation_service._gemini_service.invoke_structured_output = AsyncMock(
        side_effect=mock_structured
    )
    consultation_service._user_profile_service.language = "en"

    summary = await consultation_service.summarize(
        "U123", ConsultationSummarizeRequest(target_date=date.today(), force=True)
    )

    assert summary.language == "en"
    consultation_service._gemini_service.invoke_structured_output.assert_awaited_once()
    prompt = consultation_service._gemini_service.invoke_structured_output.call_args.kwargs["prompt"]
    assert "使用者資料庫語言：en（英文）" in prompt
    assert "請以該語言撰寫各欄位內容。" in prompt


# ── 摘要的「一天」以台北日期為準 ─────────────────────────────────────────
#
# 以下時間戳都寫 UTC，括號內是對應的台北時間（UTC+8）。


def _echo_gemini(service: ConsultationService) -> None:
    """讓假 Gemini 把收到的 prompt 原樣當摘要回傳（as JSON dict），摘要內容即可反映餵進去的對話。"""
    async def echo_structured(prompt: str, json_schema: dict) -> dict:
        # 回傳一個與 schema 相符的假 JSON，內容就是收到的 prompt
        return {key: prompt for key in json_schema.get("properties", {}).keys()}

    service._gemini_service.invoke_structured_output = AsyncMock(side_effect=echo_structured)


async def _add_text(service: ConsultationService, content: str, timestamp: datetime):
    await service._chat_history_repository.append_message(
        "U123",
        ChatMessage(
            line_id="U123", message_type="text", content=content, timestamp=timestamp
        ),
    )


# Redis 的 TTL 每寫一則就重設，天天聊天的人列表會跨好幾天；
# 某天的摘要不能混進別天的對話。
async def test_summary_excludes_messages_from_other_taipei_days(
    consultation_service: ConsultationService,
):
    _echo_gemini(consultation_service)
    # 9/13 22:00
    await _add_text(consultation_service, "昨天頭痛", datetime(2026, 9, 13, 14, 0, tzinfo=timezone.utc))
    # 9/14 10:00
    await _add_text(consultation_service, "今天咳嗽", datetime(2026, 9, 14, 2, 0, tzinfo=timezone.utc))

    summary = await consultation_service.summarize("U123", ConsultationSummarizeRequest())

    assert summary.summary_date == date(2026, 9, 14)
    assert "今天咳嗽" in summary.summary
    assert "昨天頭痛" not in summary.summary


async def test_message_after_taipei_midnight_belongs_to_next_day(
    consultation_service: ConsultationService,
):
    _echo_gemini(consultation_service)
    # UTC 仍是 9/13，台北已是 9/14 01:30
    await _add_text(consultation_service, "半夜胸悶", datetime(2026, 9, 13, 17, 30, tzinfo=timezone.utc))

    summary = await consultation_service.summarize("U123", ConsultationSummarizeRequest())

    assert summary.summary_date == date(2026, 9, 14)


# 當天稍早（排程或手動）已產生過摘要，之後又有新對話：
# 若直接沿用舊摘要，後面的對話就永遠不會被摘要。
async def test_stale_summary_is_regenerated_when_day_has_newer_messages(
    consultation_service: ConsultationService,
):
    _echo_gemini(consultation_service)
    # 9/14 08:30
    await _add_text(consultation_service, "早上頭暈", datetime(2026, 9, 14, 0, 30, tzinfo=timezone.utc))
    # 9/14 14:00，在舊摘要之後
    await _add_text(consultation_service, "下午胸悶", datetime(2026, 9, 14, 6, 0, tzinfo=timezone.utc))
    await consultation_service._repository.upsert_summary(
        ConsultationSummary(
            line_id="U123",
            summary_date=date(2026, 9, 14),
            summary="早上摘要",
            language="zh-TW",
            # 9/14 09:00
            created_at=datetime(2026, 9, 14, 1, 0, tzinfo=timezone.utc),
        )
    )

    summary = await consultation_service.summarize("U123", ConsultationSummarizeRequest())

    assert "下午胸悶" in summary.summary


async def test_up_to_date_summary_is_reused(
    consultation_service: ConsultationService,
):
    _echo_gemini(consultation_service)
    await _add_text(consultation_service, "早上頭暈", datetime(2026, 9, 14, 0, 30, tzinfo=timezone.utc))
    await _add_text(consultation_service, "下午胸悶", datetime(2026, 9, 14, 6, 0, tzinfo=timezone.utc))
    await consultation_service._repository.upsert_summary(
        ConsultationSummary(
            line_id="U123",
            summary_date=date(2026, 9, 14),
            summary="既有摘要",
            language="zh-TW",
            # Motor 未開 tz_aware，從 Mongo 讀回的是 naive UTC（9/14 16:00）
            created_at=datetime(2026, 9, 14, 8, 0),
        )
    )

    summary = await consultation_service.summarize("U123", ConsultationSummarizeRequest())

    assert summary.summary == "既有摘要"


async def test_get_raw_view_filters_by_taipei_date(
    consultation_service: ConsultationService,
):
    # 9/14 01:30
    await _add_text(consultation_service, "半夜胸悶", datetime(2026, 9, 13, 17, 30, tzinfo=timezone.utc))

    messages = await consultation_service.get_raw_view(
        "U123", date(2026, 9, 14), language="zh-TW"
    )

    assert [message.content for message in messages] == ["半夜胸悶"]


# LIFF 的原始紀錄頁把訊息攤成一整串、沒有日期標示。原文改存 30 天之後，
# 不帶日期時若全部回傳，會變成分不清是哪天的長串；只給最近有對話的那天。
async def test_raw_view_without_date_returns_latest_taipei_day(
    consultation_service: ConsultationService,
):
    await _add_text(consultation_service, "昨天頭痛", datetime(2026, 9, 13, 14, 0, tzinfo=timezone.utc))
    await _add_text(consultation_service, "今天咳嗽", datetime(2026, 9, 14, 2, 0, tzinfo=timezone.utc))

    messages = await consultation_service.get_raw_view("U123", language="zh-TW")

    assert [message.content for message in messages] == ["今天咳嗽"]


# 每日排程要把 Redis 裡每個台北日期都摘要到，而不只是最新的那天。
async def test_daily_run_summarizes_every_pending_taipei_day(
    consultation_service: ConsultationService,
):
    _echo_gemini(consultation_service)
    await _add_text(consultation_service, "昨天頭痛", datetime(2026, 9, 13, 14, 0, tzinfo=timezone.utc))
    await _add_text(consultation_service, "今天咳嗽", datetime(2026, 9, 14, 2, 0, tzinfo=timezone.utc))
    scheduler = ConsultationDailySummaryScheduler(
        consultation_service=consultation_service,
        consultation_store=consultation_service._chat_history_repository,
        run_time="02:00",
    )

    await scheduler._run_once()

    stored = consultation_service._repository.by_date
    assert sorted(day for _, day in stored) == [date(2026, 9, 13), date(2026, 9, 14)]
    assert "昨天頭痛" in stored[("U123", date(2026, 9, 13))].summary
    assert "今天咳嗽" not in stored[("U123", date(2026, 9, 13))].summary
    assert "今天咳嗽" in stored[("U123", date(2026, 9, 14))].summary


# 排程時間是台北時間：容器預設 UTC，若照容器時鐘解讀，02:00 會變成台北 10:00。
def test_next_run_is_computed_in_taipei_time():
    scheduler = ConsultationDailySummaryScheduler(
        consultation_service=MagicMock(),
        consultation_store=MagicMock(),
        run_time="02:00",
    )

    # 現在是台北 9/14 09:30，今天的 02:00 已過，下次是台北 9/15 02:00
    next_run = scheduler._next_run_at(datetime(2026, 9, 14, 1, 30, tzinfo=timezone.utc))

    assert next_run == datetime(2026, 9, 14, 18, 0, tzinfo=timezone.utc)


# ── 邊界條件測試 ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_summary_with_unsupported_language_fallback_to_zh_tw(
    consultation_service: ConsultationService,
):
    """不支援的語言應該退回中文"""
    msg = ChatMessage(
        line_id="U123",
        message_type="text",
        content="頭痛",
        timestamp=datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc),
    )

    async def mock_structured(prompt: str, json_schema: dict) -> dict:
        return {key: "test" for key in json_schema.get("properties", {}).keys()}

    consultation_service._gemini_service.invoke_structured_output = AsyncMock(
        side_effect=mock_structured
    )

    summary_text = await consultation_service._generate_summary(
        "U123", date.today(), [msg], language="fr"  # 不支援的語言
    )

    # 應該用統一的英文 key，而不是法文
    parsed = json.loads(summary_text)
    assert "health_issue" in parsed  # 英文 snake_case key


@pytest.mark.asyncio
async def test_generate_summary_with_very_long_conversation(
    consultation_service: ConsultationService,
):
    """非常長的對話不應該崩潰"""
    messages = [
        ChatMessage(
            line_id="U123",
            message_type="text" if i % 2 == 0 else "assistant_reply",
            content=f"訊息內容 {i}" * 100,  # 很長的訊息
            timestamp=datetime(2026, 9, 14, i // 60, i % 60, tzinfo=timezone.utc),
        )
        for i in range(50)  # 50 則訊息
    ]

    async def mock_structured(prompt: str, json_schema: dict) -> dict:
        # 檢查 prompt 長度
        assert len(prompt) > 1000
        return {key: "ok" for key in json_schema.get("properties", {}).keys()}

    consultation_service._gemini_service.invoke_structured_output = AsyncMock(
        side_effect=mock_structured
    )

    summary_text = await consultation_service._generate_summary(
        "U123", date.today(), messages, language="zh-TW"
    )

    parsed = json.loads(summary_text)
    assert len(parsed) > 0


@pytest.mark.asyncio
async def test_generate_summary_with_special_characters(
    consultation_service: ConsultationService,
):
    """包含特殊字符的對話應該正確處理"""
    msg = ChatMessage(
        line_id="U123",
        message_type="text",
        content="頭痛😷\n發燒🌡️\n'引號'、「書名號」",
        timestamp=datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc),
    )

    async def mock_structured(prompt: str, json_schema: dict) -> dict:
        # 檢查 prompt 包含原始內容
        assert "😷" in prompt
        assert "引號" in prompt
        return {key: "ok" for key in json_schema.get("properties", {}).keys()}

    consultation_service._gemini_service.invoke_structured_output = AsyncMock(
        side_effect=mock_structured
    )

    summary_text = await consultation_service._generate_summary(
        "U123", date.today(), [msg], language="en"
    )

    parsed = json.loads(summary_text)
    assert isinstance(parsed, dict)


# ── 摘要附上系統裡設定的用藥與掛號提醒 ─────────────────────────────────────


def _reminder(
    medications: list[Medication],
    *,
    enabled: bool = True,
    start_date: str = "2026-09-01",
    end_date: str | None = None,
) -> MedicationReminderWithMedications:
    return MedicationReminderWithMedications(
        creator_user_id="U123",
        user_id="U123",
        slot_type="morning",
        entries=[
            ReminderEntry(
                scheduled_time="08:00",
                medication_ids=[medication.id for medication in medications],
            )
        ],
        enabled=enabled,
        start_date=start_date,
        end_date=end_date,
        medications=medications,
    )


def _medication(medication_id: str, name: str, *, enabled: bool = True) -> Medication:
    return Medication(
        _id=medication_id,
        user_id="U123",
        created_by_user_id="U123",
        name=name,
        enabled=enabled,
    )


def _appointment(status: str = "scheduled") -> AppointmentReminder:
    now = datetime(2026, 9, 10, tzinfo=timezone.utc)
    return AppointmentReminder(
        user_id="U123",
        creator_user_id="U123",
        # 台北 9/20 09:30
        appointment_at=datetime(2026, 9, 20, 1, 30, tzinfo=timezone.utc),
        appointment_utc_offset_minutes=480,
        day_end_at=datetime(2026, 9, 20, 16, 0, tzinfo=timezone.utc),
        hospital_name="台大醫院",
        department="家醫科",
        status=status,
        created_at=now,
        updated_at=now,
    )


async def _summarize_and_get_prompt(service: ConsultationService) -> str:
    _echo_gemini(service)
    # 台北 9/14 10:00
    await _add_text(service, "今天咳嗽", datetime(2026, 9, 14, 2, 0, tzinfo=timezone.utc))
    await service.summarize("U123", ConsultationSummarizeRequest(force=True))
    return service._gemini_service.invoke_structured_output.call_args.kwargs["prompt"]


async def test_summary_prompt_includes_medication_names_and_appointments(
    consultation_service: ConsultationService,
):
    consultation_service._medication_service.reminders = [
        _reminder([_medication("m1", "普拿疼"), _medication("m2", "胃藥", enabled=False)])
    ]
    consultation_service._appointment_repository.appointments = [_appointment()]

    prompt = await _summarize_and_get_prompt(consultation_service)

    assert "- 早 08:00：普拿疼" in prompt
    assert "胃藥" not in prompt
    assert "- 2026-09-20 09:30 台大醫院 家醫科（已排定）" in prompt


async def test_summary_prompt_skips_inactive_reminders_and_cancelled_appointments(
    consultation_service: ConsultationService,
):
    medications = [_medication("m1", "普拿疼")]
    consultation_service._medication_service.reminders = [
        _reminder(medications, enabled=False),
        _reminder(medications, end_date="2026-09-13"),
        _reminder(medications, start_date="2026-09-15"),
    ]
    consultation_service._appointment_repository.appointments = [
        _appointment(status="cancelled")
    ]

    prompt = await _summarize_and_get_prompt(consultation_service)

    assert "用藥提醒：\n無\n掛號提醒：\n無" in prompt
    assert "普拿疼" not in prompt
    assert "台大醫院" not in prompt


async def test_appointments_are_queried_from_taipei_start_of_summary_day(
    consultation_service: ConsultationService,
):
    await _summarize_and_get_prompt(consultation_service)

    assert consultation_service._appointment_repository.day_end_after == datetime(
        2026, 9, 14, tzinfo=TAIPEI_TZ
    )


async def test_reminder_lookup_failure_is_not_swallowed(
    consultation_service: ConsultationService,
):
    consultation_service._appointment_repository.error = PyMongoError("mongo down")

    with pytest.raises(PyMongoError):
        await _summarize_and_get_prompt(consultation_service)


# ── 紅卡（風險警示）在對話稿裡換成一行看得懂的字 ─────────────────────────────


def _risk_card(reason: str) -> str:
    verdict = UrgencyVerdict(level=URGENCY_EMERGENCY, display=reason)
    return json.dumps(
        build_emergency_condition_flex(verdict, language="zh-TW", font_size="large"),
        ensure_ascii=False,
    )


async def _add_message(
    service: ConsultationService, message_type: str, content: str, timestamp: datetime
):
    await service._chat_history_repository.append_message(
        "U123",
        ChatMessage(
            line_id="U123", message_type=message_type, content=content, timestamp=timestamp
        ),
    )


async def _prompt_for(service: ConsultationService) -> str:
    _echo_gemini(service)
    await service.summarize("U123", ConsultationSummarizeRequest(force=True))
    return service._gemini_service.invoke_structured_output.call_args.kwargs["prompt"]


async def test_risk_alert_card_becomes_marker_with_user_words_and_reason(
    consultation_service: ConsultationService,
):
    await _add_message(
        consultation_service, "text", "我要自殺", datetime(2026, 9, 14, 2, 0, tzinfo=timezone.utc)
    )
    await _add_message(
        consultation_service,
        "assistant_reply",
        _risk_card("你表達想結束生命"),
        datetime(2026, 9, 14, 2, 0, 5, tzinfo=timezone.utc),
    )

    prompt = await _prompt_for(consultation_service)

    assert (
        "[assistant_reply] 觸發風險警示｜使用者輸入：「我要自殺」｜判定原因：你表達想結束生命"
        in prompt
    )
    assert '"bubble"' not in prompt
    assert "tel:119" not in prompt


async def test_risk_alert_without_reason_still_keeps_user_words(
    consultation_service: ConsultationService,
):
    # 判斷器 LLM 失敗、改以本地機率判定時，卡片沒有判定原因
    await _add_message(
        consultation_service, "text", "我剛出車禍", datetime(2026, 9, 14, 2, 0, tzinfo=timezone.utc)
    )
    await _add_message(
        consultation_service,
        "assistant_reply",
        _risk_card(""),
        datetime(2026, 9, 14, 2, 0, 5, tzinfo=timezone.utc),
    )

    prompt = await _prompt_for(consultation_service)

    assert "[assistant_reply] 觸發風險警示｜使用者輸入：「我剛出車禍」\n" in prompt


async def test_non_risk_replies_and_user_json_are_left_as_is(
    consultation_service: ConsultationService,
):
    user_json = '{"riskAlert": {"reason": "使用者自己貼的"}}'
    await _add_message(
        consultation_service, "text", user_json, datetime(2026, 9, 14, 2, 0, tzinfo=timezone.utc)
    )
    await _add_message(
        consultation_service,
        "assistant_reply",
        "{這不是 JSON}",
        datetime(2026, 9, 14, 2, 0, 5, tzinfo=timezone.utc),
    )

    prompt = await _prompt_for(consultation_service)

    assert f"[text] {user_json}" in prompt
    assert "[assistant_reply] {這不是 JSON}" in prompt
    assert "[assistant_reply] 觸發風險警示" not in prompt


# ── 其他工具卡片在對話稿裡只留種類與重點，不帶整包 Flex JSON ──────────────────


_CARD_TIME = datetime(2026, 9, 14, 2, 0, 5, tzinfo=timezone.utc)
_FLEX_STRUCTURE_STRINGS = ('"type": "bubble"', '"altText"', '"action"', '"contents"')


def _facility(name: str) -> MedicalFacility:
    return MedicalFacility(
        id=name,
        name=name,
        address="台北市中正區常德街1號",
        latitude=25.04,
        longitude=121.51,
        phone="02-2312-3456",
        type="醫院",
    )


def _department(name: str) -> DepartmentCandidate:
    return DepartmentCandidate(canonical=name, subgroup=None, facility_count=100)


def _facility_list_card(language: str = "zh-TW") -> str:
    return json.dumps(
        generate_facility_list_flex_message(
            [_facility("台大醫院"), _facility("馬偕紀念醫院")],
            language=language,
            font_size="large",
        ),
        ensure_ascii=False,
    )


def _facility_detail_card() -> str:
    return json.dumps(
        generate_facility_detail_flex_message(
            _facility("台大醫院"), language="zh-TW", font_size="large"
        ),
        ensure_ascii=False,
    )


def _symptom_card(result: SymptomTriageResult) -> str:
    return json.dumps(
        build_symptom_department_flex(result, references=(), font_size="large"),
        ensure_ascii=False,
    )


def _symptom_suggestion_card() -> str:
    return _symptom_card(
        SymptomTriageResult(
            kind=RESULT_SUGGESTION,
            user_input="肚子痛要掛哪一科",
            matched_term="腹痛",
            candidates=(_department("胃腸肝膽科"), _department("家醫科")),
        )
    )


def _symptom_fallback_card() -> str:
    return _symptom_card(
        SymptomTriageResult(
            kind=RESULT_FALLBACK,
            user_input="全身不舒服",
            fallback_reason="無法對應到已知的症狀條目",
            candidates=(_department("家醫科"), _department("內科")),
        )
    )


def _claim_card() -> str:
    card = _to_flex_message_text(
        VerificationResult(
            user_question="網傳吃鳳梨心可以溶解血栓，是真的嗎？",
            verdict="錯誤",
            reasoning="查核報告指出這是缺乏醫學根據的說法，血栓需以藥物治療。",
            source_title="鳳梨心溶血栓查核報告",
            source_url="https://tfc-taiwan.org.tw/fact-check-reports/xxx",
            matched=True,
            related_info="",
        )
    )
    assert card is not None
    return card


def _official_site_card(language: str = "zh-TW") -> str:
    return json.dumps(
        generate_official_site_flex_message(
            "https://liff.line.me/1234",
            "https://care.example.com",
            language=language,
            font_size="large",
        ),
        ensure_ascii=False,
    )


_CARD_CASES = [
    pytest.param(
        _facility_list_card,
        "[assistant_reply] 院所查詢卡｜院所：台大醫院、馬偕紀念醫院",
        id="facility_list",
    ),
    pytest.param(
        _facility_detail_card,
        "[assistant_reply] 院所查詢卡｜院所：台大醫院",
        id="facility_detail",
    ),
    pytest.param(
        _symptom_suggestion_card,
        "[assistant_reply] 科別建議卡｜建議科別：胃腸肝膽科、家醫科",
        id="symptom_suggestion",
    ),
    pytest.param(
        _symptom_fallback_card,
        "[assistant_reply] 科別建議卡｜系統無法判斷症狀，初診方向：家醫科、內科",
        id="symptom_fallback",
    ),
    pytest.param(
        _claim_card,
        "[assistant_reply] 查核判定卡｜判定結果：錯誤",
        id="claim_verdict",
    ),
    pytest.param(
        _official_site_card,
        "[assistant_reply] 官網入口卡",
        id="official_site",
    ),
]


def _assistant_lines(prompt: str) -> list[str]:
    return [
        line.strip()
        for line in prompt.splitlines()
        if line.strip().startswith("[assistant_reply]")
    ]


@pytest.mark.parametrize("build_card, expected_line", _CARD_CASES)
async def test_tool_card_json_does_not_reach_prompt(
    consultation_service: ConsultationService, build_card, expected_line
):
    card = build_card()
    # 前提：餵進去的確實是帶版面結構的卡片 JSON
    assert all(fragment in card for fragment in _FLEX_STRUCTURE_STRINGS)
    await _add_message(consultation_service, "assistant_reply", card, _CARD_TIME)

    prompt = await _prompt_for(consultation_service)

    for fragment in _FLEX_STRUCTURE_STRINGS:
        assert fragment not in prompt


@pytest.mark.parametrize("build_card, expected_line", _CARD_CASES)
async def test_tool_card_keeps_its_key_facts_in_prompt(
    consultation_service: ConsultationService, build_card, expected_line
):
    await _add_message(consultation_service, "assistant_reply", build_card(), _CARD_TIME)

    prompt = await _prompt_for(consultation_service)

    assert _assistant_lines(prompt) == [expected_line]


@pytest.mark.parametrize("build_card", [_official_site_card, _facility_list_card])
async def test_card_line_does_not_depend_on_card_language(
    consultation_service: ConsultationService, build_card
):
    lines = {}
    for language in ("zh-TW", "en"):
        consultation_service._chat_history_repository.messages.clear()
        await _add_message(
            consultation_service, "assistant_reply", build_card(language), _CARD_TIME
        )
        lines[language] = _assistant_lines(await _prompt_for(consultation_service))

    assert len(lines["zh-TW"]) == 1
    assert lines["zh-TW"] == lines["en"]


async def test_unknown_flex_card_becomes_generic_marker(
    consultation_service: ConsultationService,
):
    # 上線前存下的卡片沒有結構化 key：拿掉官網卡的標記來模擬
    legacy_card = generate_official_site_flex_message(
        "https://liff.line.me/1234", "https://care.example.com", language="zh-TW"
    )
    legacy_card.pop(OFFICIAL_SITE_KEY)
    await _add_message(
        consultation_service,
        "assistant_reply",
        json.dumps(legacy_card, ensure_ascii=False),
        _CARD_TIME,
    )

    prompt = await _prompt_for(consultation_service)

    assert "[assistant_reply] [系統卡片]" in prompt
    assert '"altText"' not in prompt


async def test_plain_text_and_non_flex_json_are_left_as_is(
    consultation_service: ConsultationService,
):
    user_card = _official_site_card()
    await _add_message(
        consultation_service, "text", user_card, datetime(2026, 9, 14, 2, 0, tzinfo=timezone.utc)
    )
    await _add_message(
        consultation_service,
        "assistant_reply",
        "建議您多休息、多喝水。",
        datetime(2026, 9, 14, 2, 0, 1, tzinfo=timezone.utc),
    )
    await _add_message(
        consultation_service,
        "assistant_reply",
        "{這不是 JSON}",
        datetime(2026, 9, 14, 2, 0, 2, tzinfo=timezone.utc),
    )
    await _add_message(
        consultation_service,
        "assistant_reply",
        '{"a": 1}',
        datetime(2026, 9, 14, 2, 0, 3, tzinfo=timezone.utc),
    )

    prompt = await _prompt_for(consultation_service)

    assert f"[text] {user_card}" in prompt
    assert "[assistant_reply] 建議您多休息、多喝水。" in prompt
    assert "[assistant_reply] {這不是 JSON}" in prompt
    assert '[assistant_reply] {"a": 1}' in prompt
    assert "官網入口卡" not in prompt
    assert "[系統卡片]" not in prompt


async def test_share_location_reply_is_plain_text_kept_as_is(
    consultation_service: ConsultationService,
):
    # 分享位置工具回的是純文字，不是卡片 JSON
    reply = await request_location_quick_reply.ainvoke({})
    assert isinstance(reply, str)
    assert not reply.lstrip().startswith("{")
    await _add_message(consultation_service, "assistant_reply", reply, _CARD_TIME)

    prompt = await _prompt_for(consultation_service)

    assert f"[assistant_reply] {reply}" in prompt


# ── 原始對話頁：卡片 JSON 依查看者語言換成一行字，存檔不動 ─────────────────────


async def _raw_contents(service: ConsultationService, language: str) -> list[str]:
    messages = await service.get_raw_view("U123", language=language)
    return [message.content for message in messages]


async def _add_every_card(service: ConsultationService) -> None:
    cards = [
        _risk_card("你表達想結束生命"),
        _facility_list_card(),
        _facility_detail_card(),
        _symptom_card(
            SymptomTriageResult(
                kind=RESULT_SUGGESTION,
                user_input="肚子痛要掛哪一科",
                matched_term="腹痛",
                candidates=(_department("家醫科"), _department("內科")),
            )
        ),
        _symptom_fallback_card(),
        _claim_card(),
        _official_site_card(),
    ]
    await _add_message(
        service, "text", "我要自殺", datetime(2026, 9, 14, 2, 0, tzinfo=timezone.utc)
    )
    for offset, card in enumerate(cards, start=1):
        await _add_message(
            service,
            "assistant_reply",
            card,
            datetime(2026, 9, 14, 2, 0, offset, tzinfo=timezone.utc),
        )


async def test_raw_view_turns_cards_into_lines_in_zh_tw(
    consultation_service: ConsultationService,
):
    await _add_every_card(consultation_service)

    contents = await _raw_contents(consultation_service, "zh-TW")

    assert contents == [
        "我要自殺",
        # 上一則就是使用者原話，紅卡不再重複帶出
        "觸發風險警示｜判定原因：你表達想結束生命",
        "院所查詢卡｜院所：台大醫院、馬偕紀念醫院",
        "院所查詢卡｜院所：台大醫院",
        "科別建議卡｜建議科別：家醫科、內科",
        "科別建議卡｜系統無法判斷症狀，初診方向：家醫科、內科",
        "查核判定卡｜判定結果：錯誤",
        "官網入口卡",
    ]


async def test_raw_view_turns_cards_into_lines_in_viewer_language(
    consultation_service: ConsultationService,
):
    await _add_every_card(consultation_service)

    contents = await _raw_contents(consultation_service, "en")

    assert contents == [
        "我要自殺",
        "Risk alert triggered | Reason: 你表達想結束生命",
        "Medical facility search card | Facilities: 台大醫院, 馬偕紀念醫院",
        "Medical facility search card | Facilities: 台大醫院",
        "Department suggestion card | Suggested departments: Family Medicine, Internal Medicine",
        "Department suggestion card | Symptom could not be determined; "
        "suggested first visit: Family Medicine, Internal Medicine",
        "Fact-check card | Verdict: False",
        "Official site card",
    ]
    for content in contents:
        for fragment in _FLEX_STRUCTURE_STRINGS:
            assert fragment not in content


async def test_raw_view_keeps_untranslated_department_and_verdict_as_is(
    consultation_service: ConsultationService,
):
    await _add_message(
        consultation_service,
        "assistant_reply",
        json.dumps(
            {
                "type": "flex",
                "symptomDepartment": {"kind": RESULT_SUGGESTION, "departments": ["胃腸肝膽科"]},
            },
            ensure_ascii=False,
        ),
        _CARD_TIME,
    )
    await _add_message(
        consultation_service,
        "assistant_reply",
        json.dumps({"type": "flex", "claimVerdict": {"verdict": "新判定"}}, ensure_ascii=False),
        datetime(2026, 9, 14, 2, 0, 6, tzinfo=timezone.utc),
    )

    contents = await _raw_contents(consultation_service, "en")

    assert contents == [
        "Department suggestion card | Suggested departments: 胃腸肝膽科",
        "Fact-check card | Verdict: 新判定",
    ]


@pytest.mark.parametrize(
    "language, expected",
    [("zh-TW", "[系統卡片]"), ("en", "[System card]"), ("ja", "[システムカード]")],
)
async def test_raw_view_unknown_card_becomes_generic_marker(
    consultation_service: ConsultationService, language, expected
):
    legacy_card = generate_official_site_flex_message(
        "https://liff.line.me/1234", "https://care.example.com", language="zh-TW"
    )
    legacy_card.pop(OFFICIAL_SITE_KEY)
    await _add_message(
        consultation_service,
        "assistant_reply",
        json.dumps(legacy_card, ensure_ascii=False),
        _CARD_TIME,
    )

    assert await _raw_contents(consultation_service, language) == [expected]


async def test_raw_view_leaves_text_and_user_json_as_is(
    consultation_service: ConsultationService,
):
    user_card = _official_site_card()
    await _add_message(
        consultation_service, "text", user_card, datetime(2026, 9, 14, 2, 0, tzinfo=timezone.utc)
    )
    await _add_message(
        consultation_service,
        "assistant_reply",
        "{這不是 JSON}",
        datetime(2026, 9, 14, 2, 0, 1, tzinfo=timezone.utc),
    )
    await _add_message(
        consultation_service,
        "assistant_reply",
        '{"a": 1}',
        datetime(2026, 9, 14, 2, 0, 2, tzinfo=timezone.utc),
    )

    assert await _raw_contents(consultation_service, "en") == [
        user_card,
        "{這不是 JSON}",
        '{"a": 1}',
    ]


async def test_raw_view_does_not_modify_stored_messages(
    consultation_service: ConsultationService,
):
    card = _official_site_card()
    await _add_message(consultation_service, "assistant_reply", card, _CARD_TIME)

    await _raw_contents(consultation_service, "en")

    stored = await consultation_service._chat_history_repository.list_messages("U123")
    assert [message.content for message in stored] == [card]
