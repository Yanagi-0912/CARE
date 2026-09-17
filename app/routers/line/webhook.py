"""LINE webhook 入口。

流程：驗簽 → 解析 → 每個事件各開一個背景 task → 立刻回 200。

以前是處理完所有事件才回 200，而且一批事件逐一串行。LINE 要求 webhook 在
1 秒內回應，否則判定失敗並重送同一批事件；agent 一輪動輒 10–60 秒，等於每一則
稍微久一點的訊息都會被重送、被答兩次。改成先回 200，處理留在背景；同一批的
多個事件（例如使用者連傳兩張圖）併行而非排隊。同一位使用者的事件順序由
dispatcher 的 per-user lock 保證，這裡不用管。
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.webhook import WebhookParser

from app.core.config import settings
from app.db.redis import RedisManager
from app.dependencies import get_line_event_handler
from app.services.line_messaging import LineEventHandler
from app.services.line_messaging.event_dedup import LineEventDedup, event_identity

logger = logging.getLogger(__name__)

router = APIRouter()

# 空的 channel secret 會讓 WebhookParser 用空 key 算 HMAC：任何人都偽造得出
# 「合法」簽章，webhook 等於不設防；而 None 會在 SDK 內以 AttributeError 炸出
# 一行看不懂的錯。兩種都在 import 時就擋下，錯誤訊息直接講缺什麼。
if not (settings.LINE_CHANNEL_SECRET or "").strip():
    raise RuntimeError(
        "LINE_CHANNEL_SECRET 未設定或為空：webhook 無法驗證簽章，拒絕啟動。"
        "請在 .env 或部署的 secret 補上 LINE Developers Console 的 Channel secret。"
    )

# 建立 LINE Webhook 解析器
parser = WebhookParser(settings.LINE_CHANNEL_SECRET)

# 背景 task 要被持有參考直到完成，否則可能在跑完之前就被 GC 回收
# （asyncio 文件明講 create_task 只持弱參考）。
_background_tasks: set[asyncio.Task] = set()


def _get_redis_client():
    # REDIS_URL 沒設（本機開發）時 get_client 會 raise，交給 dedup 退回本行程記憶。
    return RedisManager.get_client()


_event_dedup = LineEventDedup(
    _get_redis_client,
    ttl_seconds=settings.LINE_WEBHOOK_EVENT_TTL_SECONDS,
)


async def _handle_event_in_background(
    event_handler: LineEventHandler, event, *, dedup: LineEventDedup
) -> None:
    """一個事件的完整處理，例外一律留在 task 內。

    逸散的例外只會變成 "Task exception was never retrieved"，而且回 200 的
    請求早就結束了，沒有人會收到它。
    """
    event_id, is_redelivery = event_identity(event)
    try:
        if not await dedup.claim(event_id):
            logger.info(
                "LINE 事件已處理過，略過重送 event_id=%s redelivery=%s",
                event_id,
                is_redelivery,
            )
            return
        if is_redelivery:
            logger.info("LINE 重送的事件第一次被認領，照常處理 event_id=%s", event_id)
        await event_handler.handle(event)
    except Exception:
        logger.exception(
            "LINE 事件背景處理失敗 event_id=%s type=%s", event_id, type(event).__name__
        )


def schedule_events(
    event_handler: LineEventHandler, events, *, dedup: LineEventDedup | None = None
) -> list[asyncio.Task]:
    """把一批事件各自排成背景 task；回傳 task 清單給測試等待用。"""
    tasks = []
    for event in events:
        task = asyncio.create_task(
            _handle_event_in_background(
                event_handler, event, dedup=dedup or _event_dedup
            )
        )
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
        tasks.append(task)
    return tasks


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

    schedule_events(event_handler, events)
    return "OK"
