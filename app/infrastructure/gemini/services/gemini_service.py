"""Gemini 對外服務。

舊：透過 `GeminiClient` 自行用 httpx 打 generateContent，並手動解析 JSON。
新：直接使用 LangChain `ChatGoogleGenerativeAI`，提供：
- `generate_response`：一般對話／tool-call 路由。
- `invoke_boolean_structured_output`：結構化 boolean，用於 Guardrail 等分類場景。
"""
import logging
from collections.abc import Awaitable
from typing import Any
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_google_genai import ChatGoogleGenerativeAI
from app.infrastructure.gemini.client.prompt_config import PromptConfig
from app.infrastructure.gemini.shared.errors import (
    GeminiHttpError,
    GeminiNetworkError,
    GeminiSchemaError,
    GeminiUnknownError,
    raise_mapped_gemini_error,
)
from app.infrastructure.gemini.shared.types import GeminiResult
from app.infrastructure.gemini.shared.validation import validate_user_input

logger = logging.getLogger(__name__)

# Gemini 主回覆逾時（秒）
CHAT_RESPONSE_TIMEOUT_SEC = 30.0
STRUCTURED_OUTPUT_TIMEOUT_SEC = 30.0

async def _await_with_mapped_gemini_errors(awaitable: Awaitable[Any]) -> Any:
    """await 一個 LangChain coroutine；底層例外統一 map 成專案 `Gemini*Error`。"""
    try:
        return await awaitable
    except (GeminiHttpError, GeminiNetworkError, GeminiUnknownError):
        raise
    except Exception as e:
        raise_mapped_gemini_error(e)

def _single_tool_call(msg: AIMessage) -> tuple[str, dict] | None:
    """從 `AIMessage.tool_calls` 取出唯一一筆 (name, args)；不是恰好 1 筆則回 `None`。"""
    if not hasattr(msg, "tool_calls"):
        logger.debug("AIMessage 缺少 tool_calls 屬性，視為無工具呼叫")
        return None
    tool_calls = msg.tool_calls or []
    if len(tool_calls) != 1:
        return None
    tc = tool_calls[0]
    if not isinstance(tc, dict):
        return None
    name = tc.get("name")
    args = tc.get("args") or {}
    if not name:
        return None
    return str(name), dict(args) if isinstance(args, dict) else {}

class GeminiService:
    """封裝 LangChain `ChatGoogleGenerativeAI`，提供：
    - `generate_response`：一般對話／tool-call 路由。
    - `invoke_boolean_structured_output`：boolean structured output（給 Guardrail 等分類使用）。
    """
    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
        prompt_config: PromptConfig | None = None,
    ) -> None:
        """初始化 chat model 與 prompt 設定；保留 `_chat_llm` 不對外暴露。"""
        self.prompt_config = (
            prompt_config if prompt_config is not None else PromptConfig()
        )
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
        raise GeminiSchemaError(
            "AI 服務回應格式異常：預期 boolean"
        )

    async def generate_response(
        self, user_input: str, tools: list | None = None
    ) -> GeminiResult:
        """主對話入口：驗證輸入 → 呼叫 chat model（必要時 bind tools）→ 回 tool-call 或文字內容。"""
        validation = validate_user_input(user_input)
        if not validation.is_valid:
            return GeminiResult(text=validation.error_message)

        messages = [
            SystemMessage(content=self.prompt_config.system_instruction),
            HumanMessage(content=user_input),
        ]
        # 清洗傳入 tools：只保留 dict 宣告；None 會視為空清單。
        decls = [t for t in (tools or []) if isinstance(t, dict)]
        # 有工具宣告就 bind tools（開啟 tool-calling 模式）；否則走純聊天模式。
        # 注意：bind_tools 只讓模型「可以提出工具呼叫需求」，不會自動執行工具。
        runnable: Runnable = (
            self._chat_llm.bind_tools(decls) if decls else self._chat_llm
        )

        logger.info(
            "Gemini 主回覆請求（LangChain，工具宣告數=%s）：%s...",
            len(decls),
            user_input[:50],
        )
        msg = await _await_with_mapped_gemini_errors(
            runnable.ainvoke(
                messages,
                config=RunnableConfig(timeout=CHAT_RESPONSE_TIMEOUT_SEC),
            )
        )

        if not isinstance(msg, AIMessage):
            raise GeminiSchemaError("AI 服務回應格式異常：非 AIMessage")

        # 從模型回應讀出 tool_call，轉成 GeminiResult(function_name, function_args)，
        # 交由上層 orchestrator / handler 決定如何執行與路由下一步。
        single_tool_call = _single_tool_call(msg)
        if single_tool_call is not None:
            function_name, function_args = single_tool_call
            logger.info("模型要求呼叫工具：%s", function_name)
            return GeminiResult(
                function_name=function_name,
                function_args=function_args,
            )

        # 這裡保留 LangChain 原始 content：
        # - 一般對話常是 str
        # - 工具決策那一輪常是 list[block]（例如 {"type":"text","text":"..."}）
        # 工具路由主訊號看 tool_calls；content 只作為補充內容回傳給上層觀測。
        raw_content: str | list[object] = msg.content
        return GeminiResult(text=raw_content)
