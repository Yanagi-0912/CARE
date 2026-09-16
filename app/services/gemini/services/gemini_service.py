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
from app.core.config import settings
from app.services.gemini.shared.errors import (
    GeminiHttpError,
    GeminiNetworkError,
    GeminiSchemaError,
    GeminiUnknownError,
    raise_mapped_gemini_error,
)

logger = logging.getLogger(__name__)

# 單次請求失敗後的重試次數。langchain-google-genai 4.2.2 預設 6 次、且預設沒有
# 逾時：一則訊息在 RAG 路徑上最多打 8 次 Gemini（guardrail、急迫度、改寫、分級、
# 生成、agent 決策×2、網搜生成），每次都可能重試 6 次，Gemini 一慢就是重試風暴，
# 而使用者早就等不到 45 秒總預算結束。2 次：能吃掉單次瞬斷（429／503），又不會
# 讓一次呼叫的最壞情況超過 3 × 逾時。模組常數而非 env：這不該隨環境改。
DEFAULT_MAX_RETRIES = 2


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
        temperature: float = 0.0,
        thinking_level: str | None = None,
        timeout: float | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        """初始化 chat model；公開 `chat_model` 屬性。

        這裡是專案裡唯一建 `ChatGoogleGenerativeAI` 的地方（dependencies 建的
        三個實例都經過這裡），逾時與重試次數就集中在這裡設，不讓任何呼叫端
        拿到沒有逾時的模型。`timeout` 預設取 `GEMINI_REQUEST_TIMEOUT_SECONDS`
        （理由見 config），`max_retries` 見 `DEFAULT_MAX_RETRIES`。

        `temperature` 預設 0，正式路徑一律沿用。留出參數是給評測用的：
        要量測模型在同一張影像上的答案穩不穩，必須讓它有機會給出不同答案。

        `thinking_level` 預設 None＝沿用模型預設（gemini-3.8-flash 是 medium）。
        只給延遲敏感、推理需求低的呼叫用，例如查詢改寫（見
        `query_rewriter.REWRITE_THINKING_LEVEL`）。可用的值依模型而定，
        不支援的值會在呼叫時回 400。
        """
        extra: dict[str, Any] = {}
        if thinking_level is not None:
            extra["thinking_level"] = thinking_level
        self.timeout = (
            float(timeout)
            if timeout is not None
            else float(settings.GEMINI_REQUEST_TIMEOUT_SECONDS)
        )
        self.max_retries = int(max_retries)
        self.chat_model = ChatGoogleGenerativeAI(
            model=model_name,
            google_api_key=api_key,
            temperature=temperature,
            timeout=self.timeout,
            max_retries=self.max_retries,
            **extra,
        )
        logger.info(
            "GeminiService 已初始化（LangChain）：模型=%s thinking_level=%s "
            "timeout_s=%s max_retries=%s",
            model_name,
            thinking_level or "default",
            self.timeout,
            self.max_retries,
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

    async def invoke_structured_output(
        self,
        *,
        prompt: str,
        json_schema: dict[str, Any],
    ) -> Any:
        """以純文字提示詞取得 schema 約束的結構化輸出。"""
        messages = [HumanMessage(content=prompt)]
        structured: Runnable = self.chat_model.with_structured_output(
            json_schema,
            method="json_schema",
        )
        return await _await_with_mapped_gemini_errors(structured.ainvoke(messages))

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
