import logging
from typing import Any, Optional
from app.schemas import MedicalFacility
from app.db.mongodb import MongoDBManager

logger = logging.getLogger(__name__)


class UserSessionStore:
    def __init__(self) -> None:
        # 未來展望: 目前使用記憶體 (dict) 原生儲存使用者狀態。
        # 若未來伺服器開啟多個 worker，或是部署到無伺服器架構 (如 Cloud Run, AWS Lambda)，
        # 這裡的狀態將無法跨系統共享，可能會導致狀態遺失。
        # 建議未來改用 Redis 或資料庫（如 PostgreSQL）來進行集中式的狀態管理。
        self._store: dict[str, str] = {}

    def get(self, user_id: str) -> str:
        return self._store.get(user_id, "IDLE")

    def set(self, user_id: str, state: str) -> None:
        self._store[user_id] = state
        logger.debug(f"User {user_id} state → {state}")

    def clear(self, user_id: str) -> None:
        self._store.pop(user_id, None)
        logger.debug(f"User {user_id} state → IDLE (cleared)")


session_store = UserSessionStore()


class MedicalService:
    def request_location(self, user_id: str) -> dict[str, Any]:
        session_store.set(user_id, "WAITING_LOCATION")
        logger.info(f"Generating location request payload for user {user_id}")
        payload: dict[str, Any] = {
            "type": "text",
            "text": "請傳送您目前的位置資訊，我將為您尋找附近的醫療院所 🏥",
            "quickReply": {
                "items": [
                    {
                        "type": "action",
                        "action": {
                            "type": "location",
                            "label": "傳送位置資訊",
                        },
                    }
                ]
            },
        }
        return payload

    async def handle_location(
        self, user_id: str, lat: float, lng: float
    ) -> Optional[list[MedicalFacility]]:
        """
        檢查使用者是否處於 WAITING_LOCATION 狀態，
        若是則查詢附近醫療院所；若否則回傳 None 表示忽略。
        """
        if session_store.get(user_id) != "WAITING_LOCATION":
            logger.warning(
                f"User {user_id} sent location but was not in WAITING_LOCATION state, ignoring."
            )
            return None

        session_store.clear(user_id)
        return await self.find_nearby_hospitals(lat, lng)

    async def find_nearby_hospitals(
        self,
        lat: float,
        lng: float,
        radius_meters: int = 1000,
        limit: int = 5,
    ) -> list[MedicalFacility]:
        logger.info(
            f"Searching hospitals near ({lat}, {lng}) "
            f"within {radius_meters}m, limit={limit}"
        )
        
        collection = MongoDBManager.get_medical_collection()
        
        pipeline = [
            {
                "$geoNear": {
                    "near": {"type": "Point", "coordinates": [lng, lat]},
                    "distanceField": "distance_calculated",
                    "maxDistance": radius_meters,
                    "spherical": True
                }
            },
            {"$limit": limit}
        ]
        
        results = []
        try:
            # 透過 async for 尋訪游標
            async for doc in collection.aggregate(pipeline):
                facility = MedicalFacility(
                    id=str(doc["_id"]),
                    name=doc.get("name", "未知名稱"),
                    latitude=doc.get("latitude", 0.0),
                    longitude=doc.get("longitude", 0.0),
                    address=doc.get("address", "暫無地址資訊"),
                    phone=doc.get("phone") or "暫無聯絡電話",
                    type=doc.get("type", "醫療院所"),
                    distance_meters=doc.get("distance_calculated", 0.0)
                )
                results.append(facility)
                
        except Exception as e:
            logger.error(f"MongoDB geospatial query failed: {e}", exc_info=True)
            
        return results


medical_service = MedicalService()
