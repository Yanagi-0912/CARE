import logging
import math
import re
import urllib.parse
from typing import Any
from app.schemas import MedicalFacility,ClinicDaySchedule   
from app.db.mongodb import MongoDBManager

logger = logging.getLogger(__name__)

NO_FACILITY_MESSAGE = "抱歉，您附近 5 公里內暫時找不到醫療院所資料。\n功能仍在建置中，敬請期待！"
NO_NAMED_FACILITY_MESSAGE = "查無此院所資料。請提供更明確的院所名稱或地區關鍵字，我再幫您查詢。"

FACILITY_SUFFIXES = ("醫院", "診所", "衛生所", "藥局", "藥房")
TYPE_KEYWORDS = ("診所", "醫院", "藥局")
WEEKDAY_LABELS = {
    "monday": "週一", "tuesday": "週二", "wednesday": "週三",
    "thursday": "週四", "friday": "週五", "saturday": "週六", "sunday": "週日",
}

# [Fix #3] 精確縣市名稱清單，取代廣義貪婪正則 [\u4e00-\u9fff]{1,3}[縣市]。
# 舊正則會誤刪機構名中含有「市」「縣」字元的部分，或在剝離不完整時留下殘留字串，
# 改用明確清單逐一比對前綴，確保只刪除確實屬於縣市行政名稱的部分。
_COUNTY_CITY_PREFIXES: tuple[str, ...] = (
    "臺北市", "台北市", "新北市", "桃園市", "臺中市", "台中市",
    "臺南市", "台南市", "高雄市", "基隆市", "新竹市", "嘉義市",
    "新竹縣", "苗栗縣", "彰化縣", "南投縣", "雲林縣", "嘉義縣",
    "屏東縣", "宜蘭縣", "花蓮縣", "臺東縣", "台東縣",
    "澎湖縣", "金門縣", "連江縣",
)

# [Fix #6] 口語化地區名稱清單（不含縣/市字元），用於 find_facility_by_name 偵測地區前綴。
# 使用者輸入「花蓮中正診所」時，「花蓮」不觸發縣市前綴移除，
# 須透過此清單另行偵測，並補入 address 欄位篩選。
_INFORMAL_CITY_NAMES: tuple[str, ...] = (
    "臺北", "台北", "新北", "桃園", "臺中", "台中",
    "臺南", "台南", "高雄", "基隆", "新竹", "嘉義",
    "苗栗", "彰化", "南投", "雲林", "屏東", "宜蘭",
    "花蓮", "臺東", "台東", "澎湖", "金門", "連江",
)

def normalize_facility_name(text: str) -> str:
    normalized = re.sub(r"\s+", "", text or "")
    normalized = re.sub(r"[，,。．.？?！!：:；;「」『』()（）\[\]【】]", "", normalized)

    # [Fix #3] 改用精確縣市清單移除前綴，避免廣義正則誤刪機構名中含「市」「縣」的部分。
    # 舊：re.sub(r"^(臺灣|台灣)?[\u4e00-\u9fff]{1,3}[縣市]", "", normalized)
    for prefix in _COUNTY_CITY_PREFIXES:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
            break

    # [Fix #1] 統一將「台」轉為「臺」，使正規化後的關鍵字與查詢端的 [台臺] 正則搭配使用。
    # 查詢時會將「臺」展開為 [台臺]，確保不論資料庫使用哪種字元均能比對。
    normalized = normalized.replace("台", "臺")

    # 移除院所類型後綴（如「醫院」「衛生所」），保留核心名稱關鍵字供比對。
    for suffix in FACILITY_SUFFIXES:
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break

    # 移除殘留的鄉鎮市區後綴（縣市前綴移除後可能殘留，如「中正區」→「中正」）。
    for suffix in ("鄉", "鎮", "市", "區"):
        if normalized.endswith(suffix) and len(normalized) > 1:
            normalized = normalized[:-1]
            break

    return normalized

def detect_type_keyword(text: str) -> str | None:
    for keyword in TYPE_KEYWORDS:
        if keyword in (text or ""):
            return keyword
    return None

def _build_text_map_url(facility: MedicalFacility) -> str:
    """專門給純文字格式化使用的地圖連結"""
    query = f"{facility.latitude},{facility.longitude}" if facility.latitude and facility.longitude else (facility.address or facility.name)
    return f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(query)}"

