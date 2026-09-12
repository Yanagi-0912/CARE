"""Query rewriter 單元測試（DI 注入 invoke）。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from langchain_core.documents import Document

from app.services.rag.query_rewriter import (
    GeminiQueryRewriter,
    RewrittenQuery,
    normalize_en_terms,
    normalize_zh_terms,
)


@pytest.mark.asyncio
async def test_rewriter_returns_all_three_queries():
    invoke = AsyncMock(
        return_value={
            "kb_query": "  持續性性興奮症候群是什麼？  ",
            "zh_terms": "持續性性興奮症候群",
            "en_terms": "persistent genital arousal disorder",
        }
    )
    rewriter = GeminiQueryRewriter(invoke_rewrite=invoke)
    out = await rewriter.rewrite(
        "PGAD 是什麼病",
        [Document(page_content="部分相關", metadata={})],
    )
    assert out == RewrittenQuery(
        kb_query="持續性性興奮症候群是什麼？",
        zh_terms="持續性性興奮症候群",
        en_terms="persistent genital arousal disorder",
    )
    invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_rewriter_falls_back_to_original_when_empty():
    invoke = AsyncMock(return_value={"kb_query": "   ", "zh_terms": "", "en_terms": ""})
    rewriter = GeminiQueryRewriter(invoke_rewrite=invoke)
    out = await rewriter.rewrite("原始問題", [])
    assert out == RewrittenQuery(kb_query="原始問題")


@pytest.mark.asyncio
async def test_rewriter_prompt_includes_question_and_snippets():
    invoke = AsyncMock(return_value={"kb_query": "q", "zh_terms": "", "en_terms": ""})
    rewriter = GeminiQueryRewriter(invoke_rewrite=invoke)
    await rewriter.rewrite(
        "我阿公最近常頭暈",
        [Document(page_content="暈眩的常見原因", metadata={})],
    )
    prompt = invoke.await_args.args[0]
    assert "我阿公最近常頭暈" in prompt
    assert "暈眩的常見原因" in prompt


def test_zh_terms_drop_ascii_acronyms():
    """「持續性性興奮症候群 PGAD」在 gov.tw 回 0 筆，拿掉縮寫後回 5 筆。"""
    assert normalize_zh_terms("持續性性興奮症候群 PGAD") == "持續性性興奮症候群"


def test_zh_terms_normalize_separators_and_cap_at_three():
    assert normalize_zh_terms("膝蓋退化,九層塔、薑，消炎") == "膝蓋退化 九層塔 薑"


def test_en_terms_keep_multiword_terms():
    assert (
        normalize_en_terms("knee osteoarthritis,basil, ginger")
        == "knee osteoarthritis basil ginger"
    )
