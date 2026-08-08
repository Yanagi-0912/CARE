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


# 藥局「查到了、但荒謬地遠」的判定門檻，沿用搜尋階梯的第一級（5 公里）。
# 為什麼是這個數字：藥局是「順路領藥、臨時買藥」的生活機能，超過 5 公里已不可能
# 是使用者心中「附近的藥局」；而第一級距內找得到就代表該地區的收錄密度正常，
# 唯有必須擴大到第一級之外才找得到，才說明結果是資料缺口撐出來的、而非地理事實。
# 直接綁定 NEARBY_SEARCH_STEPS[0] 而不另外寫死 5000，是為了讓門檻與搜尋階梯
# 的定義保持同一個來源，日後調整階梯時不會出現兩套互相矛盾的「附近」。
PHARMACY_DATA_GAP_METERS = NEARBY_SEARCH_STEPS[0]


def _pharmacy_data_gap_note(result: NearbySearchResult) -> str | None:
    """
    查到藥局、但最近一家遠超出生活圈時，回傳「資料有限」的補充說明。

    為什麼需要：資料庫只收錄 116 家藥局，全台實際有數千家。實測台北車站查藥局
    會回傳 5 家、全部在 18 公里外，且因為湊滿了 5 筆而 satisfied=True，副標走
    「已擴大範圍找到 5 家」——使用者站在步行範圍內就有數十家藥局的地方，卻拿到
    一張看起來完全正常的卡片。既有的 location.type.pharmacy_none 只在 0 筆時
    觸發，涵蓋不到這個其實更常見的情境，因此另外補這一則說明。
    """
    match = result.facility_type_match
    if match is None or match.category != "藥局" or not result.facilities:
        return None
    nearest = min((f.distance_meters or 0) for f in result.facilities)
    if nearest <= PHARMACY_DATA_GAP_METERS:
        return None
    return t("location.type.pharmacy_data_gap").format(
        radius_km=str(math.ceil(nearest / 1000))
    )


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

    # 藥局收錄量遠低於實際家數，「查到了但很遠」時必須揭露這層資料缺口，
    # 否則卡片會讓使用者以為附近真的只有 18 公里外那幾家藥局。
    gap_note = _pharmacy_data_gap_note(result)
    if gap_note is not None:
        subtitle = f"{subtitle}\n{gap_note}"
    return subtitle


@tool
async def find_nearby_hospitals(
    lat: float, lng: float, open_now: bool = False, facility_type: str | None = None
) -> str:
    """
    當已取得用戶的 GPS 座標後，呼叫此工具搜尋附近的醫療院所。
    使用者分享位置後，該訊息會以「這是我的目前位置：lat=..., lng=...」的文字進入對話，
    此時必須從該文字取出 lat/lng 並呼叫本工具。
    只有在使用者明確表達「現在有開的／還在看診的／現在營業中」時才把 open_now 設為 true；
    單純問「附近有醫院嗎」不要設，否則會在午休與深夜篩掉大量其實稍後就開診的院所。

    facility_type 為選填的院所類型過濾，只有在使用者明確指出規模／型態時才傳，
    且一律填使用者的原始說法：講「大醫院」「大型醫院」「要住院」等 → 傳「大醫院」；
    講「診所」「小診所」→ 傳「診所」；講「藥局」「藥房」→ 傳「藥局」；
    講健保類別的正式名稱（「綜合醫院」「精神科醫院」「專科診所」「牙醫診所」等）
    → 原樣傳入，不要自行換算成「醫院」或「診所」。
    使用者只是泛稱「醫院」（例如單純說「我要去醫院」）時**不要傳** facility_type——
    口語中的「醫院」常常只是「醫療院所」的泛稱，若照字面套用類型過濾，會把 18,935 家
    診所全部排除在外，反而讓查詢結果更差。
    「牙醫」「中醫」屬於科別而非類型，遇到這兩個詞請改用
    find_nearby_facilities_by_department 並傳入 department，不要塞進 facility_type。
    """
    return await _search_nearby_facilities(
        lat, lng, open_now=open_now, facility_type=facility_type
    )


async def _search_nearby_facilities(
    lat: float, lng: float, open_now: bool = False, facility_type: str | None = None
) -> str:
    """
    「不分科別的鄰近院所搜尋」的實作本體，與 @tool 裝飾分離。

    抽出來是為了讓 find_nearby_facilities_by_department 在收到空白 department 時
    可以直接退回這條路徑（見該工具內的說明），而不必重寫一次查無結果、藥局專屬
    文案與標題覆寫這些分支——複製一份必然會逐漸走樣。
    """
    if _medical_service is None:
        return "醫療服務未初始化，請稍後再試。"

    logger.info(
        f"{LOGGER_HEADER_TEXT} 開始查詢附近醫療院所，"
        f"lat=%s, lng=%s, open_now=%s, facility_type=%r",
        lat,
        lng,
        open_now,
        facility_type,
    )
    result = await _medical_service.find_nearby_hospitals(
        lat, lng, open_now=open_now, facility_type=facility_type
    )

    # 類型看不懂時 service 層不查 DB（facilities 必為空），必須在「查無結果」判斷之前
    # 先攔下來，否則使用者會誤以為系統聽懂了他要的類型、只是附近真的沒有。
    if result.facility_type_unresolved:
        logger.info(
            f"{LOGGER_HEADER_TEXT} 無法解析院所類型，facility_type=%r", facility_type
        )
        return t("location.type.unknown").format(facility_type=facility_type)

    if not result.facilities:
        # 藥局資料庫收錄有限，查無結果的原因通常是「本系統沒收錄」而非「附近真的沒有」，
        # 用通用文案會誤導使用者，需要專屬說明。
        if (
            result.facility_type_match is not None
            and result.facility_type_match.category == "藥局"
        ):
            logger.info(f"{LOGGER_HEADER_TEXT} 查無附近藥局")
            return t("location.type.pharmacy_none").format(
                radius_km=_km(NEARBY_SEARCH_STEPS[-1])
            )
        logger.info(f"{LOGGER_HEADER_TEXT} 查無附近醫療院所")
        return t("location.no_facility").format(
            radius_km=_km(NEARBY_SEARCH_STEPS[-1])
        )

    logger.info(
        f"{LOGGER_HEADER_TEXT} 查詢完成，回傳筆數=%s, 涵蓋範圍=%s 公尺",
        len(result.facilities),
        result.reached_meters,
    )
    title_override = None
    if result.facility_type_match is not None:
        title_override = t("location.type.title").format(
            type=result.facility_type_match.category
        )
    return _to_flex_message_text(
        generate_facility_list_flex_message(
            result.facilities,
            title_override=title_override,
            subtitle_override=_build_range_subtitle(result),
        )
    )


