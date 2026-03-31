import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import httpx

from app.infrastructure.gemini.shared.errors import (
    GeminiHttpError,
    GeminiNetworkError,
    GeminiSchemaError,
    GeminiUnknownError,
)

logger = logging.getLogger(__name__)


class GeminiClient:
    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
        http_client_factory: Callable[
            [float], AbstractAsyncContextManager[httpx.AsyncClient]
        ]
        | None = None,
    ) -> None:
        self.api_key = api_key
        self.model_name = model_name
        self.http_client_factory = (
            http_client_factory
            if http_client_factory is not None
            else (lambda timeout: httpx.AsyncClient(timeout=timeout))
        )
        self.api_url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model_name}:generateContent"
        )

    async def generate_content(self, payload: dict, timeout: float = 300.0) -> dict:
        """呼叫 Gemini generateContent 並回傳原始 JSON。"""
        try:
            async with self.http_client_factory(timeout) as client:
                response = await client.post(
                    self.api_url,
                    params={"key": self.api_key},
                    json=payload,
                )

                if response.status_code != 200:
                    logger.error(f"Gemini API error: {response.status_code}")
                    raise GeminiHttpError(
                        status_code=response.status_code,
                        message=f"AI 服務發生錯誤（狀態碼: {response.status_code}）",
                    )

                return response.json()

        except httpx.TimeoutException:
            raise GeminiNetworkError("請求超時，請檢查網路連線")
        except httpx.NetworkError:
            raise GeminiNetworkError("無法連線到 AI 服務，請檢查網路連線")
        except KeyError as e:
            raise GeminiSchemaError(f"AI 服務回應格式異常：缺少欄位 {e}")
        except GeminiHttpError:
            raise
        except GeminiNetworkError:
            raise
        except GeminiSchemaError:
            raise
        except Exception as e:
            raise GeminiUnknownError(f"處理請求時發生錯誤: {e}")
