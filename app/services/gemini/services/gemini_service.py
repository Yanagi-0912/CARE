"""Gemini 對外服務。

提供基礎的 LLM 實例，以及結構化 boolean 輸出（供 Guardrail 使用）。
原有的 generate_response 與 tool 解析邏輯已被 LangGraph 取代而移除。
"""

import base64
import logging
from collections.abc import Awaitable
from typing import Any
from langchain_core.messages import HumanMessage
from langchain_core.runnables import Runnable
from langchain_google_genai import ChatGoogleGenerativeAI
from app.services.gemini.shared.errors import (
    GeminiHttpError,
    GeminiNetworkError,
    GeminiSchemaError,
    GeminiUnknownError,
    raise_mapped_gemini_error,
)

logger = logging.getLogger(__name__)


class GeminiService:
    """封裝 LangChain `ChatGoogleGenerativeAI`，提供：
    - `chat_model`: 供外部業務服務與 LangGraph Agent 綁定使用
    - `invoke_boolean_structured_output`：boolean structured output（給 Guardrail 等分類使用）。
    """

    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
    ) -> None:
        """初始化 chat model；公開 `chat_model` 屬性。"""
        self.chat_model = ChatGoogleGenerativeAI(
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
        structured: Runnable = self.chat_model.with_structured_output(
            {"type": "boolean"},
            method="json_schema",
        )
        result = await _await_with_mapped_gemini_errors(
            structured.ainvoke(messages)
        )
        if isinstance(result, bool):
            return result
        # LangChain structured output 理論上應回傳 bool；若不是，視為模型回應格式錯誤。
        raise GeminiSchemaError("AI 服務回應格式異常：預期 boolean")

    async def invoke_structured_output_with_image(
        self,
        *,
        prompt: str,
        image_bytes: bytes,
        mime_type: str,
        json_schema: dict[str, Any],
    ) -> Any:
        """以影像加提示詞取得 schema 約束的結構化輸出。

        影像以 data URI 內嵌在訊息裡，不落地成檔案、也不產生任何對外可讀取的
        網址——藥袋帶有姓名、就診機構與適應症，多一個存放點就多一個外洩面。
        """
        encoded_image = base64.b64encode(image_bytes).decode("ascii")
        messages = [
            HumanMessage(
                content=[
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": f"data:{mime_type};base64,{encoded_image}",
                    },
                ]
            )
        ]
        structured: Runnable = self.chat_model.with_structured_output(
            json_schema,
            method="json_schema",
        )
        return await _await_with_mapped_gemini_errors(structured.ainvoke(messages))


async def _await_with_mapped_gemini_errors(awaitable: Awaitable[Any]) -> Any:
    """await 一個 LangChain coroutine；底層例外統一 map 成專案 `Gemini*Error`。"""
    try:
        return await awaitable
    except (GeminiHttpError, GeminiNetworkError, GeminiUnknownError):
        raise
    except Exception as e:
        raise_mapped_gemini_error(e)
