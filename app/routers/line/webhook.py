from fastapi import APIRouter, Request, Header, HTTPException
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

        for event in events:
            try:
                event_context = LineEventContext(event)
            except ValueError as e:
                logger.warning(f"跳過無效的 LINE 事件: {e}")
                continue

            await event_context.dispatch()

        logger.info("Webhook events processed successfully")

    except InvalidSignatureError:
        logger.error("Invalid signature - possible security breach attempt")
        raise HTTPException(status_code=400, detail="Invalid signature")

    except Exception as e:
        logger.error(f"Unexpected error in webhook: {e}", exc_info=True)
        # LINE 平台仍然期望收到 200 OK，否則會重試
        # 因此即使內部處理失敗，我們也返回 OK

    return "OK"
