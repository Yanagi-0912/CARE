# LINE Bot 服務層
# 提供 LINE Messaging API 相關的業務邏輯服務

from typing import TYPE_CHECKING, Any

from .client import LineMessagingClient, LineTokenManager

if TYPE_CHECKING:
    from .event_handler import LineEventHandler
    from .message_service import LineMessageService

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
        from .message_service import LineMessageService

        return LineMessageService
    if name == "LineEventHandler":
        from .event_handler import LineEventHandler

        return LineEventHandler
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
