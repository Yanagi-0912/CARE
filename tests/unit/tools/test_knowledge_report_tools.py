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
        user_source_urls=None,
    )


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
