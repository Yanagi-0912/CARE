import logging
import re
from typing import Any

from app.repositories.medical_facility_repository import MedicalFacilityRepository
from app.schemas import MedicalFacility
from app.services.medical.medical_facility_matcher import (
    build_facility_query,
    similarity_rank,
)

logger = logging.getLogger(__name__)

NO_FACILITY_MESSAGE = "抱歉，您附近 5 公里內暫時找不到醫療院所資料。\n功能仍在建置中，敬請期待！"
NO_NAMED_FACILITY_MESSAGE = "查無此院所資料。請提供更明確的院所名稱或地區關鍵字，我再幫您查詢。"

LOGGER_HEADER_TEXT = "[Services:MedicalService]"
class MedicalService:
    def __init__(self, repository: MedicalFacilityRepository | None = None) -> None:
        self.repository = repository or MedicalFacilityRepository()

    async def find_nearby_hospitals(
        self, lat: float, lng: float, radius_meters: int = 5000, limit: int = 5
    ) -> list[MedicalFacility]:
        logger.info(
            f"{LOGGER_HEADER_TEXT} 正在搜尋 ({lat}, {lng})附近{radius_meters}公尺的醫療院所, limit={limit}"
        )
        return await self.repository.find_near(lat, lng, radius_meters, limit)

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
            results = await self.repository.find_by_query_near(query, lat, lng, limit)
        else:
            results = await self.repository.find_by_query(query, limit)
            results.sort(key=lambda item: similarity_rank(item, query_keyword_unified))

        return results, len(results)

    async def get_facility_by_id(self, facility_id: str) -> MedicalFacility | None:
        return await self.repository.find_by_id(facility_id)

medical_service = MedicalService()
