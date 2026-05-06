import requests


class LineIdTokenService:
    def __init__(self, verify_url: str = "https://api.line.me/oauth2/v2.1/verify"):
        self._verify_url = verify_url

    def verify(self, id_token: str, client_id: str) -> dict:
        response = requests.post(
            self._verify_url,
            data={"id_token": id_token, "client_id": client_id},
            timeout=10,
        )

        if response.status_code != 200:
            raise ValueError(
                f"Invalid id_token: status={response.status_code}, body={response.text}"
            )

        return response.json()