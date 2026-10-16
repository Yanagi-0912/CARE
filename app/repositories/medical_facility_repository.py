import logging
from collections.abc import Callable
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId

from app.db.mongodb import MongoDBManager
from app.schemas import MedicalFacility

logger = logging.getLogger(__name__)


class MedicalFacilityRepository:
    def __init__(
        self,
        collection_getter: Callable[[], Any] | None = None,
        *,
        supplemental_collection_getter: Callable[[], Any] | None = None,
    ) -> None:
        """
        collection_getter 省略時查 medicalFacilities（醫院、診所）。

        藥局放在另一個 collection，欄位與索引都相同，所以不另寫一份 repository，
        改由 for_pharmacies() 建一個指向藥局庫的實例。
        """
        self._collection_getter = collection_getter
        self._supplemental_collection_getter = supplemental_collection_getter

    @classmethod
    def for_pharmacies(cls) -> "MedicalFacilityRepository":
        """附近只查藥局；名稱、詳細資料與名稱索引另包含品牌零售門市。"""
        return cls(
            MongoDBManager.get_pharmacy_collection,
            supplemental_collection_getter=MongoDBManager.get_retail_collection,
        )

    def _name_collections(self):
        collections = [self._collection()]
        if self._supplemental_collection_getter is not None:
            collections.append(self._supplemental_collection_getter())
        return collections

    def _with_name_aliases(self, query: dict[str, Any]) -> dict[str, Any]:
        if self._supplemental_collection_getter is None:
            return query

        def aliases(value):
            if isinstance(value, dict):
                return {("aliases" if key == "name" else key): aliases(item) for key, item in value.items()}
            if isinstance(value, list):
                return [aliases(item) for item in value]
            return value

        alternate = aliases(query)
        return {"$or": [query, alternate]} if alternate != query else query

    def _collection(self):
        # 每次查詢才取，不在建構時綁死：service 單例在模組載入時就建好，
        # 那時 MongoDB 還沒設定。
        getter = self._collection_getter or MongoDBManager.get_medical_collection
        return getter()

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
        collection = self._collection()
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
        query = self._with_name_aliases(query)
        results = []
        try:
            for collection in self._name_collections():
                cursor = collection.find(query).limit(limit)
                async for doc in cursor:
                    results.append(self._facility_from_doc(doc))
        except Exception as e:
            logger.error(f"MongoDB facility name query failed: {e}", exc_info=True)
        if self._supplemental_collection_getter is not None:
            results.sort(key=lambda item: item.name)
        return results[:limit]
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
        geo_near: dict[str, Any] = {
            "near": {"type": "Point", "coordinates": [lng, lat]},
            "distanceField": "distance_calculated",
            "spherical": True,
            "query": self._with_name_aliases(query),
        }
        if max_distance_meters is not None:
            geo_near["maxDistance"] = max_distance_meters
        pipeline = [
            {"$geoNear": geo_near},
            {"$limit": limit},
        ]
        results = []
        try:
            for collection in self._name_collections():
                async for doc in collection.aggregate(pipeline):
                    results.append(self._facility_from_doc(doc))
        except Exception as e:
            logger.error(f"MongoDB facility name query failed: {e}", exc_info=True)
        results.sort(key=lambda item: item.distance_meters if item.distance_meters is not None else float("inf"))
        return results[:limit]

    async def list_all_names(self) -> set[str]:
        """
        取出全部院所名稱，供 facility_name_index 判斷「這串字是不是專名」。

        只投影 name 欄位（19,528 筆約 512 KB），啟動時載入一次即可，
        不在對話路徑上查詢。
        """
        names: set[str] = set()
        try:
            for collection in self._name_collections():
                projection = {"name": 1, "aliases": 1} if self._supplemental_collection_getter is not None else {"name": 1}
                async for doc in collection.find({}, projection):
                    name = doc.get("name")
                    if name:
                        names.add(name)
                    names.update(alias for alias in doc.get("aliases", []) if isinstance(alias, str) and alias)
        except Exception as e:
            logger.error(f"MongoDB list_all_names failed: {e}", exc_info=True)
        return names

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

        try:
            doc = None
            for collection in self._name_collections():
                doc = await collection.find_one({"_id": object_id})
                if doc is not None:
                    break
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
