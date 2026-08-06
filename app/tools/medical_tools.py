import json
import logging
from typing import Any

from langchain_core.tools import tool
from app.i18n.messages import t
from app.services.medical.medical_service import (
    MedicalService,
    NO_NAMED_FACILITY_MESSAGE,
)
from resources.flex_messages.medical_messages.facility_brief_flex_message import (
    generate_facility_list_flex_message,
)
from resources.flex_messages.medical_messages.facility_detail_flex_message import (
    generate_facility_detail_flex_message,
)
from app.core.request_context import get_line_user_id
from app.repositories.user_location_repository import UserLocationRepository
_medical_service: MedicalService | None = None
logger = logging.getLogger(__name__)

LOGGER_HEADER_TEXT = "[Tool:find_nearby_hospitals]"


def _to_flex_message_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def configure_medical_tools(medical_service: MedicalService) -> None:
    """DI 初始化時呼叫，注入 MedicalService 實例。"""
    global _medical_service
    _medical_service = medical_service


@tool
async def find_nearby_hospitals(lat: float, lng: float) -> str:
    """
    當已取得用戶的 GPS 座標後，呼叫此工具搜尋附近的醫療院所。
    使用者分享位置後，該訊息會以「這是我的目前位置：lat=..., lng=...」的文字進入對話，
    此時必須從該文字取出 lat/lng 並呼叫本工具。
    """
    if _medical_service is None:
        return "醫療服務未初始化，請稍後再試。"

    logger.info(
        f"{LOGGER_HEADER_TEXT} 開始查詢附近醫療院所，lat=%s, lng=%s",
        lat,
        lng,
    )
    facilities = await _medical_service.find_nearby_hospitals(lat, lng)
    if not facilities:
        logger.info(f"{LOGGER_HEADER_TEXT} 查無附近醫療院所")
        return t("location.no_facility")

    logger.info(
        f"{LOGGER_HEADER_TEXT} 查詢完成，回傳筆數=%s",
        len(facilities),
    )
    return _to_flex_message_text(generate_facility_list_flex_message(facilities))


@tool
async def lookup_medical_facility(
    keyword: str, lat: float | None = None, lng: float | None = None
) -> str:
    """
    當使用者詢問特定醫療院所、醫院、診所或藥局的電話、營業時間、診療科別、
    地址或地圖連結時，呼叫此工具用院所名稱關鍵字查詢資料庫。
    若已知使用者座標可傳入 lat/lng 以便多筆候選依距離排序。
    """
    medical_facility_limit = 3

    if _medical_service is None:
        return "醫療服務未初始化，請稍後再試。"
    # 補齊 fallback 邏輯：若 agent 呼叫時未傳 lat/lng，嘗試從 UserLocationRepository 讀取
    if lat is None or lng is None:
        user_id = get_line_user_id()
        if user_id:
            cached_location = await UserLocationRepository.get_last_location(user_id)
            if cached_location:
                lat, lng = cached_location

    logger.info(
        f"{LOGGER_HEADER_TEXT} 開始查詢醫療院所，keyword=%r, lat=%s, lng=%s",
        keyword,
        lat,
        lng,
    )
    facilities, total_count = await _medical_service.find_facility_by_name(
        keyword=keyword,
        lat=lat,
        lng=lng,
    )
    if not facilities:
        logger.info(f"{LOGGER_HEADER_TEXT} 查無符合院所，keyword=%r", keyword)
        return NO_NAMED_FACILITY_MESSAGE
    if len(facilities) == 1:
        logger.info(f"{LOGGER_HEADER_TEXT} 僅找到 1 筆院所，直接回傳詳情")
        return _to_flex_message_text(
            generate_facility_detail_flex_message(facilities[0])
        )

    logger.info(
        f"{LOGGER_HEADER_TEXT} 找到多筆候選，總數=%s，回傳前 %s 筆",
        total_count,
        min(len(facilities), medical_facility_limit),
    )
    return _to_flex_message_text(
        generate_facility_list_flex_message(
            facilities[:medical_facility_limit], total_count=total_count
        )
    )


@tool
async def request_location_quick_reply() -> str:
    """
    當使用者想要尋找、前往、或詢問醫療院所/醫院/診所/藥局的位置，
    且我們尚未取得其經緯度座標時，呼叫此工具以引導使用者傳送其當前位置。
    """
    return t("location.share_prompt")
