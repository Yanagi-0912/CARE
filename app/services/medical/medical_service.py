import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.repositories.medical_facility_repository import MedicalFacilityRepository
from app.schemas import MedicalFacility
from app.i18n.messages import t
from app.services.medical.business_hours import resolve_business_hours
from app.services.medical.department_matcher import (
    DepartmentMatch,
    build_department_query,
    resolve_department,
)
from app.services.medical.medical_facility_matcher import (
    build_facility_query,
    similarity_rank,
)

logger = logging.getLogger(__name__)

NO_FACILITY_MESSAGE = t("location.no_facility")
NO_NAMED_FACILITY_MESSAGE = "查無此院所資料。請提供更明確的院所名稱或地區關鍵字，我再幫您查詢。"

LOGGER_HEADER_TEXT = "[Services:MedicalService]"

# 找不到足夠院所時逐級放寬的搜尋範圍（公尺）。最後一級同時是硬上限：
# 超過 50 公里的院所對「現在要去看診」這個情境已經沒有實用價值。
# 不分科別與依科別搜尋共用同一套分級，否則會出現「問腸胃科找得到、
# 問醫院反而查無資料」這種前後矛盾的結果。
NEARBY_SEARCH_STEPS: tuple[int, ...] = (5_000, 10_000, 20_000, 50_000)

# 預設要湊滿的院所筆數，與 LINE Flex carousel 的可用張數一致。
DEFAULT_TARGET_COUNT = 5

# 依名稱查詢時優先採用的生活圈半徑。同名院所（仁愛、中山、博愛…）全台有數十家，
# 沒有這個限制時，排序雖仍由近到遠，但候選清單會被外縣市同名院所稀釋。
# 此半徑內查無結果時會自動放寬為全國搜尋，見 find_facility_by_name。
NAME_SEARCH_RADIUS_METERS = 50_000

# 篩選「現在營業中」時多取回幾倍候選。營業判斷必須在應用層做（clinicTime 是嵌套結構），
# 所以得先多拿一些才有東西可篩。平日上午約 82% 營業、午休僅 11.5%，
# 4 倍在多數時段足夠，深夜則會走 open_now_fallback。
OPEN_NOW_OVERFETCH_FACTOR = 4
OPEN_NOW_OVERFETCH_LIMIT = 20


def _is_open_or_emergency(facility: MedicalFacility) -> bool:
    """
    院所是否視為「現在可前往」。

    設有急診者一律保留 —— clinicTime 記的是門診時間，實測 197 家設有急診的院所
    在深夜依門診時間判斷只有 1 家「營業中」。若照此篩選，急需急診的使用者
    會被告知附近沒有院所。這條規則獨立於狀態文案，改文案不會意外破壞它。
    """
    hours = resolve_business_hours(facility)
    return hours.is_emergency or hours.is_open_now


@dataclass(frozen=True)
class NearbySearchResult:
    """鄰近院所搜尋結果，含「搜到多遠」等呈現用的脈絡。"""

    facilities: list[MedicalFacility] = field(default_factory=list)
    """找到的院所，已依距離由近到遠排序。"""

    reached_meters: int = 0
    """實際涵蓋到的搜尋範圍，對應 NEARBY_SEARCH_STEPS 的其中一級。"""

    satisfied: bool = False
    """是否在 50 公里內湊滿目標筆數。False 代表回傳的是「有找到的部分」。"""

    open_now_requested: bool = False
    """本次搜尋是否要求只看營業中的院所。"""

    open_now_fallback: bool = False
    """要求營業中但一家都沒開，已退回未過濾結果。呈現層須據此改變文案。"""

    @property
    def expanded(self) -> bool:
        """是否曾放寬到第一級（5 公里）以外。"""
        return self.reached_meters > NEARBY_SEARCH_STEPS[0]


@dataclass(frozen=True)
class DepartmentSearchResult(NearbySearchResult):
    """依科別搜尋的結果，額外帶上科別解析的來龍去脈。"""

    match: DepartmentMatch | None = None
    """解析出的科別；為 None 代表看不懂使用者說的科別，未執行查詢。"""
