"""Guardrail：判斷使用者訊息是否與健康相關，決定是否啟用 RAG 工具。

舊：自己組 raw Gemini payload → generate_content → 解析 JSON。
新：只依賴注入的 `AsyncStrToBool`（例如由 DI 傳入 `gemini_service.invoke_boolean_structured_output`），
    不 import、不綁 `GeminiService`。
"""

import logging
from collections.abc import Awaitable, Callable

from app.application.guardrail.shared.errors import (
    GuardrailClassificationError,
    map_guardrail_classification_error,
)

logger = logging.getLogger(__name__)

_CLASSIFICATION_PROMPT = (
    "你是一個訊息分類器。請判斷以下使用者訊息是否與「健康、醫療、身體狀況、疾病、藥物、營養、運動健身、心理健康」相關。\n\n"
    "使用者訊息：\n"
)

AsyncStrToBool = Callable[[str], Awaitable[bool]]


class GuardrailService:
    """以注入的「文字 → bool」分類器，決定使用者訊息是否可以走 RAG 工具。"""

    def __init__(self, async_text_to_bool: AsyncStrToBool) -> None:
        """注入分類器；不綁特定模型實作，方便替換或測試。"""
        self._async_text_to_bool = async_text_to_bool

    async def allow_rag_tool(self, user_text: str) -> bool:
        """組分類 prompt 後呼叫分類器，回傳是否允許 RAG；分類失敗時 fail-open。"""
        # 快速路徑：如果是座標位置訊息，不啟用 RAG 工具
        if user_text.startswith("這是我的目前位置") or "lat=" in user_text:
            logger.info("檢測到位置訊息，自動跳過 RAG Guardrail 並禁用 RAG 工具。")
            return False

        try:
            result = await self._async_text_to_bool(
                f"{_CLASSIFICATION_PROMPT}{user_text}",
            )
            return bool(result)
        except Exception as e:
            # Guardrail 分類失敗時採 fail-open，避免 Gemini 暫時錯誤阻斷使用者流程。
            try:
                raise map_guardrail_classification_error(e) from e
            except GuardrailClassificationError as mapped:
                logger.error(f"Guardrail 分類失敗（{mapped}）")
                return True
        except BaseException as e:
            logger.error(f"Guardrail 分類失敗（未處理錯誤）: {e}")
            return True
