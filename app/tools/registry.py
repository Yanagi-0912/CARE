from app.tools.medical_tools import find_nearby_hospitals
from app.tools.rag_tools import get_rag_answer


def get_all_tools(include_rag_tool: bool = True) -> list[dict]:
    """回傳 Langchain Tool"""

    tools = [find_nearby_hospitals]
    if include_rag_tool:
        tools.append(get_rag_answer)

    return tools
