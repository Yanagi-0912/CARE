# LINE Bot 服務層
# 提供 LINE Messaging API 相關的業務邏輯服務

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .dispatcher.dispatcher import LineEventDispatcher as LineEventHandler
    from .reply.reply import LineTokenManager

__all__ = [
    "LineEventHandler",
    "LineTokenManager",
]


def __getattr__(name: str) -> Any:
    if name == "LineEventHandler":
        from .dispatcher.dispatcher import LineEventDispatcher

        return LineEventDispatcher
    if name == "LineTokenManager":
        from .reply.reply import LineTokenManager

        return LineTokenManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
