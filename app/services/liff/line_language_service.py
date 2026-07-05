import logging
from typing import Callable, Optional

import requests

logger = logging.getLogger(__name__)


class LineLanguageService:
    """透過 LINE Messaging API 查詢使用者的 language 設定。

    只在「新使用者第一次登入」時被呼叫一次，用來決定預設語言，
    之後一律以資料庫的值為準，不會再被這支服務覆蓋
    （避免蓋掉使用者在前端手動選擇的語言）。
    language 只有 Messaging API 的 Bot Profile 有提供，
    ID token 裡沒有這個欄位。
    """

    def __init__(self, get_access_token: Callable[[], str]) -> None:
        self._get_access_token = get_access_token

    def get_language(self, line_user_id: str) -> Optional[str]:
        try:
            access_token = self._get_access_token()
        except Exception as exc:
            logger.warning("取得 LINE access token 失敗，略過 language 查詢: %s", exc)
            return None

        url = f"https://api.line.me/v2/bot/profile/{line_user_id}"
        try:
            response = requests.get(
                url,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
        except requests.RequestException as exc:
            logger.warning("呼叫 LINE Profile API 失敗: %s", exc)
            return None

        if response.status_code != 200:
            logger.warning(
                "LINE Profile API 回傳非 200: status=%s body=%s",
                response.status_code,
                response.text,
            )
            return None

        return response.json().get("language")
