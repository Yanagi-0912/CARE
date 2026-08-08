import json
import logging
import math
from typing import Any

from langchain_core.tools import tool
from app.i18n.messages import t
from app.services.medical.medical_service import (
    NEARBY_SEARCH_STEPS,
    DepartmentSearchResult,
    MedicalService,
    NearbySearchResult,
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


def _km(meters: float) -> str:
    """公尺轉公里字串，整數不留小數點（5000 -> "5"，2500 -> "2.5"）。"""
    km = meters / 1000
    return str(int(km)) if km == int(km) else f"{km:g}"


def _furthest_km(result: NearbySearchResult) -> str:
    """
    結果中最遠院所的距離（無條件進位到整數公里）。

    擴大範圍時報這個數字而不是階梯級距：階梯跳到 50 公里不代表使用者真的要跑
    50 公里，實際最遠可能只有 27 公里，講級距會讓人高估交通成本。
    """
    furthest = max((f.distance_meters or 0) for f in result.facilities)
    return str(math.ceil(furthest / 1000))


def _build_range_subtitle(result: NearbySearchResult) -> str:
    """依「搜到多遠、湊不湊得滿」組出副標，讓使用者知道結果的實際涵蓋範圍。"""
    count = len(result.facilities)

    # 要求營業中卻一家都沒開：這件事比搜尋範圍重要，優先講。
    if result.open_now_fallback:
        subtitle = t("location.open_now.none")
    elif result.open_now_requested:
        subtitle = t("location.open_now.found").format(count=count)
    elif not result.satisfied:
        # 湊不滿時重點是「我已經找到這麼遠了」，所以報搜尋上限而非最遠院所距離。
        subtitle = t("location.nearby.partial").format(
            radius_km=_km(result.reached_meters), count=count
        )
    elif result.expanded:
        subtitle = t("location.nearby.expanded").format(
            radius_km=_furthest_km(result), count=count
        )
    else:
        subtitle = t("location.nearby.found_within").format(
            radius_km=_km(NEARBY_SEARCH_STEPS[0]), count=count
        )

    # 科別搜尋時，若使用者的說法與部定專科不同，必須誠實說明這層對應。
    match = getattr(result, "match", None)
    if match is not None and match.is_alias:
        note = t("location.department.alias_note").format(
            requested=match.requested, canonical=match.canonical
        )
        subtitle = f"{subtitle}\n{note}"
    return subtitle


@tool
async def find_nearby_hospitals(
    lat: float, lng: float, open_now: bool = False
) -> str:
    """
    當已取得用戶的 GPS 座標後，呼叫此工具搜尋附近的醫療院所。
    使用者分享位置後，該訊息會以「這是我的目前位置：lat=..., lng=...」的文字進入對話，
    此時必須從該文字取出 lat/lng 並呼叫本工具。
    只有在使用者明確表達「現在有開的／還在看診的／現在營業中」時才把 open_now 設為 true；
    單純問「附近有醫院嗎」不要設，否則會在午休與深夜篩掉大量其實稍後就開診的院所。
    """
    if _medical_service is None:
        return "醫療服務未初始化，請稍後再試。"

    logger.info(
        f"{LOGGER_HEADER_TEXT} 開始查詢附近醫療院所，lat=%s, lng=%s, open_now=%s",
        lat,
        lng,
        open_now,
    )
    result = await _medical_service.find_nearby_hospitals(lat, lng, open_now=open_now)
    if not result.facilities:
        logger.info(f"{LOGGER_HEADER_TEXT} 查無附近醫療院所")
        return t("location.no_facility").format(
            radius_km=_km(NEARBY_SEARCH_STEPS[-1])
        )

    logger.info(
        f"{LOGGER_HEADER_TEXT} 查詢完成，回傳筆數=%s, 涵蓋範圍=%s 公尺",
        len(result.facilities),
        result.reached_meters,
    )
    return _to_flex_message_text(
        generate_facility_list_flex_message(
            result.facilities,
            subtitle_override=_build_range_subtitle(result),
        )
    )


@tool
async def find_nearby_facilities_by_department(
    lat: float, lng: float, department: str, open_now: bool = False
) -> str:
    """
    當使用者想找「特定科別」的醫療院所（例如腸胃科、牙科、耳鼻喉科、中醫、兒科）
    且已取得其 GPS 座標時，呼叫此工具。
    使用者分享位置後，該訊息會以「這是我的目前位置：lat=..., lng=...」的文字進入對話，
    此時必須從該文字取出 lat/lng，並把使用者稍早提到的科別一併傳入 department。
    department 請填使用者的原始說法（例如「腸胃科」），不需要自行換算成部定專科。
    只有在使用者明確表達「現在有開的／還在看診的」時才把 open_now 設為 true。
    若使用者只是要找一般醫院、沒有指定科別，請改用 find_nearby_hospitals。
    """
    if _medical_service is None:
        return "醫療服務未初始化，請稍後再試。"

    logger.info(
        "[Tool:find_nearby_facilities_by_department] 開始查詢，"
        "lat=%s, lng=%s, department=%r",
        lat,
        lng,
        department,
    )
    result = await _medical_service.find_nearby_facilities_by_department(
        lat, lng, department, open_now=open_now
    )

    if result.match is None:
        logger.info(
            "[Tool:find_nearby_facilities_by_department] 無法解析科別，department=%r",
            department,
        )
        return t("location.department.unknown").format(department=department)

    if not result.facilities:
        logger.info(
            "[Tool:find_nearby_facilities_by_department] 查無院所，canonical=%r",
            result.match.canonical,
        )
        return t("location.department.none").format(
            department=result.match.requested,
            radius_km=_km(NEARBY_SEARCH_STEPS[-1]),
        )

    logger.info(
        "[Tool:find_nearby_facilities_by_department] 完成，回傳=%s 筆，"
        "涵蓋=%s 公尺，湊滿=%s",
        len(result.facilities),
        result.reached_meters,
        result.satisfied,
    )
    return _to_flex_message_text(
        generate_facility_list_flex_message(
            result.facilities,
            title_override=t("location.department.title").format(
                department=result.match.canonical
            ),
            subtitle_override=_build_range_subtitle(result),
        )
    )


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
