from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.consultation import (
    ConsultationSummary,
    ConsultationSummarizeRequest,
)
from app.models.chat_message import ChatMessage
from app.models.medication import TAIPEI_TZ
from app.services.consultation.consultation_service import (
    ConsultationService,
    taipei_day_utc_range,
)
from app.services.consultation.scheduler import ConsultationDailySummaryScheduler


class FakeChatHistoryRepository:
    def __init__(self) -> None:
        self.messages: dict[str, list[ChatMessage]] = {}

    async def append_message(self, line_id: str, message: ChatMessage) -> None:
        self.messages.setdefault(line_id, []).append(message)

    async def list_messages(
        self, line_id: str, *, since: datetime | None = None, until: datetime | None = None
    ) -> list[ChatMessage]:
        self.last_range = (since, until)
        return [
            message
            for message in self.messages.get(line_id, [])
            if (since is None or message.timestamp >= since)
            and (until is None or message.timestamp < until)
        ]

    async def list_line_ids(
        self, *, since: datetime | None = None, until: datetime | None = None
    ) -> list[str]:
        line_ids = []
        for line_id in self.messages:
            if await self.list_messages(line_id, since=since, until=until):
                line_ids.append(line_id)
        return sorted(line_ids)


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

    messages = await consultation_service.get_raw_view("U123", date(2026, 5, 17))

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

    consultation_service._gemini_service.chat_model.ainvoke = AsyncMock(
        return_value=SimpleNamespace(content='{"主訴":"頭痛"}')
    )
    consultation_service._user_profile_service.language = "en"

    summary = await consultation_service.summarize(
        "U123", ConsultationSummarizeRequest(target_date=date.today(), force=True)
    )

    assert summary.language == "en"
    consultation_service._gemini_service.chat_model.ainvoke.assert_awaited_once()
    prompt = consultation_service._gemini_service.chat_model.ainvoke.call_args.args[0][
        0
    ].content
    assert "使用者資料庫語言：en（英文）" in prompt
    assert "請以該語言撰寫各欄位內容。" in prompt


# ── 摘要的「一天」以台北日期為準 ─────────────────────────────────────────
#
# 以下時間戳都寫 UTC，括號內是對應的台北時間（UTC+8）。


def _echo_gemini(service: ConsultationService) -> None:
    """讓假 Gemini 把收到的 prompt 原樣當摘要回傳，摘要內容即可反映餵進去的對話。"""
    service._gemini_service.chat_model.ainvoke = AsyncMock(
        side_effect=lambda messages: SimpleNamespace(content=messages[0].content)
    )


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

    messages = await consultation_service.get_raw_view("U123", date(2026, 9, 14))

    assert [message.content for message in messages] == ["半夜胸悶"]


# LIFF 的原始紀錄頁把訊息攤成一整串、沒有日期標示。原文改存 30 天之後，
# 不帶日期時若全部回傳，會變成分不清是哪天的長串；只給最近有對話的那天。
async def test_raw_view_without_date_returns_latest_taipei_day(
    consultation_service: ConsultationService,
):
    await _add_text(consultation_service, "昨天頭痛", datetime(2026, 9, 13, 14, 0, tzinfo=timezone.utc))
    await _add_text(consultation_service, "今天咳嗽", datetime(2026, 9, 14, 2, 0, tzinfo=timezone.utc))

    messages = await consultation_service.get_raw_view("U123")

    assert [message.content for message in messages] == ["今天咳嗽"]


# 每日排程只摘**昨天**（台北日期），而且只撈昨天那段的訊息——不再為每個人
# 撈 30 天的原文。今天的對話還沒結束，留給明天。
async def test_daily_run_summarizes_only_yesterday_taipei_day(
    consultation_service: ConsultationService,
):
    _echo_gemini(consultation_service)
    today = datetime.now(TAIPEI_TZ).date()
    yesterday = today - timedelta(days=1)
    day_before = today - timedelta(days=2)
    yesterday_start, today_start = taipei_day_utc_range(yesterday)
    await _add_text(consultation_service, "前天發燒", yesterday_start - timedelta(hours=3))
    await _add_text(consultation_service, "昨天頭痛", yesterday_start + timedelta(hours=14))
    await _add_text(consultation_service, "今天咳嗽", today_start + timedelta(hours=1))
    scheduler = ConsultationDailySummaryScheduler(
        consultation_service=consultation_service,
        consultation_store=consultation_service._chat_history_repository,
        run_time="02:00",
    )

    await scheduler._run_once()

    stored = consultation_service._repository.by_date
    assert [day for _, day in stored] == [yesterday]
    assert "昨天頭痛" in stored[("U123", yesterday)].summary
    assert "前天發燒" not in stored[("U123", yesterday)].summary
    assert "今天咳嗽" not in stored[("U123", yesterday)].summary
    assert ("U123", day_before) not in stored
    # 查詢本身就限定在昨天的 UTC 區間，不是撈全部再在 Python 裡過濾
    assert consultation_service._chat_history_repository.last_range == (
        yesterday_start,
        today_start,
    )


