from __future__ import annotations

import logging
from contextvars import ContextVar, Token

from langchain_core.tools import tool

from app.core.request_context import (
    get_line_user_id,
    reset_line_user_id,
    set_line_user_id,
)
from app.services.knowledge_reports.service import KnowledgeReportService
from app.services.rag.whitelist import is_allowed_url

logger = logging.getLogger(__name__)

_knowledge_report_service: KnowledgeReportService | None = None



def configure_knowledge_report_tool(service: KnowledgeReportService) -> None:
    global _knowledge_report_service
    _knowledge_report_service = service


@tool
async def submit_knowledge_report(
    question: str,
    reason: str,
    user_note: str | None = None,
    user_source_urls: list[str] | None = None,
) -> str:
    """當使用者指出知識庫內容過時、缺失或有誤，需要回報給營運團隊審核時呼叫。
    reason 須為 outdated（過時）、missing（缺失）或 other（其他）。
    """
    if _knowledge_report_service is None:
        return "知識回報服務未初始化，請稍後再試。"

    line_user_id = get_line_user_id()
    if not line_user_id:
        return "無法取得使用者身分，請稍後再試。"

    normalized_reason = (reason or "").strip().lower()
    if normalized_reason not in ("outdated", "missing", "other"):
        return "reason 必須為 outdated、missing 或 other。"

    # 過濾而非拒絕：讓工具呼叫失敗會使 agent 重試並「修正」參數——它修正的
    # 方式就是換一個更像 gov.tw 的網址，又回到幻覺（design.md 決策 3）。
    # None 必須維持 None，不可變成 []：那是「tool 不強制 URL」的契約。
    filtered_urls = user_source_urls
    if user_source_urls is not None:
        filtered_urls = [url for url in user_source_urls if is_allowed_url(url)]
        dropped = [url for url in user_source_urls if url not in filtered_urls]
        if dropped:
            logger.info(
                "submit_knowledge_report 丟棄 %s 個非白名單 URL：%s",
                len(dropped),
                dropped,
            )

    report = await _knowledge_report_service.create(
        line_user_id=line_user_id,
        question=question,
        reason=normalized_reason,  # type: ignore[arg-type]
        user_note=user_note,
        user_source_urls=filtered_urls,
        source="agent_tool",
    )
    return (
        f"已建立知識回報 {report.report_id}，狀態為 pending，"
        "營運團隊將會審核。"
    )
