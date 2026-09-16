import asyncio
import logging
import re
from collections.abc import Callable
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.repositories.medical_facility_repository import MedicalFacilityRepository
from app.schemas import MedicalFacility
from app.i18n.messages import t
from app.services.medical.business_hours import (
    TAIPEI_TZ,
    build_open_now_query,
    is_late_night,
    resolve_business_hours,
)
from app.services.medical.department_matcher import (
    DepartmentMatch,
    GENERAL_PRACTICE_DEPARTMENTS,
    UNSPECIFIED_DEPARTMENTS,
    build_department_query,
    build_unspecified_department_query,
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

# 篩選「現在營業中」時多取回幾倍候選。營業條件已經交給 Mongo 篩（build_open_now_query），
# 但長期性註記、今日時段不可信這些規則仍在應用層判斷，會再濾掉幾家，多拿一些才湊得滿。
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


def _is_open_or_emergency(facility: MedicalFacility, now: datetime) -> bool:
    """
    院所是否視為「現在可前往」。

    設有急診者一律保留 —— clinicTime 記的是門診時間，實測 197 家設有急診的院所
    在深夜依門診時間判斷只有 1 家「營業中」。若照此篩選，急需急診的使用者
    會被告知附近沒有院所。這條規則獨立於狀態文案，改文案不會意外破壞它。
    """
    hours = resolve_business_hours(facility, now=now)
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
    """向後相容舊單科呼叫端；新流程請改用 matches。"""

    matches: tuple[DepartmentMatch, ...] = ()
    """解析出的科別，同一個部定專科只留一個；為空代表一科都看不懂，未執行查詢。"""

    unresolved_departments: tuple[str, ...] = ()
    """看不懂的科別，原樣保留使用者的說法。matches 不為空時代表「只查了看得懂的
    那幾科」，呈現層 SHALL 說明哪幾科沒被搜尋，不能讓使用者以為每一科都查了。"""

    def __post_init__(self) -> None:
        if self.match is not None and not self.matches:
            object.__setattr__(self, "matches", (self.match,))
        elif self.match is None and self.matches:
            object.__setattr__(self, "match", self.matches[0])

    unspecified_ids: frozenset[str] = frozenset()
    """facilities 之中 departments 沒列出所查科別的院所 id——它們只申報了不分科。

    有兩種來源：搜內科、家醫科時主查詢本來就涵蓋不分科的院所（見
    GENERAL_PRACTICE_DEPARTMENTS），附近若全是這種診所，五張卡可能一張都
    沒寫內科；專科搜尋湊不滿時的補充梯次亦同。呈現層 SHALL 據此標示這幾筆
    未載明科別，否則使用者會以為那間診所的資料寫著他要的那一科。
    """
class MedicalService:
    def __init__(
        self,
        repository: MedicalFacilityRepository | None = None,
        *,
        department_resolver: "TermResolver | None" = None,
        facility_type_resolver: "TermResolver | None" = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository or MedicalFacilityRepository()
        # 營業中篩選要知道現在幾點。以參數注入，測試才能把時間固定在深夜。
        self._clock = clock or (lambda: datetime.now(TAIPEI_TZ))
        # 兩個兜底解析器都是選填：沒接上時行為與加這層之前完全相同（表查不到就
        # 回「看不懂」）。單元測試因此不需要為了測搜尋邏輯而準備一個假 LLM。
        self._department_resolver = department_resolver
        self._facility_type_resolver = facility_type_resolver

    def is_late_night(self) -> bool:
        """現在是否為深夜（見 business_hours.is_late_night），時間取自注入的 clock。"""
        return is_late_night(self._clock())

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

        open_now 為真時把營業條件併進 $geoNear 的 query（見 build_open_now_query），
        讓 Mongo 在 50 公里內由近到遠找有開的。原本是先取最近 20 家再於應用層篩，
        但城市裡最近 20 家都在 600 公尺內、深夜全關 —— 實測 9 個地點在 23:30 與
        02:30 全部篩成 0 家、退回列一排沒開的，其實 1～3 公里外就有急診醫院。
        """
        max_meters = NEARBY_SEARCH_STEPS[-1]
        fell_back_from_open_now = False
        if open_now:
            now = self._clock()
            candidates = await self.repository.find_near(
                lat,
                lng,
                max_meters,
                min(target_count * OPEN_NOW_OVERFETCH_FACTOR, OPEN_NOW_OVERFETCH_LIMIT),
                query=self._combine_filters(query, build_open_now_query(now)),
            )
            facilities = [f for f in candidates if _is_open_or_emergency(f, now)]
            if not facilities:
                # 50 公里內連急診都沒有（或撈回來的全被應用層規則濾掉）才會走到這裡。
                # 回「查無院所」是最差的答案 —— 改列最近的院所，
                # 讓呈現層改講「目前均未開診，以下為下次開診時間」。
                fell_back_from_open_now = True
                logger.info(
                    f"{LOGGER_HEADER_TEXT} open_now 過濾後為 0 筆，退回未過濾結果"
                )
                facilities = await self.repository.find_near(
                    lat, lng, max_meters, target_count, query=query
                )
        else:
            facilities = await self.repository.find_near(
                lat, lng, max_meters, target_count, query=query
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
        departments: Sequence[str] | str,
        target_count: int = DEFAULT_TARGET_COUNT,
        open_now: bool = False,
        facility_type: str | None = None,
    ) -> DepartmentSearchResult:
        """
        找出鄰近、且有指定科別的院所，同樣逐級放寬到 50 公里；
        facility_type 可再疊加類型過濾（兩個條件以 $and 組合，見 _combine_filters）。

        departments 可一次給多科（保底卡按鈕的「家醫科、內科、不分科」）：院所有其中
        任一科即命中，仍只打一次 DB，結果照樣由近到遠。有幾科看不懂時照查看得懂的，
        看不懂的放進 unresolved_departments；一科都看不懂才不查 DB。

        facility_type 解析失敗時比照科別解析失敗：不查 DB，
        回傳 facility_type_unresolved=True 讓呼叫端能分辨「看不懂類型」；
        但空字串／純空白視同未提供（見 _normalize_optional_arg）。
        """
        facility_type = _normalize_optional_arg(facility_type)
        matches, unresolved = await self._resolve_departments(departments)
        if not matches:
            logger.info(
                f"{LOGGER_HEADER_TEXT} 無法解析科別（含 LLM 兜底），departments=%r",
                unresolved,
            )
            return DepartmentSearchResult(unresolved_departments=unresolved)

        type_match: FacilityTypeMatch | None = None
        type_query: dict[str, Any] | None = None
        if facility_type is not None:
            type_match = await self._resolve_facility_type_with_fallback(facility_type)
            if type_match is None:
                logger.info(
                    f"{LOGGER_HEADER_TEXT} 無法解析院所類型，facility_type=%r",
                    facility_type,
                )
                return DepartmentSearchResult(
                    matches=matches,
                    unresolved_departments=unresolved,
                    facility_type_unresolved=True,
                )
            type_query = build_facility_type_query(type_match.category)

        canonicals = tuple(match.canonical for match in matches)
        logger.info(
            f"{LOGGER_HEADER_TEXT} 依科別搜尋 ({lat}, {lng})，"
            f"requested=%r → canonical=%r, 看不懂=%r, 上限=%s 公尺, target=%s, "
            f"facility_type=%s",
            [match.requested for match in matches],
            canonicals,
            unresolved,
            NEARBY_SEARCH_STEPS[-1],
            target_count,
            type_match.category if type_match else None,
        )
        query = self._combine_filters(build_department_query(*canonicals), type_query)
        result = await self._search_tiered(
            lat,
            lng,
            target_count,
            query=query,
            open_now=open_now,
            facility_type_match=type_match,
        )
        result = await self._supplement_with_unspecified(
            result,
            lat,
            lng,
            target_count,
            canonicals=canonicals,
            type_query=type_query,
            open_now=open_now,
            type_match=type_match,
        )
        logger.info(
            f"{LOGGER_HEADER_TEXT} 科別搜尋完成，canonical=%r, 回傳=%s 筆, "
            f"涵蓋範圍=%s 公尺, 湊滿目標=%s",
            canonicals,
            len(result.facilities),
            result.reached_meters,
            result.satisfied,
        )
        return DepartmentSearchResult(
            matches=matches,
            unresolved_departments=unresolved,
            facilities=result.facilities,
            reached_meters=result.reached_meters,
            satisfied=result.satisfied,
            open_now_requested=result.open_now_requested,
            open_now_fallback=result.open_now_fallback,
            facility_type_match=result.facility_type_match,
            unspecified_ids=self._unspecified_ids(result.facilities, canonicals),
        )

    async def _resolve_departments(
        self, departments: Sequence[str] | str
    ) -> tuple[tuple[DepartmentMatch, ...], tuple[str, ...]]:
        """
        逐科解析，回傳（解析出的科別, 看不懂的原始說法）。

        同一個部定專科只留第一次出現的說法：「腸胃科、心臟科」都是內科，查一次就夠，
        別名告知也沿用使用者的第一個說法。要動用 LLM 兜底的科別並行解析，多科時
        延遲不會一科一科疊上去。字串輸入視為單科，以相容舊呼叫端。
        """
        if isinstance(departments, str):
            requested_source: Sequence[str] = [departments]
        else:
            requested_source = departments
        requested = list(
            dict.fromkeys(text.strip() for text in requested_source if (text or "").strip())
        )
        resolved = await asyncio.gather(
            *(self._resolve_department_with_fallback(text) for text in requested)
        )
        matches: dict[str, DepartmentMatch] = {}
        unresolved: list[str] = []
        for text, match in zip(requested, resolved):
            if match is None:
                unresolved.append(text)
            else:
                matches.setdefault(match.canonical, match)
        return tuple(matches.values()), tuple(unresolved)

    async def _supplement_with_unspecified(
        self,
        result: NearbySearchResult,
        lat: float,
        lng: float,
        target_count: int,
        *,
        canonicals: tuple[str, ...],
        type_query: dict[str, Any] | None,
        open_now: bool,
        type_match: FacilityTypeMatch | None,
    ) -> NearbySearchResult:
        """
        專科搜尋湊不滿時，把附近未申報專科的院所補進來墊底。

        為什麼要有這一段：資料庫有一批院所的 departments 只有「不分科」，它們
        對任何專科查詢都不會命中。使用者站在那間診所旁邊搜「附近的皮膚科」，
        拿到的是「50 公里內查無」——但那不是附近沒有院所，是那家沒有申報科別。

        為什麼只在湊不滿時補、而且排在後面：一間沒申報科別的診所不等於有那一科。
        正牌的專科院所永遠優先，補進來的只是「附近就這幾家，你可以先去電問問」，
        並由呈現層標示清楚（見 DepartmentSearchResult.unspecified_ids）。

        有任一科是通科型就不走這裡：主查詢已經涵蓋未申報專科的院所，再補一次只會
        撈到同一批。
        """
        if result.satisfied or any(
            canonical in GENERAL_PRACTICE_DEPARTMENTS for canonical in canonicals
        ):
            return result

        missing = target_count - len(result.facilities)
        if missing <= 0:
            return result

        supplement = await self._search_tiered(
            lat,
            lng,
            missing,
            query=self._combine_filters(
                build_unspecified_department_query(), type_query
            ),
            open_now=open_now,
            facility_type_match=type_match,
        )
        seen = {f.id for f in result.facilities}
        extra = [f for f in supplement.facilities if f.id not in seen][:missing]
        if not extra:
            return result

        logger.info(
            f"{LOGGER_HEADER_TEXT} 科別 %r 湊不滿（%s 筆），"
            "補上 %s 筆未申報科別的鄰近院所",
            canonicals,
            len(result.facilities),
            len(extra),
        )
        return replace(result, facilities=[*result.facilities, *extra])

    @staticmethod
    def _unspecified_ids(
        facilities: list[MedicalFacility], canonicals: tuple[str, ...]
    ) -> frozenset[str]:
        """
        結果中 departments 沒列出所查科別的院所 id，呈現層據此在卡片加註。

        搜內科時主查詢會一併撈出只申報不分科的診所（見 build_department_query），
        排序只看距離，附近若全是這種診所，五筆裡可以一筆內科都沒有。這裡不動
        搜尋結果，只把它們挑出來標示；專科補充梯次補上的院所也由同一條規則標出。

        用子字串比對，與查詢用的 regex 一致：少數院所的 departments 是「家醫科、
        內科、…」整串塞進單一元素，精確比對會把它們誤標成未載明科別。
        使用者本來就在找不分科時（保底卡），不分科院所正是他要的，不標。
        """
        wanted = set(canonicals)
        if wanted & set(UNSPECIFIED_DEPARTMENTS):
            wanted.update(UNSPECIFIED_DEPARTMENTS)
        return frozenset(
            facility.id
            for facility in facilities
            if not any(
                value in listed
                for listed in facility.departments or ()
                for value in wanted
            )
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
