"""Query rewriter 單元測試（DI 注入 invoke）。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from langchain_core.documents import Document

from app.services.rag.query_rewriter import (
    GeminiQueryRewriter,
    RewrittenQuery,
    accept_typo_fix,
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


# --- 同音錯字 ---
# 2026-09-29 線上實例：注音選錯字「圓錐膠膜是什麼」（本意圓錐角膜），改寫的規則 1
# 禁止補上訊息裡沒有的病名，錯字一路帶到網搜，最後只回「資料不足」。


@pytest.mark.asyncio
async def test_rewriter_prompt_asks_for_typo_fix():
    invoke = AsyncMock(return_value={"kb_query": "q", "zh_terms": "", "en_terms": ""})
    await GeminiQueryRewriter(invoke_rewrite=invoke).rewrite("圓錐膠膜是什麼", [])
    prompt = invoke.await_args.args[0]
    assert "typo_from" in prompt and "typo_to" in prompt
    assert "同音" in prompt


@pytest.mark.asyncio
async def test_rewriter_accepts_homophone_typo_fix():
    invoke = AsyncMock(
        return_value={
            "kb_query": "圓錐角膜是什麼？",
            "zh_terms": "圓錐角膜",
            "en_terms": "keratoconus",
            "typo_from": "圓錐膠膜",
            "typo_to": "圓錐角膜",
        }
    )
    out = await GeminiQueryRewriter(invoke_rewrite=invoke).rewrite("圓錐膠膜是什麼", [])
    assert out == RewrittenQuery(
        kb_query="圓錐角膜是什麼？",
        zh_terms="圓錐角膜",
        en_terms="keratoconus",
        typo_fix=("圓錐膠膜", "圓錐角膜"),
    )


@pytest.mark.asyncio
async def test_rejected_fix_is_undone_in_search_terms():
    """「胃痛」→「胃癌」字數相同、只差一字，但痛（tòng）與癌（ái）不同音：這是
    替使用者改病名，正是規則 1 要防的事。中文查詢改回原字；英文那路無法逐字還原，
    整路不搜。"""
    invoke = AsyncMock(
        return_value={
            "kb_query": "胃癌怎麼辦？",
            "zh_terms": "胃癌 症狀",
            "en_terms": "gastric cancer",
            "typo_from": "胃痛",
            "typo_to": "胃癌",
        }
    )
    out = await GeminiQueryRewriter(invoke_rewrite=invoke).rewrite("胃痛怎麼辦", [])
    assert out == RewrittenQuery(kb_query="胃痛怎麼辦？", zh_terms="胃痛 症狀", en_terms="")


@pytest.mark.asyncio
async def test_no_typo_leaves_rewrite_untouched():
    invoke = AsyncMock(
        return_value={
            "kb_query": "高血壓飲食注意事項",
            "zh_terms": "高血壓 飲食",
            "en_terms": "hypertension diet",
            "typo_from": "",
            "typo_to": "",
        }
    )
    out = await GeminiQueryRewriter(invoke_rewrite=invoke).rewrite("高血壓要注意什麼", [])
    assert out == RewrittenQuery(
        kb_query="高血壓飲食注意事項", zh_terms="高血壓 飲食", en_terms="hypertension diet"
    )


@pytest.mark.parametrize(
    "query,typo_from,typo_to",
    [
        ("圓錐膠膜是什麼", "圓錐膠膜", "圓錐角膜"),  # 膠 jiāo／角 jiǎo，只差聲調
        ("設護腺肥大怎麼辦", "設護腺", "攝護腺"),  # 設 shè／攝 shè
    ],
)
def test_accept_typo_fix_allows_homophones(query, typo_from, typo_to):
    assert accept_typo_fix(query, typo_from, typo_to) == (typo_from, typo_to)


@pytest.mark.parametrize(
    "query,typo_from,typo_to",
    [
        ("胃痛怎麼辦", "胃痛", "胃癌"),  # 不同音：改病名
        ("頭暈耳鳴是什麼病", "頭暈耳鳴", "梅尼爾氏症"),  # 字數不同：推論病名
        ("圓錐膠膜是什麼", "眼角膜", "眼膠膜"),  # typo_from 不在原句裡
        ("圓錐角膜是什麼", "圓錐角膜", "圓錐角膜"),  # 沒有改變
        ("圓錐膠膜是什麼", "", ""),  # 模型說沒有錯字
        ("PGAD是什麼", "PGAD", "PSAD"),  # 非漢字不修
        ("膠膠膠膜是什麼", "膠膠膠", "角角角"),  # 同音但換了三個字：不像打錯字
    ],
)
def test_accept_typo_fix_rejects_everything_else(query, typo_from, typo_to):
    assert accept_typo_fix(query, typo_from, typo_to) is None
