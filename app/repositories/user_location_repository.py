# 使用者最後已知位置的暫存快取，僅存活於單一 process 記憶體中，非持久化資料庫。
# 用途：作為「選項B保底邏輯」，當 agent 未能從對話中帶出座標時，提供 fallback 位置。
import time


class UserLocationRepository:
    
    _ttl_seconds: int = 600 #暫存使用者位置10分鐘
    _cache: dict[str, tuple[float, float, float]] = {}
    

    @classmethod
    async def save_location(cls, user_id: str, lat: float, lng: float) -> None:
        expire_at = time.time() + cls._ttl_seconds
        cls._cache[user_id] = (lat, lng, expire_at)

    @classmethod
    async def get_last_location(cls, user_id: str) -> tuple[float, float] | None:
        entry = cls._cache.get(user_id)
        if entry is None:
            return None
        
        lat, lng, expire_at = entry
        if time.time() > expire_at:
            del cls._cache[user_id]
            return None
            
        return lat, lng