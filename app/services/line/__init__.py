# LINE Bot 服務層
# 提供 LINE Messaging API 相關的業務邏輯服務

from typing import TYPE_CHECKING, Any

from app.services.line.messaging_client import LineMessagingClient
from app.services.line.token_manager import LineTokenManager, line_token_manager

if TYPE_CHECKING:
    from app.services.line.event_handler import LineEventContext
    from app.services.line.message_service import LineMessageService

__all__ = [
    "LineMessageService",
    "LineMessagingClient",
    "LineTokenManager",
    "line_token_manager",
    "LineEventContext",
]


def __getattr__(name: str) -> Any:
    if name == "LineMessageService":
        from app.services.line.message_service import LineMessageService

        return LineMessageService
    if name == "LineEventContext":
        from app.services.line.event_handler import LineEventContext

        return LineEventContext
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
