from app.tools.medical_tools import find_nearby_hospitals, request_location_quick_reply
from app.tools.rag_tools import get_rag_answer
from app.tools.web_tools import search_public_web


def get_all_tools(
    include_rag_tool: bool = True,
    include_web_tool: bool | None = None,
) -> list:
    """回傳 Langchain Tool。

    include_web_tool 預設跟隨 include_rag_tool（G1：同一把 allow_rag 閘門）。
    """
    if include_web_tool is None:
        include_web_tool = include_rag_tool

    tools = [find_nearby_hospitals, request_location_quick_reply]
    if include_rag_tool:
        tools.append(get_rag_answer)
    if include_web_tool:
        tools.append(search_public_web)

    return tools
