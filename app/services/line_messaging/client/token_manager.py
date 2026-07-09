import logging
from datetime import datetime, timedelta
from typing import Optional

import requests

from app.services.line_messaging.shared.errors import LineTokenError

logger = logging.getLogger(__name__)


class LineTokenManager:
    def __init__(self, channel_id: str | None, channel_secret: str | None):
        self.channel_id = channel_id
        self.channel_secret = channel_secret
        self._access_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None

    def get_token(self) -> str:
        if self._is_token_valid():
            logger.debug("Using cached LINE access token")
            return self._access_token
        logger.info("LINE access token is missing or expired; fetching a new one.")
        return self._fetch_new_token()

    def _is_token_valid(self) -> bool:
        if not self._access_token or not self._token_expires_at:
            return False
        buffer_time = timedelta(minutes=5)
        return datetime.now() < (self._token_expires_at - buffer_time)

    def _fetch_new_token(self) -> str:
        if not self.channel_id or not self.channel_secret:
            raise LineTokenError(
                "LINE_CHANNEL_ID and LINE_CHANNEL_SECRET must be configured."
            )

        url = "https://api.line.me/oauth2/v3/token"
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        data = {
            "grant_type": "client_credentials",
            "client_id": self.channel_id,
            "client_secret": self.channel_secret,
        }

        try:
            response = requests.post(url, headers=headers, data=data, timeout=10)
            response.raise_for_status()
            result = response.json()
            access_token = result.get("access_token")
            expires_in = result.get("expires_in", 2592000)

            if not access_token:
                raise LineTokenError("LINE token response did not include access_token")

            self._access_token = access_token
            self._token_expires_at = datetime.now() + timedelta(seconds=expires_in)
            return access_token

        except requests.exceptions.RequestException as exc:
            error_msg = f"Failed to fetch LINE access token: {exc}"
            if getattr(exc, "response", None) is not None:
                error_msg += f"\nLINE response: {exc.response.text}"
            logger.error(error_msg)
            raise LineTokenError(error_msg) from exc
