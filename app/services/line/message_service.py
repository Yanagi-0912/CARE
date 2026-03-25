from typing import Optional, Protocol
from linebot.v3.messaging import (
    ReplyMessageRequest,
    TextMessage,
    QuickReply,
    QuickReplyItem,
    LocationAction,
)
from app.orchestration import ResponseOrchestrator
from app.services.line.shared.errors import LineTokenError, LineValidationError
from app.services.line.shared.validation import (
    validate_reply_context,
    validate_text_message,
)
import logging

logger = logging.getLogger(__name__)


class TokenProvider(Protocol): # protocol 是來規範 傳進來的 di 要怎麼使用 建立一個role 給他
    def get_token(self) -> str: ...
'''get_token() 的實作在 LineTokenManager；
LineMessageService 只依賴 TokenProvider 介面，
具體實例由 dependencies.py 建立並注入，避免 service 耦合具體類別與建立邏輯。
'''
class MedicalServiceLike(Protocol):
    def request_location(self, user_id: str) -> dict: ...


class LineMessagingClientLike(Protocol):
    def reply_message(self, access_token: str, request: ReplyMessageRequest) -> None: ...


class LineMessageService:
    def __init__( # 依賴注入點
    #有沒有想過不直接使用 token_manager 的class 就好
    # 因為要 low coupling 不要使用 直接依賴 token_manager 的class 會造成耦合
    
        self,
        response_orchestrator: ResponseOrchestrator,
        token_provider: TokenProvider, 
        medical_service: MedicalServiceLike,
        line_messaging_client: LineMessagingClientLike,
    ):
        self.response_orchestrator = response_orchestrator
        self.token_provider = token_provider
        self.medical_service = medical_service
        self.line_messaging_client = line_messaging_client
        logger.info("LineMessageService initialized with Gemini AI")

    async def process_and_reply(
        self, user_text: str, reply_token: str, user_id: Optional[str] = None
    ) -> bool:
        try:
            validate_reply_context(reply_token, user_id)
            normalized_text = validate_text_message(user_text)
            logger.info(f"Processing message from user {user_id}: {user_text[:50]}...")
            result = await self.response_orchestrator.orchestrate_response(
                normalized_text
            )

            if result.is_function_call and result.function_name == "request_location":
                return await self.send_location_quick_reply(reply_token, user_id)

            response_text = result.text or "抱歉，我無法理解您的問題，請重新輸入。"
            success = await self.send_line_reply(reply_token, response_text, user_id)

            if success:
                logger.info(f"Successfully processed and replied to user {user_id}")
            return success

        except LineValidationError as e:
            logger.warning(f"Validation in process_and_reply: {e}")
            await self.send_line_reply(reply_token, str(e), user_id)
            return False

        except LineTokenError as e:
            logger.error(f"LINE token error in process_and_reply: {e}")
            await self._send_error_reply(reply_token, user_id)
            return False

        except ValueError as e:
            logger.error(f"API error in process_and_reply: {e}")
            fallback = f"抱歉，AI 服務暫時無法使用：{e}"
            await self.send_line_reply(reply_token, fallback, user_id)
            return False

        except Exception as e:
            logger.error(f"Error in process_and_reply: {e}", exc_info=True)
            await self._send_error_reply(reply_token, user_id)
            return False

    async def send_line_reply(
        self, reply_token: str, message_text: str, user_id: Optional[str] = None
    ) -> bool:
        try:
            validate_reply_context(reply_token, user_id)
            access_token = self.token_provider.get_token()
            self.line_messaging_client.reply_message(
                access_token,
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(text=message_text)],
                ),
            )

            logger.info(f"Message sent to LINE for user {user_id}")
            return True

        except LineValidationError as e:
            logger.warning(f"Validation in send_line_reply: {e}")
            return False

        except LineTokenError as e:
            logger.error(f"Failed to get LINE token: {e}")
            return False

        except Exception as e:
            logger.error(f"Failed to send LINE message: {e}", exc_info=True)
            return False

    async def send_location_quick_reply(
        self, reply_token: str, user_id: Optional[str] = None
    ) -> bool:
        try:
            validate_reply_context(reply_token, user_id)
            self.medical_service.request_location(user_id)
            access_token = self.token_provider.get_token()
            request = ReplyMessageRequest(
                reply_token=reply_token,
                messages=[
                    TextMessage(
                        text="請傳送您目前的位置資訊，我將為您尋找附近的醫療院所 ",
                        quick_reply=QuickReply(
                            items=[
                                QuickReplyItem(
                                    action=LocationAction(label="傳送位置資訊")
                                )
                            ]
                        ),
                    )
                ],
            )
            self.line_messaging_client.reply_message(access_token, request)
            logger.info(f"Location quick reply sent to user {user_id}")
            return True

        except LineValidationError as e:
            logger.warning(f"Validation in send_location_quick_reply: {e}")
            return False

        except LineTokenError as e:
            logger.error(f"LINE token error in send_location_quick_reply: {e}")
            return False

        except Exception as e:
            logger.error(f"Failed to send location quick reply: {e}", exc_info=True)
            return False

    async def _send_error_reply(
        self, reply_token: str, user_id: Optional[str] = None
    ) -> bool:
        try:
            error_message = "抱歉，處理您的訊息時發生錯誤，請稍後再試"
            return await self.send_line_reply(reply_token, error_message, user_id)
        except Exception as e:
            logger.error(f"Failed to send error reply: {e}")
            return False
