"""
LIFF 的醫療院所查詢 API。

設計原則：**回結構化事實，不回組好的句子。**

LINE 那一側由 `medical_tools.py` 在伺服器端把「已擴大到 20 公里找到 5 家」這類
副標組成中文字串再送出，那是對的——LINE 的語言來自對話情境，`get_request_language()`
在 webhook 流程裡有設。但 API 路由沒有任何語言中介層，contextvar 一律是預設的
zh-TW，若在這裡組字串，LIFF 上選了日文的使用者會拿到中文副標。LIFF 自己有一整套
react-i18next（六種語言），因此本模組只回傳「搜到多遠、湊不湊得滿、科別對應到誰、
藥局最近一家有多遠」這些事實，句子交給前端組。

反過來說，**判定規則不得在前端重寫**：階梯放寬、營業狀態分級、藥局資料缺口門檻
全部在 service 層算完才回傳，前端只做字串渲染。這條界線一旦跑掉，兩個通道就會
對同一筆結果講不一樣的話。
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.dependencies import CurrentUser, get_current_user, get_medical_service
from app.schemas import MedicalFacility
from app.services.medical.business_hours import (
    has_emergency_department,
    resolve_clinic_hours,
)
from app.services.medical.medical_service import (
    NEARBY_SEARCH_STEPS,
    MedicalService,
    NearbySearchResult,
)
from app.services.medical.search_summary import pharmacy_data_gap_meters

logger = logging.getLogger(__name__)

router = APIRouter()

LOGGER_HEADER_TEXT = "[Router:Medical]"

SERVICE_UNAVAILABLE_DETAIL = "醫療院所查詢暫時不可用，請稍後再試"


class NextOpenPayload(BaseModel):
    """下一次開診的時間點。星期以 key 回傳，由前端翻成當地語言。"""

    weekday_key: str = Field(description="星期英文小寫，monday ~ sunday")
    time_text: str = Field(description="開診時間，例如 08:00")
    is_today: bool = Field(description="是否為今日稍後開診")


class BusinessStatusPayload(BaseModel):
    """
    院所當下的營業狀態。

    `status` 是列舉值而非文字：狀態分級（營業中／今日尚未開診／午休中／今日已結束／
    今日休診／請電洽／無資料）牽涉跨日跨週的下次開診計算與兩個資料陷阱（急診院所的
    clinicTime 記的是門診時間、notes 的節慶休診綁定特定日期），這些判斷留在後端；
    前端只把列舉值對到顏色與譯文。

    `has_emergency` 與 `status` 並存而非擠成同一格：設有急診是「能力標示」，不是
    營業狀態，一家有急診的醫院門診仍可能正在午休，使用者需要同時看到兩件事。
    """

    status: str = Field(description="BusinessStatus 列舉值，例如 open / break / emergency")
    next_open: NextOpenPayload | None = Field(
        None, description="下次開診時間；營業中或七天內查無排班時為 null"
    )
    note: str | None = Field(None, description="院所 notes 原文，格式不規則，原樣顯示")
    has_emergency: bool = Field(description="是否設有急診醫學科")


class FacilityPayload(MedicalFacility):
    """
    院所資料，附上算好的營業狀態。

    直接繼承 MedicalFacility 而不另立精簡模型：clinic_time / departments / notes
    本來就在 `find_near` 的查詢結果裡，欄位挑掉再讓前端另外打一支詳情 API，
    只是把已經付過成本的資料丟掉。
    """

    business_status: BusinessStatusPayload = Field(description="營業狀態與下次開診時間")


class DepartmentMatchPayload(BaseModel):
    """科別解析結果。`is_alias` 為真時前端必須揭露這層對應，否則使用者會以為系統
    真的有「腸胃科」這個分類，而非把它併進了「內科」。"""

    requested: str = Field(description="使用者原本的說法，例如「腸胃科」")
    canonical: str = Field(description="資料庫實際存在的部定專科，例如「內科」")
    is_alias: bool = Field(description="兩者不同時為 true，回覆需說明對應關係")


class FacilityTypeMatchPayload(BaseModel):
    """院所類型解析結果，語意同 DepartmentMatchPayload。"""

    requested: str = Field(description="使用者原本的說法，例如「大醫院」")
    category: str = Field(description="分類：醫院／診所／藥局")
    is_alias: bool = Field(description="兩者不同時為 true")


class NearbyHospitalsResponse(BaseModel):
    facilities: list[FacilityPayload] = Field(description="附近醫療院所列表，由近到遠")
    count: int = Field(description="回傳筆數")

    reached_meters: int = Field(
        description=(
            "實際涵蓋到的搜尋範圍（公尺），對應 NEARBY_SEARCH_STEPS 的其中一級。"
            "前端據此告知使用者「已擴大到幾公里」。"
        )
    )
    satisfied: bool = Field(
        description="是否在上限內湊滿 limit 筆。false 代表這是「有找到的部分」"
    )
    expanded: bool = Field(description="是否曾放寬到第一級（5 公里）以外")
    furthest_meters: float | None = Field(
        None,
        description=(
            "結果中最遠院所的距離。前端報「已擴大範圍」時應顯示這個數字而非 "
            "reached_meters：階梯跳到 50 公里不代表使用者真的要跑 50 公里，"
            "實際最遠可能只有 27 公里，講級距會讓人高估交通成本。"
        ),
    )
    max_meters: int = Field(
        description="本次搜尋的硬上限（公尺），供前端組「範圍內查無資料」文案"
    )

    open_now_requested: bool = Field(description="本次是否要求只看營業中")
    open_now_fallback: bool = Field(
        description=(
            "要求營業中但一家都沒開，已退回未過濾結果。"
            "true 時前端須改講「目前均未開診，以下為下次開診時間」"
        )
    )

    department: DepartmentMatchPayload | None = Field(
        None, description="科別解析結果；未指定科別時為 null"
    )
    facility_type: FacilityTypeMatchPayload | None = Field(
        None, description="類型解析結果；未指定類型時為 null"
    )
    unresolved_department: str | None = Field(
        None,
        description=(
            "指定了科別但解析不出來時，原樣回傳使用者的說法（此時 facilities 必為空）。"
            "與「附近真的沒有」分開，前端才不會讓使用者誤以為系統聽懂了"
        ),
    )
    unresolved_facility_type: str | None = Field(
        None, description="指定了類型但解析不出來時，原樣回傳使用者的說法"
    )

    pharmacy_data_gap_meters: float | None = Field(
        None,
        description=(
            "查到藥局、但最近一家已遠超生活圈時，回傳最近一家的距離。"
            "資料庫只收錄 116 家藥局，此時結果看起來正常其實是資料缺口，必須揭露"
        ),
    )


class FacilitySearchResponse(BaseModel):
    """依名稱查詢的結果。"""

    facilities: list[FacilityPayload] = Field(description="符合的院所")
    count: int = Field(description="回傳筆數")
    total_count: int = Field(description="符合條件的總數，可能大於回傳筆數")


def _to_business_status(facility: MedicalFacility) -> BusinessStatusPayload:
    """把 business_hours 的判斷結果攤平成可序列化的 payload。"""
    hours = resolve_clinic_hours(facility)
    next_open = (
        NextOpenPayload(
            weekday_key=hours.next_open.weekday_key,
            time_text=hours.next_open.time_text,
            is_today=hours.next_open.is_today,
        )
        if hours.next_open is not None
        else None
    )
    return BusinessStatusPayload(
        status=hours.status.value,
        next_open=next_open,
        note=hours.note,
        has_emergency=has_emergency_department(facility),
    )


def _to_payload(facility: MedicalFacility) -> FacilityPayload:
    return FacilityPayload(
        **facility.model_dump(),
        business_status=_to_business_status(facility),
    )


def _department_payload(result: NearbySearchResult) -> DepartmentMatchPayload | None:
    match = getattr(result, "match", None)
    if match is None:
        return None
    return DepartmentMatchPayload(
        requested=match.requested,
        canonical=match.canonical,
        is_alias=match.is_alias,
    )


def _facility_type_payload(
    result: NearbySearchResult,
) -> FacilityTypeMatchPayload | None:
    match = result.facility_type_match
    if match is None:
        return None
    return FacilityTypeMatchPayload(
        requested=match.requested,
        category=match.category,
        is_alias=match.is_alias,
    )


@router.get(
    "/nearby",
    response_model=NearbyHospitalsResponse,
    summary="依經緯度搜尋附近醫療院所",
    description=(
        "LIFF 透過瀏覽器 Geolocation 取得座標後呼叫此 API。"
        "可依科別、院所類型、是否營業中過濾，過濾條件一律填使用者的原始說法，"
        "由後端負責對應到資料庫實際存在的值。"
    ),
)
async def get_nearby_hospitals(
    lat: Annotated[float, Query(ge=-90, le=90, description="緯度")],
    lng: Annotated[float, Query(ge=-180, le=180, description="經度")],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[MedicalService, Depends(get_medical_service)],
    limit: Annotated[int, Query(ge=1, le=20, description="最多回傳筆數")] = 5,
    radius_meters: Annotated[
        int | None,
        Query(
            ge=100,
            le=50_000,
            description=(
                "硬性半徑上限（公尺）。省略時採用與 LINE 相同的階梯放寬"
                "（5→10→20→50 公里），直到湊滿 limit 筆為止。"
            ),
        ),
    ] = None,
    open_now: Annotated[
        bool,
        Query(
            description=(
                "只看現在營業中的院所。設有急診者一律保留——clinicTime 記的是門診時間，"
                "深夜依門診時間判斷會把急診院所全部濾掉"
            )
        ),
    ] = False,
    department: Annotated[
        str | None,
        Query(
            max_length=40,
            description="科別，填使用者的原始說法即可（例如「腸胃科」，後端會對到「內科」）",
        ),
    ] = None,
    facility_type: Annotated[
        str | None,
        Query(
            max_length=40,
            description="院所類型，填原始說法（例如「大醫院」「藥局」）",
        ),
    ] = None,
) -> NearbyHospitalsResponse:
    """
    搜尋附近院所。

    **為什麼不再截掉超出 radius_meters 的結果**：先前這裡固定用 5,000 公尺過濾
    service 回傳的結果，等於把 service 層「湊不滿就逐級放寬到 50 公里」的設計
    整個抵銷掉。實務後果是同一個座標在 LINE 會擴大範圍拿到 5 家，在 LIFF 卻顯示
    「附近 5 公里內暫無資料」——醫療資源密度低的地區，LIFF 這條路等於是死的。
    現在 radius_meters 改為選填：省略時行為與 LINE 一致（回傳 reached_meters
    讓前端說明實際搜到多遠），只有呼叫端明確要求硬上限時才截斷。
    """
    max_meters = radius_meters or NEARBY_SEARCH_STEPS[-1]

    logger.info(
        f"{LOGGER_HEADER_TEXT} /nearby lat=%s, lng=%s, limit=%s, radius=%s, "
        "open_now=%s, department=%r, facility_type=%r",
        lat,
        lng,
        limit,
        radius_meters,
        open_now,
        department,
        facility_type,
    )

    try:
        # 有帶科別就走科別搜尋——service 層的兩支方法共用同一套階梯與類型過濾，
        # 差別只在多一層科別解析，因此這裡只需要選對入口，不必自己組查詢條件。
        if (department or "").strip():
            result: NearbySearchResult = (
                await service.find_nearby_facilities_by_department(
                    lat=lat,
                    lng=lng,
                    department=department,
                    target_count=limit,
                    open_now=open_now,
                    facility_type=facility_type,
                )
            )
        else:
            result = await service.find_nearby_hospitals(
                lat=lat,
                lng=lng,
                target_count=limit,
                open_now=open_now,
                facility_type=facility_type,
            )
    except Exception as exc:
        logger.exception(f"{LOGGER_HEADER_TEXT} /nearby 查詢失敗")
        raise HTTPException(
            status_code=503,
            detail=SERVICE_UNAVAILABLE_DETAIL,
        ) from exc

    # 科別／類型解析失敗一律回 200 而非 4xx：這不是呼叫端把 API 用錯了，而是
    # 使用者講了一個系統對不上的詞，屬於正常的查詢結果之一。用錯誤碼會逼前端把
    # 它塞進錯誤橫幅，跟「Atlas 掛了」混為一談；回 200 加上 unresolved_* 欄位，
    # 前端才能在同一個結果區裡好好說明「我不確定你說的是哪一科」。
    is_department_search = bool((department or "").strip())
    unresolved_department = (
        department
        if is_department_search and getattr(result, "match", None) is None
        else None
    )
    unresolved_facility_type = facility_type if result.facility_type_unresolved else None

    facilities = list(result.facilities)
    if radius_meters is not None:
        # 呼叫端明確要求硬上限時才截斷。$geoNear 已由近到遠排序，
        # 因此截掉超距的等同取半徑內最近的 limit 筆。
        # 距離未知不等於超出半徑——沒有 distance_meters 就無從判斷，不該被濾掉。
        facilities = [
            f
            for f in facilities
            if f.distance_meters is None or f.distance_meters <= radius_meters
        ]

    distances = [f.distance_meters for f in facilities if f.distance_meters is not None]

    logger.info(
        f"{LOGGER_HEADER_TEXT} /nearby 完成，回傳=%s 筆，涵蓋=%s 公尺，湊滿=%s",
        len(facilities),
        result.reached_meters,
        result.satisfied,
    )

    return NearbyHospitalsResponse(
        facilities=[_to_payload(f) for f in facilities],
        count=len(facilities),
        reached_meters=result.reached_meters,
        satisfied=result.satisfied,
        expanded=result.expanded,
        furthest_meters=max(distances) if distances else None,
        max_meters=max_meters,
        open_now_requested=result.open_now_requested,
        open_now_fallback=result.open_now_fallback,
        department=_department_payload(result),
        facility_type=_facility_type_payload(result),
        unresolved_department=unresolved_department,
        unresolved_facility_type=unresolved_facility_type,
        pharmacy_data_gap_meters=pharmacy_data_gap_meters(result),
    )


@router.get(
    "/facilities",
    response_model=FacilitySearchResponse,
    summary="依名稱關鍵字查詢醫療院所",
    description=(
        "對應 LINE 的 lookup_medical_facility。傳入 lat/lng 時會優先在生活圈"
        "（50 公里）內比對，查無再放寬為全國——同名院所（仁愛、中山、博愛…）"
        "全台有數十家，不限縮會被外縣市同名院所稀釋。"
    ),
)
async def search_facilities(
    keyword: Annotated[
        str, Query(min_length=1, max_length=60, description="院所名稱關鍵字")
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[MedicalService, Depends(get_medical_service)],
    lat: Annotated[float | None, Query(ge=-90, le=90, description="緯度，選填")] = None,
    lng: Annotated[
        float | None, Query(ge=-180, le=180, description="經度，選填")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=20, description="最多回傳筆數")] = 10,
) -> FacilitySearchResponse:
    """
    名稱查詢。

    lat/lng 只有兩個都給才算數：service 層以 `lat is not None and lng is not None`
    決定要不要走 geo 查詢，這裡先擋掉只給一半的情況，免得前端在定位尚未回來時
    傳了半組座標，卻拿到與「完全沒給座標」不同的排序（後者是相似度排序）。
    """
    has_coords = lat is not None and lng is not None

    logger.info(
        f"{LOGGER_HEADER_TEXT} /facilities keyword=%r, has_coords=%s, limit=%s",
        keyword,
        has_coords,
        limit,
    )

    try:
        facilities, total_count = await service.find_facility_by_name(
            keyword=keyword,
            lat=lat if has_coords else None,
            lng=lng if has_coords else None,
            limit=limit,
        )
    except Exception as exc:
        logger.exception(f"{LOGGER_HEADER_TEXT} /facilities 查詢失敗")
        raise HTTPException(
            status_code=503,
            detail=SERVICE_UNAVAILABLE_DETAIL,
        ) from exc

    logger.info(
        f"{LOGGER_HEADER_TEXT} /facilities 完成，回傳=%s 筆，總數=%s",
        len(facilities),
        total_count,
    )

    return FacilitySearchResponse(
        facilities=[_to_payload(f) for f in facilities],
        count=len(facilities),
        total_count=total_count,
    )
