from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.knowledge_report import KnowledgeReport
from app.tools import knowledge_report_tools as tools


def _sample_report() -> KnowledgeReport:
    now = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
    return KnowledgeReport(
        report_id="KR-20260802-TOOL",
        line_user_id="U_LINE",
        status="pending",
        reason="other",
        question="test",
        created_at=now,
        updated_at=now,
    )


@pytest.fixture(autouse=True)
def reset_tool_state():
    tools.configure_knowledge_report_tool(None)
    yield
    tools.configure_knowledge_report_tool(None)


@pytest.mark.asyncio
async def test_submit_without_line_user_id_fails_gracefully():
    service = MagicMock()
    tools.configure_knowledge_report_tool(service)
    result = await tools.submit_knowledge_report.ainvoke(
        {"question": "Q", "reason": "missing"}
    )
    assert "無法取得使用者身分" in result
    service.create.assert_not_called()


@pytest.mark.asyncio
async def test_submit_without_service():
    token = tools.set_line_user_id("U_LINE")
    try:
        result = await tools.submit_knowledge_report.ainvoke(
            {"question": "Q", "reason": "missing"}
        )
    finally:
        tools.reset_line_user_id(token)
    assert "未初始化" in result


@pytest.mark.asyncio
async def test_submit_creates_pending_report():
    service = MagicMock()
    service.create = AsyncMock(return_value=_sample_report())
    tools.configure_knowledge_report_tool(service)

    token = tools.set_line_user_id("U_LINE")
    try:
        result = await tools.submit_knowledge_report.ainvoke(
            {
                "question": "高血壓飲食？",
                "reason": "outdated",
                "user_note": "請更新",
            }
        )
    finally:
        tools.reset_line_user_id(token)

    assert "KR-20260802-TOOL" in result
    assert "pending" in result
    service.create.assert_awaited_once_with(
        line_user_id="U_LINE",
        question="高血壓飲食？",
        reason="outdated",
        user_note="請更新",
        # user_source_urls=None 這個斷言是「tool 不強制 URL」的迴歸守門，
        # 必須原樣保留：None 不可被「順手」正規化成 []（design.md 決策 3）
        user_source_urls=None,
        source="agent_tool",
    )


@pytest.mark.asyncio
async def test_submit_without_urls_still_creates_report():
    """守門：URL 維持選填。

    改成必填不會讓 LLM 去找正確連結，只會讓它為了完成工具呼叫而生一個。
    幻覺出的 https://www.hpa.gov.tw/<編出來的路徑> 會通過白名單（白名單只看
    host）、進待審佇列、被 admin 當成使用者提供的來源、然後核准去 scrape。
    """
    service = MagicMock()
    service.create = AsyncMock(return_value=_sample_report())
    tools.configure_knowledge_report_tool(service)

    token = tools.set_line_user_id("U_LINE")
    try:
        result = await tools.submit_knowledge_report.ainvoke(
            {"question": "高血壓飲食？", "reason": "missing"}
        )
    finally:
        tools.reset_line_user_id(token)

    assert "KR-20260802-TOOL" in result
    assert service.create.await_args.kwargs["user_source_urls"] is None


@pytest.mark.asyncio
async def test_submit_filters_non_whitelisted_urls_without_failing():
    """非白名單 URL 靜默過濾，工具呼叫仍然成功。

    讓工具呼叫失敗會使 agent 進入重試或改寫參數的迴圈，而它「修正」的方式
    就是換一個更像 gov.tw 的網址——又回到幻覺。過濾是靜默降級：回報照建，
    只是少了不可用的來源，admin 端行為與「使用者沒附來源」完全一致。
    """
    service = MagicMock()
    service.create = AsyncMock(return_value=_sample_report())
    tools.configure_knowledge_report_tool(service)

    token = tools.set_line_user_id("U_LINE")
    try:
        result = await tools.submit_knowledge_report.ainvoke(
            {
                "question": "高血壓飲食？",
                "reason": "outdated",
                "user_source_urls": ["https://www.youtube.com/watch?v=1"],
            }
        )
    finally:
        tools.reset_line_user_id(token)

    assert "KR-20260802-TOOL" in result
    assert "pending" in result
    assert service.create.await_args.kwargs["user_source_urls"] == []


@pytest.mark.asyncio
async def test_submit_keeps_whitelisted_urls():
    service = MagicMock()
    service.create = AsyncMock(return_value=_sample_report())
    tools.configure_knowledge_report_tool(service)

    token = tools.set_line_user_id("U_LINE")
    try:
        await tools.submit_knowledge_report.ainvoke(
            {
                "question": "高血壓飲食？",
                "reason": "outdated",
                "user_source_urls": [
                    "https://www.hpa.gov.tw/Pages/Detail.aspx?nodeid=1",
                    "https://evil.com/x",
                ],
            }
        )
    finally:
        tools.reset_line_user_id(token)

    assert service.create.await_args.kwargs["user_source_urls"] == [
        "https://www.hpa.gov.tw/Pages/Detail.aspx?nodeid=1"
    ]


@pytest.mark.asyncio
async def test_submit_invalid_reason():
    service = MagicMock()
    tools.configure_knowledge_report_tool(service)
    token = tools.set_line_user_id("U_LINE")
    try:
        result = await tools.submit_knowledge_report.ainvoke(
            {"question": "Q", "reason": "invalid"}
        )
    finally:
        tools.reset_line_user_id(token)

    assert "reason 必須為" in result
    service.create.assert_not_called()
