"""看診摘要：驗的是「不宣稱誰說的」與「寧可少寫也不亂寫」這兩條規則。"""

import asyncio

import pytest

from app.services.clinic_transcript.summarizer import (
    PROMPT,
    SUMMARY_SCHEMA,
    ClinicVisitSummarizer,
    ClinicVisitSummary,
)


class _FakeGemini:
    def __init__(self, payload=None, exc=None, delay=0.0):
        self.payload, self.exc, self.delay = payload, exc, delay
        self.prompt = None

    async def invoke_structured_output(self, *, prompt, json_schema):
        self.prompt = prompt
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        return self.payload


_FULL = {
    "main_points": ["血壓控制得還可以", " "],
    "medication_changes": [
        {"description": "降血壓的藥先停", "quote": "那個降血壓的先停掉"},
        {"description": "沒有原文的一則", "quote": ""},
    ],
    "next_visit": " 兩週後 ",
    "reminders": ["少吃鹹"],
    "unclear": ["中間有一段聽不清楚"],
}


@pytest.mark.asyncio
async def test_正常解析並去掉空白項():
    summary = await ClinicVisitSummarizer(_FakeGemini(_FULL)).summarize("逐字稿")
    assert summary.main_points == ("血壓控制得還可以",)
    assert summary.next_visit == "兩週後"
    assert summary.unclear == ("中間有一段聽不清楚",)


@pytest.mark.asyncio
async def test_沒有原文的用藥變動一律丟掉():
    """規則 4：少一則，好過給家人一則無法核對的用藥指示。"""
    summary = await ClinicVisitSummarizer(_FakeGemini(_FULL)).summarize("逐字稿")
    assert [c.description for c in summary.medication_changes] == ["降血壓的藥先停"]
    assert summary.medication_changes[0].quote == "那個降血壓的先停掉"


@pytest.mark.asyncio
async def test_逾時回空摘要而不是拋錯():
    """逐字稿已經存好了，不能因為摘要失敗就讓整次看診紀錄消失。"""
    gemini = _FakeGemini(_FULL, delay=0.05)
    summary = await ClinicVisitSummarizer(gemini, timeout_seconds=0.01).summarize("逐字稿")
    assert summary.is_empty


@pytest.mark.asyncio
async def test_模型爆炸也回空摘要():
    summary = await ClinicVisitSummarizer(_FakeGemini(exc=RuntimeError("boom"))).summarize("x")
    assert summary.is_empty


@pytest.mark.asyncio
async def test_空逐字稿不呼叫模型():
    gemini = _FakeGemini(_FULL)
    assert (await ClinicVisitSummarizer(gemini).summarize("   ")).is_empty
    assert gemini.prompt is None


@pytest.mark.asyncio
async def test_過長逐字稿截斷並標記():
    gemini = _FakeGemini(_FULL)
    summary = await ClinicVisitSummarizer(gemini).summarize("字" * 50_000)
    assert summary.truncated is True


def test_提示詞明著禁止指派說話者():
    """這是整個功能的安全核心，不能被順手改掉。"""
    assert "不要寫「醫師說」" in PROMPT
    assert "不要猜" in PROMPT


def test_schema_沒有任何說話者欄位():
    assert "speaker" not in str(SUMMARY_SCHEMA)
    assert "doctor" not in str(SUMMARY_SCHEMA)


def test_空摘要的預設值不會爆():
    assert ClinicVisitSummary().is_empty