def _build_text_tel_uri(phone: str | None) -> str | None:
    """專門給純文字格式化使用的電話 URI"""
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    return f"tel:{digits}" if len(digits) >= 6 else None

build_tel_uri = _build_text_tel_uri

def format_departments(departments: list[str] | None) -> str:
    if not departments:
        return "無資料"
    clean_departments = [item for item in departments if item]
    return "、".join(clean_departments) if clean_departments else "無資料"

def format_clinic_time(clinic_time: dict[str, ClinicDaySchedule] | None) -> str:
    if not clinic_time:
        return "無資料"
    parts: list[str] = []
    for day_key, label in WEEKDAY_LABELS.items():
        day = clinic_time.get(day_key)
        if day is None:
            continue
        if day.isClosed:
            parts.append(f"{label}：休診")
            continue
        # 讀取新 schema 的 slots 欄位，每個 slot 為 ClinicTimeSlot 物件（open/close）
        ranges = [
            f"{slot.open}-{slot.close}"
            for slot in day.slots
            if slot.open and slot.close
        ]
        parts.append(f"{label}：{'、'.join(ranges) if ranges else '無資料'}")
    return "\n".join(parts) if parts else "無資料"

def format_facility_detail(facility: MedicalFacility) -> str:
    """輸出純文字詳細資訊"""
    tel_uri = _build_text_tel_uri(facility.phone)
    phone_text = facility.phone or "無資料"
    lines = [
        f"已為您查到：{facility.name}",
        f"類型：{facility.type or '無資料'}",
        f"地址：{facility.address or '無資料'}",
        f"電話：{phone_text}",
        f"營業時間：\n{format_clinic_time(facility.clinic_time)}",
        f"診療科別：{format_departments(facility.departments)}",
        f"地圖連結：{_build_text_map_url(facility)}",
    ]
    if tel_uri:
        lines.append(f"撥打電話：{tel_uri}")
    return "\n".join(lines)

def format_candidate_list(facilities: list[MedicalFacility], total_count: int, *, has_location: bool) -> str:
    lines = ["找到多筆相似院所，請選擇您要查詢的院所："]
    for index, facility in enumerate(facilities[:3], 1):
        distance = f"（距離約 {facility.distance_meters:.0f} 公尺）" if has_location and facility.distance_meters is not None else ""
        lines.append(f"{index}. {facility.name}{distance}\n   類型：{facility.type or '無資料'}\n   地址：{facility.address or '無資料'}")
    if total_count > 3:
        lines.append("結果超過 3 筆，您可以提供更明確的地區或完整名稱以縮小範圍。")
    return "\n".join(lines)


