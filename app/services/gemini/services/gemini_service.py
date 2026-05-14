"""Gemini 對外服務。

提供基礎的 LLM 實例，以及結構化 boolean 輸出（供 Guardrail 使用）。
原有的 generate_response 與 tool 解析邏輯已被 LangGraph 取代而移除。
"""

import logging
from collections.abc import Awaitable
from typing import Any
from langchain_core.messages import HumanMessage
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_google_genai import ChatGoogleGenerativeAI
from app.services.gemini.shared.errors import (
    GeminiHttpError,
    GeminiNetworkError,
    GeminiSchemaError,
    GeminiUnknownError,
    raise_mapped_gemini_error,
)

logger = logging.getLogger(__name__)

# Gemini 結構化輸出逾時（秒）
STRUCTURED_OUTPUT_TIMEOUT_SEC = 30.0


class GeminiService:
    """封裝 LangChain `ChatGoogleGenerativeAI`，提供：
    - `_chat_llm`: 供 LangGraph Agent 綁定使用
    - `invoke_boolean_structured_output`：boolean structured output（給 Guardrail 等分類使用）。
    """

    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
    ) -> None:
        """初始化 chat model；保留 `_chat_llm` 不對外暴露。"""
        self._chat_llm = ChatGoogleGenerativeAI(
            model=model_name,
            google_api_key=api_key,
            temperature=0,
        )
        logger.info(
            "GeminiService 已初始化（LangChain）：模型=%s",
            model_name,
        )

    async def invoke_boolean_structured_output(self, user_content: str) -> bool:
        """以 JSON Schema `{"type": "boolean"}` 強制模型回傳 bool；非 bool 時拋 `GeminiSchemaError`。"""
        messages = [HumanMessage(content=user_content)]
        structured: Runnable = self._chat_llm.with_structured_output(
            {"type": "boolean"},
            method="json_schema",
        )
        result = await _await_with_mapped_gemini_errors(
            structured.ainvoke(
                messages,
                config=RunnableConfig(timeout=STRUCTURED_OUTPUT_TIMEOUT_SEC),
            )
        )
        if isinstance(result, bool):
            return result
        # LangChain structured output 理論上應回傳 bool；若不是，視為模型回應格式錯誤。
        raise GeminiSchemaError("AI 服務回應格式異常：預期 boolean")


async def _await_with_mapped_gemini_errors(awaitable: Awaitable[Any]) -> Any:
    """await 一個 LangChain coroutine；底層例外統一 map 成專案 `Gemini*Error`。"""
    try:
        return await awaitable
    except (GeminiHttpError, GeminiNetworkError, GeminiUnknownError):
        raise
    except Exception as e:
        raise_mapped_gemini_error(e)
