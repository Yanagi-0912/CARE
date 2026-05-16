"""Guardrail 例外對應。

舊：service 層直接針對每種 `Gemini*Error` 個別 `except` 並 log。
新：在這裡集中映射成單一 `GuardrailClassificationError`，service 層只需處理一種 application 例外。
"""

from app.services.gemini.shared.errors import (
    GeminiHttpError,
    GeminiNetworkError,
    GeminiSchemaError,
    GeminiUnknownError,
)


class GuardrailClassificationError(Exception):
    """Guardrail 分類失敗時，application 層使用的統一例外型別。"""


def map_guardrail_classification_error(exc: BaseException) -> GuardrailClassificationError:
    """將底層 `Gemini*Error` 或其他例外，映射為 `GuardrailClassificationError` 物件。"""
    if isinstance(exc, GeminiNetworkError):
        return GuardrailClassificationError(f"網路錯誤: {exc}")
    if isinstance(exc, GeminiHttpError):
        return GuardrailClassificationError(f"HTTP 錯誤: {exc}")
    if isinstance(exc, GeminiSchemaError):
        return GuardrailClassificationError(f"回應格式錯誤: {exc}")
    if isinstance(exc, GeminiUnknownError):
        return GuardrailClassificationError(f"未知錯誤: {exc}")
    return GuardrailClassificationError(f"未處理錯誤: {exc}")
