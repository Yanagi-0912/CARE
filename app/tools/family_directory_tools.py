"""查詢登入者自己的家庭名單與稱謂。"""

from langchain_core.tools import tool

from app.core.request_context import get_line_user_id
from app.i18n.messages import t

FAMILY_DIRECTORY_TOOL_NAME = "get_family_directory"

_family_directory_service = None


def configure_family_directory_tool(family_directory_service) -> None:
    global _family_directory_service
    _family_directory_service = family_directory_service


@tool(FAMILY_DIRECTORY_TOOL_NAME)
async def get_family_directory(person: str = "", relationship: str = "") -> str:
    """查目前使用者自己的家庭名單、某位家人的稱謂，或某類稱謂有哪些人。

    person：查某位成員時填姓名；列出全部或依稱謂查詢時留空。
    relationship：只可填 parent、child、spouse、sibling、grandparent、
    grandchild、other；查姓名或列出全部時可留空。
    """
    if _family_directory_service is None:
        return t("family.directory.error")
    operator_id = get_line_user_id()
    if not operator_id:
        return t("family.directory.error")
    return await _family_directory_service.describe(
        operator_id,
        person=person,
        relationship=relationship,
    )


__all__ = [
    "FAMILY_DIRECTORY_TOOL_NAME",
    "configure_family_directory_tool",
    "get_family_directory",
]
