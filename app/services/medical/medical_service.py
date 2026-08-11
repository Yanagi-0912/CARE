import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.repositories.medical_facility_repository import MedicalFacilityRepository
from app.schemas import MedicalFacility
from app.i18n.messages import t
from app.services.medical.business_hours import resolve_business_hours
from app.services.medical.department_matcher import (
    DepartmentMatch,
    build_department_query,
    resolve_department,
)
from app.services.medical.facility_type_matcher import (
    FacilityTypeMatch,
    build_facility_type_query,
    resolve_facility_type,
)
from app.services.medical.medical_facility_matcher import (
    build_facility_query,
    similarity_rank,
)

if TYPE_CHECKING:
    # 只為了型別標註而 import。llm_term_resolver 會拉進 GeminiService／langchain，
    # 執行期匯入等於讓「查附近院所」相依於 LLM 用戶端，而兜底解析器是選填的。
    from app.services.medical.llm_term_resolver import TermResolver

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


def _normalize_optional_arg(value: str | None) -> str | None:
    """
    把空字串／純空白的字串參數一律正規化成 None（＝視為「沒有給」）。

    為什麼需要：facility_type 是選填參數，而 LLM function calling（尤其 Gemini）
    對選填字串參數送 `""` 是實務上很常見的行為。若沿用 `is not None` 判斷，
    空字串會被當成「使用者說了某個看不懂的類型」而走進解析失敗分支——不查
    資料庫、直接回「我不確定「」對應到哪一種院所類型」，一個空字串就讓
    「找附近院所」這個核心流程整個壞掉。空字串在語意上等同未提供，必須在
    進入解析之前就吸收掉，才能讓兩層（agent 用 truthy、service 用 is not None）
    對「空值」的定義一致。
    """
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


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

    facility_type_match: FacilityTypeMatch | None = None
    """解析出的院所類型；未要求類型過濾時亦為 None，故不可單獨用來判斷「看不懂」——
    請改看 facility_type_unresolved。"""

    facility_type_unresolved: bool = False
    """呼叫端有給 facility_type 但解析不出來。True 時本次未查詢 DB（比照科別解析
    失敗的處理方式），facilities 必為空清單。與 facility_type_match 皆為 None
    的情形（未要求類型過濾）區分開來，讓呼叫端能分辨「沒有要濾類型」與
    「濾了但看不懂」——後者若被誤判成前者，使用者會誤以為系統理解了他的需求。"""

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
    def __init__(
        self,
        repository: MedicalFacilityRepository | None = None,
        *,
        department_resolver: "TermResolver | None" = None,
        facility_type_resolver: "TermResolver | None" = None,
    ) -> None:
        self.repository = repository or MedicalFacilityRepository()
        # 兩個兜底解析器都是選填：沒接上時行為與加這層之前完全相同（表查不到就
        # 回「看不懂」）。單元測試因此不需要為了測搜尋邏輯而準備一個假 LLM。
        self._department_resolver = department_resolver
        self._facility_type_resolver = facility_type_resolver

    def configure_llm_fallbacks(
        self,
        *,
        department_resolver: "TermResolver | None" = None,
        facility_type_resolver: "TermResolver | None" = None,
    ) -> None:
        """
        事後接上 LLM 兜底解析器。

        存在的理由：本模組結尾就地建立了 medical_service 單例（模組載入時），
        而解析器需要 GeminiService，那是在 app.dependencies 才組好的。與其把
        單例改成延遲建立而動到所有 import 端，不如比照 configure_medical_tools
        的做法，在 dependencies 完成組裝後回頭注入。
        """
        if department_resolver is not None:
            self._department_resolver = department_resolver
        if facility_type_resolver is not None:
            self._facility_type_resolver = facility_type_resolver

    async def _resolve_department_with_fallback(
        self, department: str
    ) -> DepartmentMatch | None:
        """先查別名表，表查不到才動用 LLM 兜底（見 llm_term_resolver 模組註解）。"""
        match = resolve_department(department)
        if match is not None or self._department_resolver is None:
            return match

        canonical = await self._department_resolver.resolve(department)
        if canonical is None:
            return None
        logger.info(
            f"{LOGGER_HEADER_TEXT} 科別由 LLM 兜底解析，requested=%r → canonical=%r",
            department,
            canonical,
        )
        return DepartmentMatch(
            canonical=canonical, requested=department, source="llm"
        )

    async def _resolve_facility_type_with_fallback(
        self, facility_type: str
    ) -> FacilityTypeMatch | None:
        """同 _resolve_department_with_fallback，但對應到院所類型分類。"""
        match = resolve_facility_type(facility_type)
        if match is not None or self._facility_type_resolver is None:
            return match

        category = await self._facility_type_resolver.resolve(facility_type)
        if category is None:
            return None
        logger.info(
            f"{LOGGER_HEADER_TEXT} 院所類型由 LLM 兜底解析，requested=%r → category=%r",
            facility_type,
            category,
        )
        return FacilityTypeMatch(
            category=category, requested=facility_type, source="llm"
        )

    async def _search_tiered(
        self,
        lat: float,
        lng: float,
        target_count: int,
        query: dict[str, Any] | None = None,
        open_now: bool = False,
        facility_type_match: FacilityTypeMatch | None = None,
    ) -> NearbySearchResult:
        """
        逐級放寬 5→10→20→50 公里，直到湊滿目標筆數。

        facility_type_match 純粹是要原樣夾帶進回傳結果，本身不影響查詢邏輯——
        真正的類型過濾條件已經在呼叫端組進 query 裡了，這裡只是共用建構子的
        單一出口，避免兩個呼叫端各自手動複製 NearbySearchResult 的欄位。

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
            facility_type_match=facility_type_match,
        )

    async def find_nearby_hospitals(
        self,
        lat: float,
        lng: float,
        target_count: int = DEFAULT_TARGET_COUNT,
        open_now: bool = False,
        facility_type: str | None = None,
    ) -> NearbySearchResult:
        """
        找出鄰近的醫療院所，可選擇只看某一類型（醫院／診所／藥局），
        湊不滿就逐級放寬到 50 公里。

        facility_type 為 None 代表不限類型（省略時行為與過去完全相同）；
        給了但解析不出來時比照科別搜尋的處理方式：不查 DB，直接回傳
        facility_type_unresolved=True，讓呼叫端能明確告知使用者「看不懂」，
        而不是靜默退化成查全部院所。空字串／純空白視同未提供（見
        _normalize_optional_arg），不會被誤判成「看不懂的類型」。
        """
        facility_type = _normalize_optional_arg(facility_type)
        type_match: FacilityTypeMatch | None = None
        type_query: dict[str, Any] | None = None
        if facility_type is not None:
            type_match = await self._resolve_facility_type_with_fallback(facility_type)
            if type_match is None:
                logger.info(
                    f"{LOGGER_HEADER_TEXT} 無法解析院所類型，facility_type=%r",
                    facility_type,
                )
                return NearbySearchResult(facility_type_unresolved=True)
            type_query = build_facility_type_query(type_match.category)

        logger.info(
            f"{LOGGER_HEADER_TEXT} 搜尋 ({lat}, {lng}) 附近醫療院所，"
            f"上限=%s 公尺, target=%s, open_now=%s, facility_type=%s",
            NEARBY_SEARCH_STEPS[-1],
            target_count,
            open_now,
            type_match.category if type_match else None,
        )
        result = await self._search_tiered(
            lat,
            lng,
            target_count,
            query=type_query,
            open_now=open_now,
            facility_type_match=type_match,
        )
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
        facility_type: str | None = None,
    ) -> DepartmentSearchResult:
        """
        找出鄰近、且有指定科別的院所，同樣逐級放寬到 50 公里；
        facility_type 可再疊加類型過濾（兩個條件以 $and 組合，見 _combine_filters）。

        facility_type 解析失敗時比照科別解析失敗：不查 DB，
        回傳 facility_type_unresolved=True 讓呼叫端能分辨「看不懂類型」；
        但空字串／純空白視同未提供（見 _normalize_optional_arg）。
        """
        facility_type = _normalize_optional_arg(facility_type)
        match = await self._resolve_department_with_fallback(department)
        if match is None:
            logger.info(
                f"{LOGGER_HEADER_TEXT} 無法解析科別（含 LLM 兜底），department=%r",
                department,
            )
            return DepartmentSearchResult(match=None)

        type_match: FacilityTypeMatch | None = None
        type_query: dict[str, Any] | None = None
        if facility_type is not None:
            type_match = await self._resolve_facility_type_with_fallback(facility_type)
            if type_match is None:
                logger.info(
                    f"{LOGGER_HEADER_TEXT} 無法解析院所類型，facility_type=%r",
                    facility_type,
                )
                return DepartmentSearchResult(match=match, facility_type_unresolved=True)
            type_query = build_facility_type_query(type_match.category)

        logger.info(
            f"{LOGGER_HEADER_TEXT} 依科別搜尋 ({lat}, {lng})，"
            f"requested=%r → canonical=%r, 上限=%s 公尺, target=%s, facility_type=%s",
            match.requested,
            match.canonical,
            NEARBY_SEARCH_STEPS[-1],
            target_count,
            type_match.category if type_match else None,
        )
        query = self._combine_filters(build_department_query(match.canonical), type_query)
        result = await self._search_tiered(
            lat,
            lng,
            target_count,
            query=query,
            open_now=open_now,
            facility_type_match=type_match,
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
            facility_type_match=result.facility_type_match,
        )

    @staticmethod
    def _combine_filters(
        *filters: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """
        把多個查詢條件以 $and 組合；只有一個條件時不包 $and。

        刻意保留「單一條件不包 $and」這個特例，是為了不動到既有測試對
        department-only query 的斷言（`{"departments": {...}}`），否則
        單純新增類型過濾這個維度就會意外改變科別搜尋既有的 query 形狀，
        破壞向後相容。
        """
        active = [f for f in filters if f]
        if not active:
            return None
        if len(active) == 1:
            return active[0]
        return {"$and": active}

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
