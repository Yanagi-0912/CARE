from linebot.v3.webhooks import MessageEvent
from app.services.line.message_service import line_message_service
from app.services.medical.medical_service import medical_service, session_store
from app.schemas import MedicalFacility
import logging

logger = logging.getLogger(__name__)


async def handle_text_message_async(event: MessageEvent):
    user_text = event.message.text
    reply_token = event.reply_token
    user_id = event.source.user_id if hasattr(event.source, "user_id") else None

    logger.info(f"Received text message event from user {user_id}")

    await line_message_service.process_and_reply(
        user_text=user_text,
        reply_token=reply_token,
        user_id=user_id,
    )


async def handle_location_message_async(event: MessageEvent):
    reply_token = event.reply_token
    user_id = event.source.user_id if hasattr(event.source, "user_id") else None
    lat: float = event.message.latitude
    lng: float = event.message.longitude

    logger.info(f"Received location from user {user_id}: ({lat}, {lng})")

    if session_store.get(user_id) != "WAITING_LOCATION":
        logger.warning(
            f"User {user_id} sent location but was not in WAITING_LOCATION state, ignoring."
        )
        return

    session_store.clear(user_id)

    facilities: list[MedicalFacility] = await medical_service.find_nearby_hospitals(
        lat, lng
    )

    if facilities:
        lines = [f"📍 為您找到附近 {len(facilities)} 間醫療院所：\n"]
        for i, f in enumerate(facilities, 1):
            dist = (
                f"（{f.distance_meters:.0f} 公尺）"
                if f.distance_meters is not None
                else ""
            )
            lines.append(f"{i}. {f.name}{dist}\n   {f.address}")
        reply_text = "\n".join(lines)
    else:
        reply_text = (
            "抱歉，您附近 1 公里內暫時找不到醫療院所資料。\n功能仍在建置中，敬請期待！"
        )

    await line_message_service._send_line_reply(reply_token, reply_text, user_id)
