# LINE Bot 服務層
# 提供 LINE Messaging API 相關的業務邏輯服務

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .event_handler import LineEventHandler

__all__ = [
    "LineEventHandler",
]


def __getattr__(name: str) -> Any:
    if name == "LineEventHandler":
        from .event_handler import LineEventHandler

        return LineEventHandler
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