class MedicalService:
    def __init__(self, repository: MedicalFacilityRepository | None = None) -> None:
        self.repository = repository or MedicalFacilityRepository()

    async def _search_tiered(
        self,
        lat: float,
        lng: float,
        target_count: int,
        query: dict[str, Any] | None = None,
        open_now: bool = False,
    ) -> NearbySearchResult:
        """
        逐級放寬 5→10→20→50 公里，直到湊滿目標筆數。

        實作上只打一次 DB：$geoNear 本來就由近到遠回傳，一次抓到 50 公里內最近的
        N 筆，等同於跑完整條階梯，但省下 3 次網路往返。分級只影響「回覆時要告訴
        使用者搜到多遠」，不影響選出來的院所。

        open_now 為真時多取回候選再於應用層過濾營業狀態：clinicTime 是「七天各含
        slots 陣列」的嵌套結構，用 $expr 下推到 Mongo 會複雜且無法利用索引。
        """
        max_meters = NEARBY_SEARCH_STEPS[-1]
        fetch_count = (
            min(target_count * OPEN_NOW_OVERFETCH_FACTOR, OPEN_NOW_OVERFETCH_LIMIT)
            if open_now
            else target_count
        )
        facilities = await self.repository.find_near(
            lat, lng, max_meters, fetch_count, query=query
        )

        fell_back_from_open_now = False
        if open_now:
            open_facilities = [f for f in facilities if _is_open_or_emergency(f)]
            if open_facilities:
                facilities = open_facilities
            else:
                # 深夜／午休時範圍內可能一家都沒開。回「查無院所」是最差的答案 ——
                # 退回未過濾的結果，讓呈現層改講「目前均未開診，以下為下次開診時間」。
                fell_back_from_open_now = True
                logger.info(
                    f"{LOGGER_HEADER_TEXT} open_now 過濾後為 0 筆，退回未過濾結果"
                )

        reached_meters, selected, satisfied = self._resolve_search_tier(
            facilities, target_count
        )
        return NearbySearchResult(
            facilities=selected,
            reached_meters=reached_meters,
            satisfied=satisfied,
            open_now_requested=open_now,
            open_now_fallback=fell_back_from_open_now,
        )

    async def find_nearby_hospitals(
        self,
        lat: float,
        lng: float,
        target_count: int = DEFAULT_TARGET_COUNT,
        open_now: bool = False,
    ) -> NearbySearchResult:
        """找出鄰近的醫療院所（不限科別），湊不滿就逐級放寬到 50 公里。"""
        logger.info(
            f"{LOGGER_HEADER_TEXT} 搜尋 ({lat}, {lng}) 附近醫療院所，"
            f"上限=%s 公尺, target=%s, open_now=%s",
            NEARBY_SEARCH_STEPS[-1],
            target_count,
            open_now,
        )
        result = await self._search_tiered(lat, lng, target_count, open_now=open_now)
        logger.info(
            f"{LOGGER_HEADER_TEXT} 搜尋完成，回傳=%s 筆, 涵蓋範圍=%s 公尺, 湊滿目標=%s",
            len(result.facilities),
            result.reached_meters,
            result.satisfied,
        )
        return result

    async def find_nearby_facilities_by_department(
        self,
        lat: float,
        lng: float,
        department: str,
        target_count: int = DEFAULT_TARGET_COUNT,
        open_now: bool = False,
    ) -> DepartmentSearchResult:
        """找出鄰近、且有指定科別的院所，同樣逐級放寬到 50 公里。"""
        match = resolve_department(department)
        if match is None:
            logger.info(
                f"{LOGGER_HEADER_TEXT} 無法解析科別，department=%r", department
            )
            return DepartmentSearchResult(match=None)

        logger.info(
            f"{LOGGER_HEADER_TEXT} 依科別搜尋 ({lat}, {lng})，"
            f"requested=%r → canonical=%r, 上限=%s 公尺, target=%s",
            match.requested,
            match.canonical,
            NEARBY_SEARCH_STEPS[-1],
            target_count,
        )
        result = await self._search_tiered(
            lat,
            lng,
            target_count,
            query=build_department_query(match.canonical),
            open_now=open_now,
        )
        logger.info(
            f"{LOGGER_HEADER_TEXT} 科別搜尋完成，canonical=%r, 回傳=%s 筆, "
            f"涵蓋範圍=%s 公尺, 湊滿目標=%s",
            match.canonical,
            len(result.facilities),
            result.reached_meters,
            result.satisfied,
        )
        return DepartmentSearchResult(
            match=match,
            facilities=result.facilities,
            reached_meters=result.reached_meters,
            satisfied=result.satisfied,
            open_now_requested=result.open_now_requested,
            open_now_fallback=result.open_now_fallback,
        )

    @staticmethod
    def _resolve_search_tier(
        facilities: list[MedicalFacility], target_count: int
    ) -> tuple[int, list[MedicalFacility], bool]:
        """
        從已依距離排序的結果推算「階梯式擴大」會停在哪一級。

        回傳 (涵蓋範圍公尺, 選出的院所, 是否湊滿目標筆數)。湊不滿時回傳最大範圍
        內所有找到的院所 —— 寧可給使用者 2 家 50 公里內的，也不要回「查無資料」。
        """
        for step in NEARBY_SEARCH_STEPS:
            within = [
                item
                for item in facilities
                if (item.distance_meters or 0) <= step
            ]
            if len(within) >= target_count:
                return step, within[:target_count], True

        return NEARBY_SEARCH_STEPS[-1], list(facilities[:target_count]), False

    async def find_facility_by_name(
        self,
        keyword: str,
        lat: float | None = None,
        lng: float | None = None,
        limit: int = 20,  # 最多回傳20筆資料
    ) -> tuple[list[MedicalFacility], int]:
        query, query_keyword_unified = build_facility_query(keyword)

        # 如果什麼搜尋條件都沒撈到，才回傳空結果
        if not query:
            return [], 0

        logger.info(f"{LOGGER_HEADER_TEXT} 最終 MongoDB 查詢條件 query = {query}")

        if lat is not None and lng is not None:
            # 先限縮在生活圈內，避免「仁愛醫院」把幾百公里外的同名院所排在前面。
            results = await self.repository.find_by_query_near(
                query,
                lat,
                lng,
                limit,
                max_distance_meters=NAME_SEARCH_RADIUS_METERS,
            )
            if not results:
                # 生活圈內查無同名院所時放寬到全國：使用者在高雄問「臺大醫院在哪」
                # 是合理需求，硬套距離上限會讓原本查得到的院所變成查無資料。
                logger.info(
                    f"{LOGGER_HEADER_TEXT} {NAME_SEARCH_RADIUS_METERS} 公尺內查無院所，"
                    "放寬為全國搜尋"
                )
                results = await self.repository.find_by_query_near(
                    query, lat, lng, limit
                )
        else:
            results = await self.repository.find_by_query(query, limit)
            results.sort(key=lambda item: similarity_rank(item, query_keyword_unified))

        return results, len(results)

    async def get_facility_by_id(self, facility_id: str) -> MedicalFacility | None:
        return await self.repository.find_by_id(facility_id)

medical_service = MedicalService()
