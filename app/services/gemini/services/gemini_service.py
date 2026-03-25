from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import httpx
from app.services.gemini.client.gemini_client import GeminiClient
from app.services.gemini.client.prompt_config import PromptConfig
from app.services.gemini.shared.errors import GeminiSchemaError
from app.services.gemini.shared.types import GeminiResult
from app.services.gemini.shared.validation import validate_user_input
import logging

logger = logging.getLogger(__name__)


class GeminiService:
    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
        http_client_factory: Callable[
            [float], AbstractAsyncContextManager[httpx.AsyncClient]
        ] | None = None,
        gemini_client: GeminiClient | None = None,
        prompt_config: PromptConfig | None = None,
    ) -> None:
        self.gemini_client = gemini_client or GeminiClient(
            api_key=api_key,
            model_name=model_name,
            http_client_factory=http_client_factory,
        )
        self.prompt_config = prompt_config or PromptConfig()
        logger.info(
            f"GeminiService initialized with model: {self.gemini_client.model_name}"
        )

    async def generate_content(self, payload: dict, timeout: float = 300.0) -> dict:
        return await self.gemini_client.generate_content(payload, timeout=timeout)

    async def generate_response(
        self, user_input: str, tools: list = None
    ) -> GeminiResult:
        validation = validate_user_input(user_input)
        if not validation.is_valid:
            return GeminiResult(text=validation.error_message)

        payload = {
            "contents": [{"parts": [{"text": user_input}]}],
            "systemInstruction": {
                "parts": [{"text": self.prompt_config.system_instruction}]
            },
        }

        if tools:
            payload["tools"] = tools

        try:
            logger.info(f"Sending tool-enabled request to Gemini: {user_input[:50]}...")
            data = await self.generate_content(payload, timeout=300.0)
            part = data["candidates"][0]["content"]["parts"][0]

            # Gemini 回傳 functionCall 時，part 內有 "functionCall" 欄位
            if "functionCall" in part:
                func = part["functionCall"]
                logger.info(f"Gemini requested function call: {func['name']}")
                return GeminiResult(
                    function_name=func["name"],
                    function_args=func.get("args", {}),
                )

            return GeminiResult(text=part["text"])
        except KeyError as e:
            raise GeminiSchemaError(f"AI 服務回應格式異常：缺少欄位 {e}")
