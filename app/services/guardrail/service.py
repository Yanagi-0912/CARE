"""Guardrail：判斷使用者訊息是否與健康或醫療識詐相關，決定是否啟用 RAG。"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

_CLASSIFICATION_PROMPT = (
    "你是一個訊息分類器。請判斷以下使用者訊息是否與下列主題相關：\n"
    "健康、醫療、身體狀況、疾病、藥物、營養、運動健身、心理健康；\n"
    "或醫療場景詐騙／識詐（例如假藥、假醫師、假醫院或健保相關簡訊、"
    "保證療效的可疑保健話術、因醫療／檢驗／健保／保險理賠名義要求匯款或點擊不明連結）。\n\n"
    "使用者訊息：\n"
)

AsyncStrToBool = Callable[[str], Awaitable[bool]]


class GuardrailService:
    """以注入的「文字 → bool」分類器，決定是否允許 RAG。"""

    def __init__(self, async_text_to_bool: AsyncStrToBool) -> None:
        self._async_text_to_bool = async_text_to_bool

    async def allow_rag_tool(self, user_text: str) -> bool:
        if user_text.startswith("這是我的目前位置") or "lat=" in user_text:
            logger.debug("檢測到位置訊息，跳過分類並禁用 RAG。")
            return False

        try:
            return bool(
                await self._async_text_to_bool(
                    f"{_CLASSIFICATION_PROMPT}{user_text}",
                )
            )
        except Exception as e:
            # fail-open：分類失敗不阻斷對話
            logger.error("Guardrail 分類失敗（fail-open）: %s", e, exc_info=True)
            return True