async def test_summarize_day_returns_none_without_messages(
    consultation_service: ConsultationService,
):
    """那天沒講話的人不寫「尚無諮詢記錄」的空摘要。"""
    result = await consultation_service.summarize_day("U123", date(2026, 9, 13))

    assert result is None
    assert consultation_service._repository.by_date == {}


async def test_daily_run_skips_users_without_messages_yesterday(
    consultation_service: ConsultationService,
):
    _echo_gemini(consultation_service)
    today = datetime.now(TAIPEI_TZ).date()
    _, today_start = taipei_day_utc_range(today - timedelta(days=1))
    await _add_text(consultation_service, "今天咳嗽", today_start + timedelta(hours=1))
    scheduler = ConsultationDailySummaryScheduler(
        consultation_service=consultation_service,
        consultation_store=consultation_service._chat_history_repository,
        run_time="02:00",
    )

    await scheduler._run_once()

    assert consultation_service._repository.by_date == {}
    consultation_service._gemini_service.chat_model.ainvoke.assert_not_awaited()


def test_taipei_day_utc_range():
    start, end = taipei_day_utc_range(date(2026, 9, 14))

    assert start == datetime(2026, 9, 13, 16, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 9, 14, 16, 0, tzinfo=timezone.utc)


# 單日失敗不能殺掉整個排程 task：以前 _run_once 沒有 try/except，一次資料庫
# 抖動就讓摘要從此停擺，而且沒有任何 log。
async def test_run_loop_survives_a_failing_tick():
    store = MagicMock()
    calls = {"n": 0}

    async def _list_line_ids(**_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("db down")
        return []

    store.list_line_ids = _list_line_ids
    scheduler = ConsultationDailySummaryScheduler(
        consultation_service=MagicMock(),
        consultation_store=store,
        run_time="02:00",
    )
    # 讓「下一次執行」永遠是現在，迴圈不必等到凌晨
    scheduler._next_run_at = lambda now: now

    # patch 的是 asyncio 模組本身的 sleep（排程器以 `asyncio.sleep` 取用），
    # 測試自己要讓出事件迴圈得用 patch 前抓住的真 sleep。
    real_sleep = asyncio.sleep

    async def _yield_once(_seconds):
        await real_sleep(0)

    with patch("asyncio.sleep", new=_yield_once):
        task = asyncio.create_task(scheduler._run_loop())
        for _ in range(50):
            await real_sleep(0)
            if calls["n"] >= 2:
                break
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    assert calls["n"] >= 2


@pytest.mark.parametrize("value", ["9:00 AM", "25:00", "02", "02:60", ""])
def test_invalid_run_time_fails_at_construction(value):
    """設錯時要在建構時就炸，訊息點名環境變數，而不是 task 靜默死掉。"""
    with pytest.raises(ValueError, match="CONSULTATION_DAILY_SUMMARY_TIME"):
        ConsultationDailySummaryScheduler(
            consultation_service=MagicMock(),
            consultation_store=MagicMock(),
            run_time=value,
        )


async def test_summarize_failure_log_does_not_contain_raw_line_id(caplog):
    store = MagicMock()
    store.list_line_ids = AsyncMock(return_value=["Uabcdef0123456789abcdef0123456789"])
    service = MagicMock()
    service.summarize_day = AsyncMock(side_effect=RuntimeError("boom"))
    scheduler = ConsultationDailySummaryScheduler(
        consultation_service=service, consultation_store=store, run_time="02:00"
    )

    with caplog.at_level(logging.INFO):
        await scheduler._run_once()

    assert "summarize failed" in caplog.text
    assert "Uabcdef0123456789abcdef0123456789" not in caplog.text


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
