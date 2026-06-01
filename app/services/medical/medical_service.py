import logging
import urllib.parse
from app.schemas import MedicalFacility
from app.db.mongodb import MongoDBManager

logger = logging.getLogger(__name__)

NO_FACILITY_MESSAGE = (
    "抱歉，您附近 5 公里內暫時找不到醫療院所資料。\n功能仍在建置中，敬請期待！"
)


def format_facility_list(facilities: list[MedicalFacility]) -> str:
    # 將醫療院所列表格式化為使用者可讀的純文字
    lines = [f"為您找到附近 {len(facilities)} 間醫療院所：\n"]
    for i, f in enumerate(facilities, 1):
        dist = (
            f"（{f.distance_meters:.0f} 公尺）" if f.distance_meters is not None else ""
        )
        escaped_addr = urllib.parse.quote(f.address)
        map_url = f"https://www.google.com/maps/search/?api=1&query={escaped_addr}"
        lines.append(f"{i}. {f.name}{dist}\n   地址：{f.address}\n   地圖連結：{map_url}")
    return "\n".join(lines)


class MedicalService:
    async def find_nearby_hospitals(
        self,
        lat: float,
        lng: float,
        radius_meters: int = 5000,
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
                    "spherical": True,
                }
            },
            {"$limit": limit},
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
                    distance_meters=doc.get("distance_calculated", 0.0),
                )
                results.append(facility)

        except Exception as e:
            logger.error(f"MongoDB geospatial query failed: {e}", exc_info=True)

        return results


medical_service = MedicalService()
