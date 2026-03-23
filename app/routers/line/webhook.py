from fastapi import APIRouter, Request, Header, HTTPException
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent,
    LocationMessageContent,
    ImageMessageContent,
    VideoMessageContent,
    AudioMessageContent,
    FileMessageContent,
)
from linebot.v3.webhook import WebhookParser
from linebot.v3.exceptions import InvalidSignatureError
from app.services.line import LineEventContext
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

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
            # 為每個事件建立獨立的 Handler 實例，確保異步安全；如果缺少必要欄位會拋出 ValueError
            try:
                event_context = LineEventContext(event)
            except ValueError as e:
                logger.warning(f"跳過無效的 LINE 事件: {e}")
                continue

            # 處理文字訊息事件
            if isinstance(event, MessageEvent) and isinstance(
                event.message, TextMessageContent
            ):
                await event_context.handle_text_message()
            # 處理位置訊息事件（用戶透過 Quick Reply 傳回 GPS 座標）
            elif isinstance(event, MessageEvent) and isinstance(
                event.message, LocationMessageContent
            ):
                await event_context.handle_location_message()
            # 處理多媒體與檔案訊息事件 (圖片、影片、音訊、檔案)
            elif isinstance(
                event.message,
                (
                    ImageMessageContent,
                    VideoMessageContent,
                    AudioMessageContent,
                    FileMessageContent,
                ),
            ):
                await event_context.handle_media_message()

        logger.info("Webhook events processed successfully")

    except InvalidSignatureError:
        logger.error("Invalid signature - possible security breach attempt")
        raise HTTPException(status_code=400, detail="Invalid signature")

    except Exception as e:
        logger.error(f"Unexpected error in webhook: {e}", exc_info=True)
        # LINE 平台仍然期望收到 200 OK，否則會重試
        # 因此即使內部處理失敗，我們也返回 OK

    return "OK"
