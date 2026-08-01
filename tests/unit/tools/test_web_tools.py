from unittest.mock import AsyncMock, MagicMock

import pytest

from app.tools import web_tools


@pytest.mark.asyncio
async def test_search_public_web_returns_uninit_message_without_service(monkeypatch):
    monkeypatch.setattr(web_tools, "_web_search_service", None)
    result = await web_tools.search_public_web.ainvoke({"query": "高血壓"})
    assert result == "網路搜尋服務未初始化，請稍後再試。"


@pytest.mark.asyncio
async def test_search_public_web_delegates_to_service(monkeypatch):
    service = MagicMock()
    service.answer = AsyncMock(return_value="以下參考網路公開資料\n\n答案")
    monkeypatch.setattr(web_tools, "_web_search_service", service)

    result = await web_tools.search_public_web.ainvoke({"query": "高血壓"})
    assert "以下參考網路公開資料" in result
    service.answer.assert_awaited_once_with("高血壓")
