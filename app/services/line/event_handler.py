from pathlib import Path
from typing import Optional, cast
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent,
    LocationMessageContent,
    ImageMessageContent,
    VideoMessageContent,
    AudioMessageContent,
    FileMessageContent,
)
from app.services.line.message_service import line_message_service
from app.services.medical.medical_service import medical_service, session_store
from app.services.media.mutimedia_processor import media_processor_service
from app.schemas import MedicalFacility
import logging

logger = logging.getLogger(__name__)

IMAGE_FILE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp", ".svg"}
VIDEO_FILE_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
AUDIO_FILE_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".aac"}


def _get_reply_token(event: MessageEvent) -> Optional[str]:
    reply_token = event.reply_token
    if not reply_token:
        logger.warning("Missing reply_token in LINE event; skip replying")
        return None
    return reply_token

def _get_user_id(event: MessageEvent) -> Optional[str]:
    return getattr(event.source, "user_id", None)


def _infer_media_type_from_file_name(file_name: str) -> str:
    extension = Path(file_name).suffix.lower()
    if extension in IMAGE_FILE_EXTENSIONS:
        return "image"
    if extension in VIDEO_FILE_EXTENSIONS:
        return "video"
    if extension in AUDIO_FILE_EXTENSIONS:
        return "audio"
    return "file"

async def _process_media_and_reply(
    *,
    media_message_id: str,
    user_media_type: str,
    reply_token: str,
    source_file_name: Optional[str] = None,
    user_id: Optional[str],
) -> None:
    media_content = await media_processor_service.process_media(
        media_message_id=media_message_id,
        user_media_type=user_media_type,
        source_file_name=source_file_name,
        user_id=user_id,
    )

    await line_message_service.process_and_reply(
        user_text=f"以下為用戶傳送的{user_media_type}媒體內容：\n{media_content}",
        reply_token=reply_token,
        user_id=user_id,
    )


async def handle_text_message_async(event: MessageEvent):
    message = cast(TextMessageContent, event.message)
    user_text = message.text
    reply_token = _get_reply_token(event)
    if reply_token is None:
        return
    user_id = _get_user_id(event)

    logger.info(f"Received text message event from user {user_id}")

    await line_message_service.process_and_reply(
        user_text=user_text,
        reply_token=reply_token,
        user_id=user_id,
    )

async def handle_location_message_async(event: MessageEvent):
    reply_token = _get_reply_token(event)
    if reply_token is None:
        return
    user_id = _get_user_id(event)
    if user_id is None:
        logger.warning("Missing user_id in location event; skip handling")
        return

    message = cast(LocationMessageContent, event.message)
    lat: float = message.latitude
    lng: float = message.longitude

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


async def handle_image_message_async(event: MessageEvent):
    reply_token = _get_reply_token(event)
    if reply_token is None:
        return
    user_id = _get_user_id(event)
    message = cast(ImageMessageContent, event.message)

    logger.info(f"Received image message event from user {user_id}")

    await _process_media_and_reply(
        media_message_id=message.id,
        user_media_type=message.type,
        reply_token=reply_token,
        user_id=user_id,
    )


async def handle_video_message_async(event: MessageEvent):
    reply_token = _get_reply_token(event)
    if reply_token is None:
        return
    user_id = _get_user_id(event)
    message = cast(VideoMessageContent, event.message)

    logger.info(f"Received video message event from user {user_id}")

    await _process_media_and_reply(
        media_message_id=message.id,
        user_media_type=message.type,
        reply_token=reply_token,
        user_id=user_id,
    )

async def handle_audio_message_async(event: MessageEvent):
    reply_token = _get_reply_token(event)
    if reply_token is None:
        return
    user_id = _get_user_id(event)
    message = cast(AudioMessageContent, event.message)

    logger.info(f"Received audio message event from user {user_id}")

    await _process_media_and_reply(
        media_message_id=message.id,
        user_media_type=message.type,
        reply_token=reply_token,
        user_id=user_id,
    )

async def handle_file_message_async(event: MessageEvent):
    reply_token = _get_reply_token(event)
    if reply_token is None:
        return
    user_id = _get_user_id(event)
    message = cast(FileMessageContent, event.message)
    inferred_media_type = _infer_media_type_from_file_name(message.file_name)

    logger.info(f"Received file message event from user {user_id}: {message.file_name}")

    await _process_media_and_reply(
        media_message_id=message.id,
        user_media_type=inferred_media_type,
        reply_token=reply_token,
        source_file_name=message.file_name,
        user_id=user_id,
    )