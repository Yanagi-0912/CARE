from datetime import datetime, timedelta, timezone

import jwt  # type: ignore[import-not-found]


class AppJwtService:
    def __init__(
        self,
        secret: str,
        algorithm: str,
        expires_minutes: int,
        issuer: str = "care-backend",
    ):
        self._secret = secret
        self._algorithm = algorithm
        self._expires_minutes = max(expires_minutes, 1)
        self._issuer = issuer

    def issue_for_user(self, line_user_id: str) -> tuple[str, int]:
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=self._expires_minutes)

        token = jwt.encode(
            {
                "sub": line_user_id,
                "iss": self._issuer,
                "iat": int(now.timestamp()),
                "exp": int(expires_at.timestamp()),
            },
            self._secret,
            algorithm=self._algorithm,
        )
        return token, self._expires_minutes * 60

    def decode_user_id(self, token: str) -> str:
        payload = jwt.decode(
            token,
            self._secret,
            algorithms=[self._algorithm],
            issuer=self._issuer,
        )

        line_user_id = payload.get("sub")
        if not line_user_id:
            raise ValueError("JWT payload missing sub")
        return line_user_id
