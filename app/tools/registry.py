from app.tools.claim_tools import is_claim_tool_configured, verify_claim
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
        # verify_claim 與 get_rag_answer 同屬「guardrail 放行後才提供」的知識庫
        # 工具，因此跟著 include_rag_tool 一起開關；是否配置服務（即
        # CLAIM_VERIFICATION_ENABLED 這道獨立開關的結果，見
        # is_claim_tool_configured 的說明）是另一層過濾，兩者皆為真才提供，
        # 不新增第二個布林參數（YAGNI）。
        if is_claim_tool_configured():
            tools.append(verify_claim)

    return tools
