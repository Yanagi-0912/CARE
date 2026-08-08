from app.tools.knowledge_report_tools import submit_knowledge_report
from app.tools.medical_tools import (
    find_nearby_facilities_by_department,
    find_nearby_hospitals,
    lookup_medical_facility,
    request_location_quick_reply,
)
from app.tools.official_site_tools import open_official_site
from app.tools.rag_tools import get_rag_answer
from app.tools.user_document_tools import answer_from_uploaded_document


def get_all_tools(include_rag_tool: bool = True) -> list:
    """回傳 Langchain Tool。"""
    tools = [
        find_nearby_hospitals,
        find_nearby_facilities_by_department,
        lookup_medical_facility,
        request_location_quick_reply,
        submit_knowledge_report,
        open_official_site,
    ]
    if include_rag_tool:
        tools.extend([get_rag_answer, answer_from_uploaded_document])

    return tools
