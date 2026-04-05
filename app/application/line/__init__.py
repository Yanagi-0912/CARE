# LINE Bot 服務層
# 提供 LINE Messaging API 相關的業務邏輯服務

from typing import TYPE_CHECKING, Any

from app.application.line.client import LineMessagingClient, LineTokenManager

if TYPE_CHECKING:
    from app.application.line.event_handler import LineEventHandler
    from app.infrastructure.line.message_service import LineMessageService

__all__ = [
    "LineMessageService",
    "LineMessagingClient",
    "LineTokenManager",
    "get_line_token_manager",
    "LineEventHandler",
]


def __getattr__(name: str) -> Any:
    if name == "get_line_token_manager":
        from app.dependencies import get_line_token_manager

        return get_line_token_manager
    if name == "LineMessageService":
        from app.infrastructure.line.message_service import LineMessageService

        return LineMessageService
    if name == "LineEventHandler":
        from app.application.line.event_handler import LineEventHandler

        return LineEventHandler
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
