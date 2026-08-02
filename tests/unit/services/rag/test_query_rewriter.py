"""Query rewriter 單元測試（DI 注入 invoke）。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from langchain_core.documents import Document

from app.services.rag.query_rewriter import GeminiQueryRewriter


@pytest.mark.asyncio
async def test_rewriter_returns_stripped_query():
    invoke = AsyncMock(return_value={"query": "  高血壓飲食注意事項  "})
    rewriter = GeminiQueryRewriter(invoke_rewrite=invoke)
    out = await rewriter.rewrite(
        "高血壓要注意什麼",
        [Document(page_content="部分相關", metadata={})],
    )
    assert out == "高血壓飲食注意事項"
    invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_rewriter_falls_back_to_original_when_empty():
    invoke = AsyncMock(return_value={"query": "   "})
    rewriter = GeminiQueryRewriter(invoke_rewrite=invoke)
    out = await rewriter.rewrite("原始問題", [])
    assert out == "原始問題"
