from __future__ import annotations

from contextvars import ContextVar, Token

from langchain_core.tools import tool

from app.services.knowledge_reports.service import KnowledgeReportService

line_user_id_var: ContextVar[str | None] = ContextVar("line_user_id", default=None)

_knowledge_report_service: KnowledgeReportService | None = None


def set_line_user_id(user_id: str) -> Token:
    return line_user_id_var.set(user_id)


def reset_line_user_id(token: Token) -> None:
    line_user_id_var.reset(token)


def get_line_user_id() -> str | None:
    return line_user_id_var.get()


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

    report = await _knowledge_report_service.create(
        line_user_id=line_user_id,
        question=question,
        reason=normalized_reason,  # type: ignore[arg-type]
        user_note=user_note,
        user_source_urls=user_source_urls,
    )
    return (
        f"已建立知識回報 {report.report_id}，狀態為 pending，"
        "營運團隊將會審核。"
    )
