from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .event_handler import LineEventHandler
    from .token_manager import LineTokenManager

__all__ = ["LineEventHandler", "LineTokenManager"]


def __getattr__(name: str) -> Any:
    if name == "LineEventHandler":
        from .event_handler import LineEventHandler

        return LineEventHandler
    if name == "LineTokenManager":
        from .token_manager import LineTokenManager

        return LineTokenManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
