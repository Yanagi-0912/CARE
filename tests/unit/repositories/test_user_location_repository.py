import time
import pytest
from app.repositories.user_location_repository import UserLocationRepository


@pytest.mark.asyncio
async def test_user_location_repository_save_and_get():
    await UserLocationRepository.save_location("U123", 25.033, 121.565)
    location = await UserLocationRepository.get_last_location("U123")
    assert location == (25.033, 121.565)


@pytest.mark.asyncio
async def test_user_location_repository_expired():
    await UserLocationRepository.save_location("U456", 25.033, 121.565)
    UserLocationRepository._cache["U456"] = (25.033, 121.565, time.time() - 1)
    location = await UserLocationRepository.get_last_location("U456")
    assert location is None
