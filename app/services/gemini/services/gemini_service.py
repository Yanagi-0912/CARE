"""Gemini 對外服務。

提供基礎的 LLM 實例，以及結構化 boolean 輸出（供 Guardrail 使用）。
原有的 generate_response 與 tool 解析邏輯已被 LangGraph 取代而移除。
"""

import logging
from collections.abc import Awaitable
from datetime import date
from typing import Any
from langchain_core.messages import HumanMessage
from langchain_core.runnables import Runnable
from langchain_google_genai import ChatGoogleGenerativeAI
from app.models.chat_message import ChatMessage
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
            structured.ainvoke(messages)
        )
        if isinstance(result, bool):
            return result
        # LangChain structured output 理論上應回傳 bool；若不是，視為模型回應格式錯誤。
        raise GeminiSchemaError("AI 服務回應格式異常：預期 boolean")

    async def generate_consultation_summary(
        self, target_date: date, messages: list[ChatMessage]
    ) -> str:
        """呼叫 Gemini 生成對話摘要。"""
        if not messages:
            return "該日期尚無諮詢記錄。"

        transcript_lines = []
        for message in messages:
            transcript_lines.append(f"[{message.message_type}] {message.content}")
        prompt = f"""
        你是醫療諮詢摘要助手。
        請根據對話輸出 JSON。

        規則：
        - 僅輸出 JSON
        - "症狀"欄位只能填寫使用者自己明確提到的症狀，不可包含 AI 回覆中提到的內容
        - 「建議」只填寫 AI 給出的核心行動建議，大約 3-5 項
        - 不要 markdown
        - 不要額外說明
        - 不要輸出任何空陣列 []
        - 只要沒有資料，就直接填寫「無」
        - 若某欄位有多個項目，請用「、」分隔成單一字串

        schema:
        {{
        "主訴": string,
        "症狀": string,
        "檢查": string,
        "建議": string,
        "重要時間點": string,
        "其他": string,
        "AI小摘要": string
        }}

        輸出格式注意：
        - 每個欄位都必須是可直接閱讀的中文字串
        - 若該欄位沒有可填內容，請寫「無」
        - "AI小摘要" 請用 1 到 3 句話總結整體重點，並給出下一步建議或提醒

        日期：{target_date.isoformat()}

        對話：
        {transcript_lines}
        """

        try:
            result = await self._chat_llm.ainvoke(prompt)
        except Exception as exc:
            raise_mapped_gemini_error(exc)
        content = getattr(result, "content", "")
        summary_text = str(content).strip()
        return summary_text or "該日期尚無可摘要內容。"



async def _await_with_mapped_gemini_errors(awaitable: Awaitable[Any]) -> Any:
    """await 一個 LangChain coroutine；底層例外統一 map 成專案 `Gemini*Error`。"""
    try:
        return await awaitable
    except (GeminiHttpError, GeminiNetworkError, GeminiUnknownError):
        raise
    except Exception as e:
        raise_mapped_gemini_error(e)
