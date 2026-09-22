from unittest.mock import AsyncMock

import pytest

from app.core.request_context import reset_line_user_id, set_line_user_id
from app.i18n.messages import t
from app.tools import family_directory_tools
from app.tools.family_directory_tools import (
    configure_family_directory_tool,
    get_family_directory,
)


@pytest.fixture(autouse=True)
def restore_service():
    previous = family_directory_tools._family_directory_service
    configure_family_directory_tool(None)
    yield
    configure_family_directory_tool(previous)


@pytest.mark.asyncio
async def test_tool_passes_only_the_request_user_and_structured_query():
    service = AsyncMock()
    service.describe.return_value = "您設定為父／母的家人：王美玲。"
    configure_family_directory_tool(service)
    token = set_line_user_id("U_ME")
    try:
        result = await get_family_directory.ainvoke(
            {"person": "", "relationship": "parent"}
        )
    finally:
        reset_line_user_id(token)

    assert result == "您設定為父／母的家人：王美玲。"
    service.describe.assert_awaited_once_with(
        "U_ME", person="", relationship="parent"
    )


@pytest.mark.asyncio
async def test_tool_fails_safely_without_service_or_request_user():
    assert await get_family_directory.ainvoke({}) == t("family.directory.error")

    configure_family_directory_tool(AsyncMock())
    assert await get_family_directory.ainvoke({}) == t("family.directory.error")
