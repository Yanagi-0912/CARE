from dataclasses import dataclass, field
from typing import Optional


@dataclass
class GeminiResult:
    text: Optional[str] = None
    function_name: Optional[str] = None
    function_args: dict = field(default_factory=dict)

    @property
    def is_function_call(self) -> bool:
        return self.function_name is not None


@dataclass
class ValidationResult:
    is_valid: bool
    error_message: Optional[str] = None


@dataclass
class ClassificationResult:
    is_health_related: bool
    confidence: float = 0.0
    category: Optional[str] = None
