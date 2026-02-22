"""
LINE Bot Webhook 路由層
負責接收來自 LINE 平台的 Webhook 請求、驗證簽名並分發事件
"""
from fastapi import APIRouter, Request, Header, HTTPException
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.webhook import WebhookParser
from linebot.v3.exceptions import InvalidSignatureError
from app.routers.line.handlers import handle_text_message_async
from app.core.config import settings
import logging
import json

logger = logging.getLogger(__name__)

# 初始化路由器和 webhook 解析器
router = APIRouter()
parser = WebhookParser(settings.LINE_CHANNEL_SECRET)


@router.post("/callback")
async def callback(request: Request, x_line_signature: str = Header(None)):
    """
    LINE Bot Webhook 回調端點
    
    此端點接收來自 LINE 平台的所有事件通知（消息、追蹤、取消追蹤等）
    
    Args:
        request: FastAPI Request 對象
        x_line_signature: LINE 平台提供的簽名，用於驗證請求來源
    
    Returns:
        str: 返回 "OK" 表示成功接收
    
    Raises:
        HTTPException: 當簽名驗證失敗或缺少簽名時
    """
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
            # 處理文字消息事件
            if isinstance(event, MessageEvent) and isinstance(event.message, TextMessageContent):
                await handle_text_message_async(event)
        
        logger.info("Webhook events processed successfully")
        
    except InvalidSignatureError:
        logger.error("Invalid signature - possible security breach attempt")
        raise HTTPException(status_code=400, detail="Invalid signature")
    
    except Exception as e:
        logger.error(f"Unexpected error in webhook: {e}", exc_info=True)
        # LINE 平台仍然期望收到 200 OK，否則會重試
        # 因此即使內部處理失敗，我們也返回 OK
    
    return "OK"
