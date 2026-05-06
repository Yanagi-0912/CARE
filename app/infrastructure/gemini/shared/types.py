from dataclasses import dataclass, field
from typing import Optional


@dataclass
class GeminiResult:
    # LangChain AIMessage.content 可能是純文字，或是內容區塊陣列（tool-call 回合常見）。
    text: Optional[str | list[object]] = None
    function_name: Optional[str] = None
    function_args: dict = field(default_factory=dict)

    @property
    def is_function_call(self) -> bool:
        return self.function_name is not None


@dataclass
class ValidationResult:
    is_valid: bool
    error_message: Optional[str] = None
