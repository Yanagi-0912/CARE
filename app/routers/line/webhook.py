from fastapi import APIRouter, Request, Header, HTTPException
from linebot.v3.webhooks import MessageEvent, TextMessageContent, LocationMessageContent
from linebot.v3.webhook import WebhookParser
from linebot.v3.exceptions import InvalidSignatureError
from app.services.line import handle_text_message_async, handle_location_message_async
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

# 初始化路由器和 webhook 解析器
router = APIRouter()
parser = WebhookParser(settings.LINE_CHANNEL_SECRET)


@router.post("/callback")
async def callback(request: Request, x_line_signature: str = Header(None)):
    # 驗證是否包含 X-Line-Signature header
    if x_line_signature is None:
        logger.error("Missing X-Line-Signature header")
        raise HTTPException(status_code=400, detail="Missing X-Line-Signature header")

    # 獲取請求 body
    body = await request.body()
    body_decoded = body.decode("utf-8")

    try:
        # 驗證簽名並解析事件
        events = parser.parse(body_decoded, x_line_signature)

        # 異步處理每個事件
        for event in events:
            # 處理文字訊息事件
            if isinstance(event, MessageEvent) and isinstance(
                event.message, TextMessageContent
            ):
                await handle_text_message_async(event)
            # 處理位置訊息事件（用戶透過 Quick Reply 傳回 GPS 座標）
            elif isinstance(event, MessageEvent) and isinstance(
                event.message, LocationMessageContent
            ):
                await handle_location_message_async(event)

        logger.info("Webhook events processed successfully")

    except InvalidSignatureError:
        logger.error("Invalid signature - possible security breach attempt")
        raise HTTPException(status_code=400, detail="Invalid signature")

    except Exception as e:
        logger.error(f"Unexpected error in webhook: {e}", exc_info=True)
        # LINE 平台仍然期望收到 200 OK，否則會重試
        # 因此即使內部處理失敗，我們也返回 OK

    return "OK"
