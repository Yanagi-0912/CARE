import logging
import time

import requests

logger = logging.getLogger(__name__)

_DEFAULT_MAX_ATTEMPTS = 3
_DEFAULT_TIMEOUT_SECONDS = 10
_DEFAULT_RETRY_BACKOFF_SECONDS = 0.5


class LineIdTokenService:
    def __init__(
        self,
        verify_url: str = "https://api.line.me/oauth2/v2.1/verify",
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        retry_backoff_seconds: float = _DEFAULT_RETRY_BACKOFF_SECONDS,
    ):
        self._verify_url = verify_url
        self._max_attempts = max_attempts
        self._timeout_seconds = timeout_seconds
        self._retry_backoff_seconds = retry_backoff_seconds

    def verify(self, id_token: str, client_id: str) -> dict:
        last_exc: requests.RequestException | None = None

        for attempt in range(1, self._max_attempts + 1):
            try:
                response = requests.post(
                    self._verify_url,
                    data={"id_token": id_token, "client_id": client_id},
                    timeout=self._timeout_seconds,
                )
            except requests.RequestException as exc:
                last_exc = exc
                if attempt >= self._max_attempts:
                    break
                logger.warning(
                    "LINE ID token verify 連線失敗，準備重試 (%s/%s): %s",
                    attempt,
                    self._max_attempts,
                    exc,
                )
                time.sleep(self._retry_backoff_seconds * attempt)
                continue

            if response.status_code != 200:
                raise ValueError(
                    f"Invalid id_token: status={response.status_code}, body={response.text}"
                )

            return response.json()

        assert last_exc is not None
        raise last_exc