@tool
async def find_nearby_facilities_by_department(
    lat: float,
    lng: float,
    department: str,
    open_now: bool = False,
    facility_type: str | None = None,
) -> str:
    """
    當使用者想找「特定科別」的醫療院所（例如腸胃科、牙科、耳鼻喉科、中醫、兒科）
    且已取得其 GPS 座標時，呼叫此工具。
    使用者分享位置後，該訊息會以「這是我的目前位置：lat=..., lng=...」的文字進入對話，
    此時必須從該文字取出 lat/lng，並把使用者稍早提到的科別一併傳入 department。
    department 請填使用者的原始說法（例如「腸胃科」），不需要自行換算成部定專科。
    只有在使用者明確表達「現在有開的／還在看診的」時才把 open_now 設為 true。
    若使用者只是要找一般醫院、沒有指定科別，請改用 find_nearby_hospitals。

    facility_type 為選填的院所類型過濾，可與 department 同時使用（例如使用者說
    「大醫院的腸胃科」→ department="腸胃科", facility_type="大醫院"）。傳入時機
    比照 find_nearby_hospitals：只有使用者明確講「大醫院」「大型醫院」「要住院」
    → 傳「大醫院」；講「診所」「小診所」→ 傳「診所」；講「藥局」「藥房」→ 傳「藥局」。
    使用者泛稱「醫院」時不要傳，理由同 find_nearby_hospitals——會誤刪絕大多數診所。
    """
    if _medical_service is None:
        return "醫療服務未初始化，請稍後再試。"

    # department 雖是必填，但 LLM function calling 對字串參數送空值是實測會發生的
    # 行為。此時回「我不確定「」對應到哪一個診療科別」等於讓整個找院所的流程斷在
    # 一個模型端的失誤上；改為退回不分科別的一般搜尋，使用者至少拿得到附近院所，
    # 而且 facility_type 若有帶仍然沿用。取捨：這會讓「模型漏填科別」變得比較不
    # 顯眼，但比起把錯誤丟回給使用者，給出可用結果才是正確的降級方向。
    if not (department or "").strip():
        logger.info(
            "[Tool:find_nearby_facilities_by_department] department 為空，"
            "退回不分科別的一般搜尋，facility_type=%r",
            facility_type,
        )
        return await _search_nearby_facilities(
            lat, lng, open_now=open_now, facility_type=facility_type
        )

    logger.info(
        "[Tool:find_nearby_facilities_by_department] 開始查詢，"
        "lat=%s, lng=%s, department=%r, facility_type=%r",
        lat,
        lng,
        department,
        facility_type,
    )
    result = await _medical_service.find_nearby_facilities_by_department(
        lat, lng, department, open_now=open_now, facility_type=facility_type
    )

    if result.match is None:
        logger.info(
            "[Tool:find_nearby_facilities_by_department] 無法解析科別，department=%r",
            department,
        )
        return t("location.department.unknown").format(department=department)

    # 已知限制（Task 2 揭露）：科別與類型同時解析失敗時，service 層只會回報科別失敗
    # （科別的 early return 先發生），不會走到這裡。此處只需處理「科別解析成功、
    # 類型解析失敗」的情形。
    if result.facility_type_unresolved:
        logger.info(
            "[Tool:find_nearby_facilities_by_department] 無法解析院所類型，"
            "facility_type=%r",
            facility_type,
        )
        return t("location.type.unknown").format(facility_type=facility_type)

    if not result.facilities:
        # 藥局資料庫收錄有限，查無結果多半是「本系統沒收錄」而非「附近真的沒有」，
        # 優先於通用的科別查無文案，避免使用者誤以為附近真的沒有藥局。
        if (
            result.facility_type_match is not None
            and result.facility_type_match.category == "藥局"
        ):
            logger.info(
                "[Tool:find_nearby_facilities_by_department] 查無符合科別的藥局，"
                "canonical=%r",
                result.match.canonical,
            )
            return t("location.type.pharmacy_none").format(
                radius_km=_km(NEARBY_SEARCH_STEPS[-1])
            )
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
    # 科別與類型同時存在時（例如「大醫院的腸胃科」），把類型併進科別標題裡
    # （「附近的腸胃科（醫院）」），而不是只挑一邊呈現——否則使用者會看不出
    # 系統其實同時套用了兩個條件，以為篩選範圍比實際寬。
    department_label = result.match.canonical
    if result.facility_type_match is not None:
        department_label = f"{department_label}（{result.facility_type_match.category}）"
    return _to_flex_message_text(
        generate_facility_list_flex_message(
            result.facilities,
            title_override=t("location.department.title").format(
                department=department_label
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
