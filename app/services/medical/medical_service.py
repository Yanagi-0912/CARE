import logging
import math
import re
from typing import Any
from app.schemas import MedicalFacility   
from app.db.mongodb import MongoDBManager
from app.services.medical.medical_facility_matcher import(
    _INFORMAL_CITY_NAMES, normalize_facility_name, detect_type_keyword,FACILITY_ALIASES
)
logger = logging.getLogger(__name__)

NO_FACILITY_MESSAGE = "抱歉，您附近 5 公里內暫時找不到醫療院所資料。\n功能仍在建置中，敬請期待！"
NO_NAMED_FACILITY_MESSAGE = "查無此院所資料。請提供更明確的院所名稱或地區關鍵字，我再幫您查詢。"

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

        # 偵測原始輸入中的口語化地區前綴（如"花蓮"，不含縣/市字元）。
        # 含縣市字元的前綴（如"花蓮縣"）已由 normalize_facility_name 內的 _COUNTY_CITY_PREFIXES 處理，
        # 此處僅處理純城市名（如"花蓮"）前綴，並稍後加入 address 欄位輔助篩選。
        detected_city: str | None = None
        keyword_for_normalize = keyword
        for city in _INFORMAL_CITY_NAMES:#偵測口語化地區前綴
            if raw_clean.startswith(city) and len(raw_clean) > len(city):
                # 確認剝離後下一個字元不是"縣"或"市"，
                # 避免"台北市立醫院"中"台北"被誤判為口語前綴（應由縣市清單處理）。
                remaining = raw_clean[len(city):]
                if remaining and remaining[0] not in ("縣", "市"):
                    detected_city = city
                    keyword_for_normalize = remaining
                    break

        normalized_keyword = normalize_facility_name(keyword_for_normalize)
        query_keyword = normalized_keyword or raw_clean
        # 檢查正規化後的關鍵字是否是知名簡稱(如台大、榮總)，若是則替換為官方核心關鍵字
        if normalized_keyword in FACILITY_ALIASES:
            query_keyword = FACILITY_ALIASES[normalized_keyword]
        else:
            query_keyword = normalized_keyword or raw_clean
            
        if not query_keyword:
            return [], 0

        escaped = re.escape(query_keyword)
        escaped = escaped.replace("台", "臺")
        
        collection = MongoDBManager.get_medical_collection()
        query: dict[str, Any] = {"name": {"$regex": escaped, "$options": "i"}}

        # 若偵測到口語化地區前綴，加入 address 欄位輔助篩選，
        # 例："花蓮中正診所"name 比對"中正"，同時限制 address 含"花蓮"。
        if detected_city:
            # 同步處理台/臺字元，使"台北"能比對資料庫地址中的"臺北"。
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
    # 依據關鍵字與院所名稱的相似度進行排序
    def _similarity_rank(facility: MedicalFacility, keyword: str) -> tuple[int, int]:
        facility_name = normalize_facility_name(facility.name)
        if facility_name == keyword: exact_rank = 0
        elif facility_name.startswith(keyword): exact_rank = 1
        elif keyword in facility_name: exact_rank = 2
        else: exact_rank = 3
        return exact_rank, math.inf if not facility_name else len(facility_name)

medical_service = MedicalService()