class MedicalService:
    # 這裡只負責單純的資料獲取與運算，與前端 UI 展現徹底脫鉤
    async def find_nearby_hospitals(self, lat: float, lng: float, radius_meters: int = 5000, limit: int = 5) -> list[MedicalFacility]:
        logger.info(f"正在搜尋 ({lat}, {lng})附近{radius_meters}公尺的醫療院所, limit={limit}")
        collection = MongoDBManager.get_medical_collection()
        pipeline = [
            {
                "$geoNear": {
                    "near": {"type": "Point", "coordinates": [lng, lat]},
                    "distanceField": "distance_calculated",
                    "maxDistance": radius_meters,
                    "spherical": True,
                }
            },
            {"$limit": limit},
        ]
        results = []
        try:
            async for doc in collection.aggregate(pipeline):
                results.append(self._facility_from_doc(doc))
        except Exception as e:
            logger.error(f"MongoDB geospatial query failed: {e}", exc_info=True)
        return results

    async def find_facility_by_name(self, keyword: str, lat: float | None = None, lng: float | None = None, limit: int = 20) -> tuple[list[MedicalFacility], int]:
        raw_clean = re.sub(r"\s+", "", keyword or "")

        # [Fix #6] 偵測原始輸入中的口語化地區前綴（如「花蓮」，不含縣/市字元）。
        # 含縣市字元的前綴（如「花蓮縣」）已由 normalize_facility_name 內的 _COUNTY_CITY_PREFIXES 處理，
        # 此處僅處理純城市名（如「花蓮」）前綴，並稍後加入 address 欄位輔助篩選。
        detected_city: str | None = None
        keyword_for_normalize = keyword
        for city in _INFORMAL_CITY_NAMES:
            if raw_clean.startswith(city) and len(raw_clean) > len(city):
                # 確認剝離後下一個字元不是「縣」或「市」，
                # 避免「台北市立醫院」中「台北」被誤判為口語前綴（應由縣市清單處理）。
                remaining = raw_clean[len(city):]
                if remaining and remaining[0] not in ("縣", "市"):
                    detected_city = city
                    keyword_for_normalize = remaining
                    break

        normalized_keyword = normalize_facility_name(keyword_for_normalize)
        query_keyword = normalized_keyword or raw_clean
        if not query_keyword:
            return [], 0

        # [Fix #1] 將查詢關鍵字中的「臺」展開為 [台臺] 正則，
        # 使查詢同時比對資料庫中使用簡體「台」或正體「臺」的院所名稱，
        # 解決使用者輸入「台大」卻查不到資料庫存「臺大」院所的問題。
        escaped = re.escape(query_keyword)
        pattern = escaped.replace("臺", "[台臺]")

        collection = MongoDBManager.get_medical_collection()
        query: dict[str, Any] = {"name": {"$regex": pattern, "$options": "i"}}

        # [Fix #6] 若偵測到口語化地區前綴，加入 address 欄位輔助篩選，
        # 例：「花蓮中正診所」→ name 比對「中正」，同時限制 address 含「花蓮」。
        if detected_city:
            # 同步處理台/臺字元，使「台北」能比對資料庫地址中的「臺北」。
            city_normalized = detected_city.replace("台", "臺")
            city_pattern = city_normalized.replace("臺", "[台臺]")
            query["address"] = {"$regex": city_pattern, "$options": "i"}
            logger.info(
                f"[MedicalService] 偵測到口語化地區前綴，"
                f"原始關鍵字={keyword!r}，地區={detected_city!r}，查詢關鍵字={query_keyword!r}"
            )

        type_keyword = detect_type_keyword(keyword)
        if type_keyword == "藥局":
            query["$or"] = [{"type": {"$regex": "藥局", "$options": "i"}}, {"type": "藥師自營"}]
        elif type_keyword:
            query["type"] = {"$regex": type_keyword, "$options": "i"}

        results: list[MedicalFacility] = []
        try:
            if lat is not None and lng is not None:
                pipeline = [
                    {
                        "$geoNear": {
                            "near": {"type": "Point", "coordinates": [lng, lat]},
                            "distanceField": "distance_calculated",
                            "spherical": True,
                            "query": query,
                        }
                    },
                    {"$limit": limit},
                ]
                async for doc in collection.aggregate(pipeline):
                    results.append(self._facility_from_doc(doc))
            else:
                cursor = collection.find(query).limit(limit)
                async for doc in cursor:
                    results.append(self._facility_from_doc(doc))
                results.sort(key=lambda item: self._similarity_rank(item, query_keyword))
        except Exception as e:
            logger.error(f"MongoDB facility name query failed: {e}", exc_info=True)
            return [], 0

        return results, len(results)

    @staticmethod
    def _facility_from_doc(doc: dict[str, Any]) -> MedicalFacility:
        return MedicalFacility(
            id=str(doc.get("_id", "")),
            name=doc.get("name", "未知名稱"),
            latitude=doc.get("latitude", 0.0),
            longitude=doc.get("longitude", 0.0),
            address=doc.get("address", "暫無地址資訊"),
            phone=doc.get("phone"),
            type=doc.get("type", "醫療院所"),
            clinic_time=doc.get("clinicTime"),
            departments=doc.get("departments"),
            distance_meters=doc.get("distance_calculated"),
        )

    @staticmethod
    def _similarity_rank(facility: MedicalFacility, keyword: str) -> tuple[int, int]:
        facility_name = normalize_facility_name(facility.name)
        if facility_name == keyword: exact_rank = 0
        elif facility_name.startswith(keyword): exact_rank = 1
        elif keyword in facility_name: exact_rank = 2
        else: exact_rank = 3
        return exact_rank, math.inf if not facility_name else len(facility_name)

medical_service = MedicalService()