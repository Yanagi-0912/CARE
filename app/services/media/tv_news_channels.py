"""記住長輩常看哪幾台。

台標認不出來時，「他常看的台」是我們唯一能用的先驗：同一筆搜尋結果，來自他
常看的台比來自隨便一台更可能是他這次看到的那一則，所以常看的台可以用比較低
的比對門檻（見 `tv_news_lookup.TvNewsArticleFinder.find`）。

記的是**次數**不是單一最愛：長輩不會只看一台。只在台別確定時才記——畫面認出
台標，或他自己回答「這是民視」。猜的不能記，猜錯會一路影響之後每一次比對。

讀寫都不得讓查核失敗：這是加值，資料庫不通就當作沒有偏好。
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

from app.repositories.user_profile_repository import UserProfileRepository

logger = logging.getLogger(__name__)


class TvNewsChannelMemory:
    def __init__(self, repository: Any = UserProfileRepository) -> None:
        self._repository = repository

    async def preferred(self, user_id: Optional[str]) -> Sequence[str]:
        if not user_id:
            return ()
        try:
            profile = await self._repository.get_user_profile(user_id)
            return tuple(self._repository.preferred_tv_channels(profile))
        except Exception:  # noqa: BLE001 - 沒有偏好只是門檻高一點
            logger.warning("讀不到常看的電視台，這次不用偏好", exc_info=True)
            return ()

    async def remember(self, user_id: Optional[str], channel: str) -> None:
        if not user_id or not channel:
            return
        try:
            await self._repository.record_tv_news_channel(user_id, channel)
        except Exception:  # noqa: BLE001 - 記不起來不影響這一次的回答
            logger.warning("記不住常看的電視台", exc_info=True)
