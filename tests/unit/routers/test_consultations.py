from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.dependencies import (
    CurrentUser,
    get_consultation_download_token_service,
    get_consultation_service,
    get_current_user,
)
from app.main import app
from app.models.consultation import ConsultationSummary


class FakeConsultationService:
    def __init__(self, summaries: list[ConsultationSummary]) -> None:
        self.get_all_summaries = AsyncMock(return_value=summaries)


class FakeDownloadTokenService:
    def __init__(self, token: str = "download-token", user_id: str = "U123") -> None:
        self.token = token
        self.user_id = user_id

    def issue_for_user(self, line_user_id: str) -> tuple[str, int]:
        assert line_user_id == self.user_id
        return self.token, 300

    def decode_user_id(self, token: str) -> str:
        assert token == self.token
        return self.user_id


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def override_current_user():
    def _override(user_id: str = "U123"):
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            line_user_id=user_id
        )

    yield _override
    app.dependency_overrides.clear()


@pytest.fixture()
def override_consultation_service():
    def _override(service: FakeConsultationService):
        app.dependency_overrides[get_consultation_service] = lambda: service
        return service

    yield _override
    app.dependency_overrides.clear()


@pytest.fixture()
def override_download_token_service():
    def _override(service: FakeDownloadTokenService):
        app.dependency_overrides[get_consultation_download_token_service] = (
            lambda: service
        )
        return service

    yield _override
    app.dependency_overrides.clear()


def test_get_my_summary_download_token_returns_token(
    client,
    override_current_user,
    override_consultation_service,
    override_download_token_service,
):
    override_current_user("U123")
    override_consultation_service(FakeConsultationService(summaries=[]))
    override_download_token_service(FakeDownloadTokenService())

    response = client.get("/api/consultations/me/allsummaries/download-token")

    assert response.status_code == 200
    assert response.json() == {
        "downloadToken": "download-token",
        "expiresIn": 300,
    }


def test_download_my_summary_history_returns_json_attachment(
    client,
    override_consultation_service,
    override_download_token_service,
):
    summaries = [
        ConsultationSummary(
            line_id="U123",
            summary_date=date(2026, 5, 26),
            summary="5/26 摘要",
            created_at=datetime(2026, 5, 26, 10, 11, 12),
        ),
        ConsultationSummary(
            line_id="U123",
            summary_date=date(2026, 5, 27),
            summary="5/27 摘要",
            created_at=datetime(2026, 5, 27, 13, 14, 15),
        ),
    ]
    fake_service = override_consultation_service(
        FakeConsultationService(summaries=summaries)
    )
    override_download_token_service(FakeDownloadTokenService())

    fixed_now = datetime(2026, 5, 29, 13, 45, 59)
    with patch("app.routers.users.consultations.datetime") as mock_datetime:
        mock_datetime.now.return_value = fixed_now
        response = client.get(
            "/api/consultations/me/allsummaries/download?downloadToken=download-token"
        )

    assert response.status_code == 200
    assert response.headers["content-disposition"] == (
        'attachment; filename="CARE_consult_summart_20260529134559.json"'
    )
    assert response.headers["content-type"] == "application/json; charset=utf-8"
    assert response.json() == [
        {
            "line_id": "U123",
            "summary_date": "2026-05-26",
            "summary": "5/26 摘要",
            "created_at": "2026-05-26T10:11:12",
        },
        {
            "line_id": "U123",
            "summary_date": "2026-05-27",
            "summary": "5/27 摘要",
            "created_at": "2026-05-27T13:14:15",
        },
    ]
    fake_service.get_all_summaries.assert_awaited_once_with("U123")
