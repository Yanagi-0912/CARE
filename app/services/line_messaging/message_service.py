from typing import Optional, Protocol
from linebot.v3.messaging import (
    ReplyMessageRequest,
    TextMessage,
    QuickReply,
    QuickReplyItem,
    LocationAction,
)

from app.services.line_messaging.shared.errors import LineTokenError, LineValidationError
from app.services.line_messaging.shared.validation import (
    validate_reply_context,
)
import logging

logger = logging.getLogger(__name__)


class TokenProvider(
    Protocol
):  # protocol 是來規範 傳進來的 di 要怎麼使用 建立一個role 給他
    def get_token(self) -> str: ...


"""get_token() 的實作在 LineTokenManager；
LineMessageService 只依賴 TokenProvider 介面，
具體實例由 dependencies.py 建立並注入，避免 service 耦合具體類別與建立邏輯。
"""


class MedicalServiceLike(Protocol):
    pass


class LineMessagingClientLike(Protocol):
    def reply_message(
        self, access_token: str, request: ReplyMessageRequest
    ) -> None: ...


class LineMessageService:
    def __init__(  # 依賴注入點
        # 有沒有想過不直接使用 token_manager 的class 就好
        # 因為要 low coupling 不要使用 直接依賴 token_manager 的class 會造成耦合
        self,
        token_provider: TokenProvider,
        medical_service: MedicalServiceLike,
        line_messaging_client: LineMessagingClientLike,
    ):
        self.token_provider = token_provider
        self.medical_service = medical_service
        self.line_messaging_client = line_messaging_client
        logger.info("LineMessageService initialized with Gemini AI")

    async def send_line_reply(
        self, reply_token: str, message_text: str, user_id: Optional[str] = None, request_location: bool = False
    ) -> bool:
        try:
            validate_reply_context(reply_token, user_id)
            access_token = self.token_provider.get_token()
            
            if request_location:
                quick_reply = QuickReply(
                    items=[
                        QuickReplyItem(
                            action=LocationAction(label="分享位置資訊")
                        )
                    ]
                )
                text_message = TextMessage(text=message_text, quickReply=quick_reply)
            else:
                text_message = TextMessage(text=message_text)

            self.line_messaging_client.reply_message(
                access_token,
                ReplyMessageRequest(
                    replyToken=reply_token,
                    messages=[text_message],
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


    async def _send_error_reply(
        self, reply_token: str, user_id: Optional[str] = None
    ) -> bool:
        try:
            error_message = "抱歉，處理您的訊息時發生錯誤，請稍後再試"
            return await self.send_line_reply(reply_token, error_message, user_id)
        except Exception as e:
            logger.error(f"Failed to send error reply: {e}")
            return False
