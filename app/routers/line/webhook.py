from fastapi import APIRouter, Request, Header, HTTPException, Depends
from linebot.v3.webhook import WebhookParser
from linebot.v3.exceptions import InvalidSignatureError

from app.core.config import settings
from app.dependencies import get_line_event_handler
from app.services.line_messaging import LineEventHandler

router = APIRouter()

# 建立 LINE Webhook 解析器
parser = WebhookParser(settings.LINE_CHANNEL_SECRET)


@router.post("/callback")
async def callback(
    request: Request,
    x_line_signature: str = Header(None),
    event_handler: LineEventHandler = Depends(get_line_event_handler),
):
    if not x_line_signature:
        raise HTTPException(status_code=400, detail="Missing X-Line-Signature header")

    # 讀取請求內容
    body = await request.body()
    body_str = body.decode("utf-8")

    try:
        # 驗證簽名並解析 events
        events = parser.parse(body_str, x_line_signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    # 處理每一個 event
    for event in events:
        await event_handler.handle(event)

    return "OK"
