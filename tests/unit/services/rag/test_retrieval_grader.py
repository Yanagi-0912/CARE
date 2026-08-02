"""Retrieval grader 單元測試（結構化輸出以 DI 注入，禁止 monkey patch）。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from langchain_core.documents import Document

from app.services.rag.retrieval_grader import (
    Grade,
    GeminiRetrievalGrader,
    parse_grade,
)


def test_parse_grade_accepts_canonical_values():
    assert parse_grade("correct") is Grade.CORRECT
    assert parse_grade("AMBIGUOUS") is Grade.AMBIGUOUS
    assert parse_grade("incorrect") is Grade.INCORRECT


def test_parse_grade_rejects_unknown():
    with pytest.raises(ValueError):
        parse_grade("maybe")


@pytest.mark.asyncio
async def test_gemini_grader_returns_enum_and_truncates_docs():
    invoke = AsyncMock(return_value={"grade": "correct"})
    grader = GeminiRetrievalGrader(invoke_grade=invoke, max_chars_per_doc=20)
    docs = [
        Document(page_content="A" * 100, metadata={"id": "1"}),
        Document(page_content="短文", metadata={"id": "2"}),
    ]
    result = await grader.grade("高血壓要注意什麼", docs)
    assert result is Grade.CORRECT
    invoke.assert_awaited_once()
    prompt = invoke.await_args.args[0]
    assert "高血壓要注意什麼" in prompt
    assert "A" * 20 in prompt
    assert "A" * 21 not in prompt


@pytest.mark.asyncio
async def test_gemini_grader_maps_ambiguous():
    invoke = AsyncMock(return_value={"grade": "ambiguous"})
    grader = GeminiRetrievalGrader(invoke_grade=invoke)
    result = await grader.grade(
        "q", [Document(page_content="x", metadata={})]
    )
    assert result is Grade.AMBIGUOUS
