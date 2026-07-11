# LINE Bot 服務層
# 提供 LINE Messaging API 相關的業務邏輯服務

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .event_handler import LineEventHandler
    from .loading_animation import LineLoadingAnimationService
    from .token_manager import LineTokenManager

__all__ = [
    "LineEventHandler",
    "LineLoadingAnimationService",
    "LineTokenManager",
]


def __getattr__(name: str) -> Any:
    if name == "LineEventHandler":
        from .event_handler import LineEventHandler

        return LineEventHandler
    if name == "LineLoadingAnimationService":
        from .loading_animation import LineLoadingAnimationService

        return LineLoadingAnimationService
    if name == "LineTokenManager":
        from .token_manager import LineTokenManager

        return LineTokenManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
