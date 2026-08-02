from app.tools.medical_tools import (
    find_nearby_hospitals,
    lookup_medical_facility,
    request_location_quick_reply,
)
from app.tools.rag_tools import get_rag_answer


def get_all_tools(include_rag_tool: bool = True) -> list:
    """回傳 Langchain Tool。"""
    tools = [find_nearby_hospitals, lookup_medical_facility, request_location_quick_reply]
    if include_rag_tool:
        tools.append(get_rag_answer)

    return tools
