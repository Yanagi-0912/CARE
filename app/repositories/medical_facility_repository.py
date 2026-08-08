import logging
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId

from app.db.mongodb import MongoDBManager
from app.schemas import MedicalFacility

logger = logging.getLogger(__name__)


class MedicalFacilityRepository:
    # 依經緯度搜尋附近醫療院所
    async def find_near(
        self,
        lat: float,
        lng: float,
        radius_meters: int,
        limit: int,
        query: dict[str, Any] | None = None,
    ) -> list[MedicalFacility]:
        """
        依距離由近到遠回傳院所。傳入 query 可額外過濾（例如限定科別），
        過濾條件會併入 $geoNear 內部，讓 Mongo 邊擴張搜尋半徑邊篩選，
        而不是先取最近 N 筆再過濾（後者會漏掉稍遠但符合條件的院所）。
        """
        collection = MongoDBManager.get_medical_collection()
        geo_near: dict[str, Any] = {
            "near": {"type": "Point", "coordinates": [lng, lat]},
            "distanceField": "distance_calculated",
            "maxDistance": radius_meters,
            "spherical": True,
        }
        if query:
            geo_near["query"] = query
        pipeline = [
            {"$geoNear": geo_near},
            {"$limit": limit},
        ]
        results = []
        try:
            async for doc in collection.aggregate(pipeline):
                results.append(self._facility_from_doc(doc))
        except Exception as e:
            logger.error(f"MongoDB geospatial query failed: {e}", exc_info=True)
        return results
    # 依院所名稱或地址關鍵字搜尋醫療院所
    async def find_by_query(
        self, query: dict[str, Any], limit: int
    ) -> list[MedicalFacility]:
        collection = MongoDBManager.get_medical_collection()
        results = []
        try:
            cursor = collection.find(query).limit(limit)
            async for doc in cursor:
                results.append(self._facility_from_doc(doc))
        except Exception as e:
            logger.error(f"MongoDB facility name query failed: {e}", exc_info=True)
        return results
    # 依院所名稱或地址關鍵字搜尋醫療院所，並依經緯度排序
    async def find_by_query_near(
        self,
        query: dict[str, Any],
        lat: float,
        lng: float,
        limit: int,
        max_distance_meters: int | None = None,
    ) -> list[MedicalFacility]:
        """
        依關鍵字搜尋並由近到遠排序。max_distance_meters 為 None 時不限距離
        （全國搜尋），呼叫端須自行決定是否要限縮。
        """
        collection = MongoDBManager.get_medical_collection()
        geo_near: dict[str, Any] = {
            "near": {"type": "Point", "coordinates": [lng, lat]},
            "distanceField": "distance_calculated",
            "spherical": True,
            "query": query,
        }
        if max_distance_meters is not None:
            geo_near["maxDistance"] = max_distance_meters
        pipeline = [
            {"$geoNear": geo_near},
            {"$limit": limit},
        ]
        results = []
        try:
            async for doc in collection.aggregate(pipeline):
                results.append(self._facility_from_doc(doc))
        except Exception as e:
            logger.error(f"MongoDB facility name query failed: {e}", exc_info=True)
        return results

    async def find_by_id(self, facility_id: str) -> MedicalFacility | None:
        logger.info(
            f"[MedicalService] 正在依 _id 查詢院所，facility_id={facility_id!r}"
        )
        try:
            object_id = ObjectId(facility_id)
        except (InvalidId, TypeError):
            logger.warning(
                f"[MedicalService] facility_id 格式不合法，facility_id={facility_id!r}"
            )
            return None

        collection = MongoDBManager.get_medical_collection()
        try:
            doc = await collection.find_one({"_id": object_id})
        except Exception as e:
            logger.error(f"MongoDB find_one by _id query failed: {e}", exc_info=True)
            return None

        if doc is None:
            logger.info(f"[MedicalService] 查無院所，facility_id={facility_id!r}")
            return None

        return self._facility_from_doc(doc)
    # 將 MongoDB document 轉換為 MedicalFacility 物件
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
            notes=doc.get("notes") or None,
            distance_meters=doc.get("distance_calculated"),
        )
