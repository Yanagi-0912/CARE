# 包裝原本的agent 和 line message service，讓它們在處理訊息的同時也能記錄到
# consultation service 中。
from __future__ import annotations
from typing import Any, Optional
from app.services.consultation.consultation_service import ConsultationService
from app.services.consultation.context import get_current_consultation_context
import logging


# 這裡定義了一些代理類別，用來包裝原本的 agent 和 line message service
class ConsultationAwareAgent:
    def __init__(self, agent: Any, consultation_service: ConsultationService) -> None:
        self._agent = agent
        self._consultation_service = consultation_service

    async def invoke(self, user_input: str) -> dict:
        # 先把使用者輸入記錄到 consultation service

        ctx = get_current_consultation_context()

        logger = logging.getLogger(__name__)
        logger.info(
            f"[ConsultationAwareAgent] 開始呼叫 record_user_message，line_id={ctx.line_id if ctx else None}"
        )
        await self._consultation_service.record_user_message(user_input)
        # 再調用原本的 agent 來獲取回覆
        return await self._agent.invoke(user_input=user_input)


class ConsultationAwareLineMessageService:
    def __init__(self, service: Any, consultation_service: ConsultationService) -> None:
        self._service = service
        self._consultation_service = consultation_service

    async def send_line_reply(
        self,
        reply_token: str,
        message_text: str,
        user_id: Optional[str] = None,
        request_location: bool = False,
    ) -> bool:

        success = await self._service.send_line_reply(
            reply_token,
            message_text,
            user_id,
            request_location=request_location,
        )

        if success:
            await self._consultation_service.record_assistant_message(message_text)

        return success

    def __getattr__(self, item: str) -> Any:
        return getattr(self._service, item)
