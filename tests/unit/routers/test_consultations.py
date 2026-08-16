from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.dependencies import (
    CurrentUser,
    get_consultation_download_token_service,
    get_consultation_service,
    get_current_user,
    get_family_tree_service,
)
from app.main import app
from app.models.chat_message import ChatMessage
from app.models.consultation import ConsultationSummary
from app.models.family_tree import FamilyMember, FamilyTree
from app.services.family.family_tree_service import FamilyTreeService


class FakeConsultationService:
    def __init__(
        self,
        summaries: list[ConsultationSummary] | None = None,
        messages: list[ChatMessage] | None = None,
    ) -> None:
        self.get_all_summaries = AsyncMock(return_value=summaries or [])
        self.get_raw_view = AsyncMock(return_value=messages or [])


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
def override_family_service():
    """注入一個只有 get_family_tree 被假掉的真 FamilyTreeService。

    刻意不用 AsyncMock 整包取代 service：要驗的正是 ensure_family_member 的判斷邏輯，
    整包 mock 掉的話 403 那條路根本不會被執行，測試就變成只在測 mock 自己。
    """

    def _override(member_ids: list[str], owner_id: str = "U123"):
        now = datetime.now(tz=timezone.utc)
        service = FamilyTreeService()
        service.get_family_tree = AsyncMock(
            return_value=FamilyTree(
                user_id=owner_id,
                family_members=[FamilyMember(user_id=m) for m in member_ids],
                created_at=now,
                updated_at=now,
            )
        )
        app.dependency_overrides[get_family_tree_service] = lambda: service
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

    response = client.get("/api/consultations/me/summary/downloadtoken")

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
            language="zh-TW",
            created_at=datetime(2026, 5, 26, 10, 11, 12),
        ),
        ConsultationSummary(
            line_id="U123",
            summary_date=date(2026, 5, 27),
            summary="5/27 摘要",
            language="en",
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
            "/api/consultations/me/summary/download?downloadToken=download-token"
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
            "language": "zh-TW",
            "created_at": "2026-05-26T10:11:12",
        },
        {
            "line_id": "U123",
            "summary_date": "2026-05-27",
            "summary": "5/27 摘要",
            "language": "en",
            "created_at": "2026-05-27T13:14:15",
        },
    ]
    fake_service.get_all_summaries.assert_awaited_once_with("U123")


# ── /{userId} 家庭授權 ────────────────────────────────────────────────────────
# 這兩支端點吐的是別人的諮詢內容，是全專案最敏感的資料。若只驗登入態不驗族譜，
# 任何持有自己 token 的人只要知道對方的 LINE userId 就能整份撈走，因此正反兩面都要蓋到。

def _sample_messages() -> list[ChatMessage]:
    return [
        ChatMessage(
            line_id="U_MEMBER",
            message_type="text",
            content="最近頭很痛",
            timestamp=datetime(2026, 5, 26, 10, 0, 0),
        )
    ]


def _sample_summaries() -> list[ConsultationSummary]:
    return [
        ConsultationSummary(
            line_id="U_MEMBER",
            summary_date=date(2026, 5, 26),
            summary="5/26 摘要",
            language="zh-TW",
            created_at=datetime(2026, 5, 26, 10, 11, 12),
        )
    ]


def test_get_member_summary_history_allows_family_member(
    client, override_current_user, override_consultation_service, override_family_service
):
    override_current_user("U123")
    fake_service = override_consultation_service(
        FakeConsultationService(summaries=_sample_summaries())
    )
    family_service = override_family_service(["U_MEMBER"])

    response = client.get("/api/consultations/U_MEMBER/allsummaries")

    assert response.status_code == 200
    assert response.json()[0]["summary"] == "5/26 摘要"
    family_service.get_family_tree.assert_awaited_once_with("U123")
    fake_service.get_all_summaries.assert_awaited_once_with("U_MEMBER")


def test_get_member_summary_history_rejects_non_family_member(
    client, override_current_user, override_consultation_service, override_family_service
):
    override_current_user("U123")
    fake_service = override_consultation_service(
        FakeConsultationService(summaries=_sample_summaries())
    )
    override_family_service(["U_SOMEONE_ELSE"])

    response = client.get("/api/consultations/U_STRANGER/allsummaries")

    assert response.status_code == 403
    assert "非家庭成員" in response.json()["detail"]
    # 授權必須擋在讀取之前，不能先撈出來再決定要不要回傳
    fake_service.get_all_summaries.assert_not_awaited()


def test_get_member_raw_consultations_allows_family_member(
    client, override_current_user, override_consultation_service, override_family_service
):
    override_current_user("U123")
    fake_service = override_consultation_service(
        FakeConsultationService(messages=_sample_messages())
    )
    family_service = override_family_service(["U_MEMBER"])

    response = client.get("/api/consultations/U_MEMBER/messages/raw")

    assert response.status_code == 200
    body = response.json()
    assert body["line_id"] == "U_MEMBER"
    assert body["view_type"] == "raw"
    assert body["messages"][0]["content"] == "最近頭很痛"
    family_service.get_family_tree.assert_awaited_once_with("U123")
    fake_service.get_raw_view.assert_awaited_once_with("U_MEMBER")


def test_get_member_raw_consultations_rejects_non_family_member(
    client, override_current_user, override_consultation_service, override_family_service
):
    override_current_user("U123")
    fake_service = override_consultation_service(
        FakeConsultationService(messages=_sample_messages())
    )
    override_family_service(["U_SOMEONE_ELSE"])

    response = client.get("/api/consultations/U_STRANGER/messages/raw")

    assert response.status_code == 403
    assert "非家庭成員" in response.json()["detail"]
    fake_service.get_raw_view.assert_not_awaited()


def test_get_member_raw_consultations_allows_self_without_family_lookup(
    client, override_current_user, override_consultation_service, override_family_service
):
    """查自己不該因為族譜是空的就被擋——也不該為此多打一次 DB。"""
    override_current_user("U123")
    fake_service = override_consultation_service(
        FakeConsultationService(messages=_sample_messages())
    )
    family_service = override_family_service([])

    response = client.get("/api/consultations/U123/messages/raw")

    assert response.status_code == 200
    family_service.get_family_tree.assert_not_awaited()
    fake_service.get_raw_view.assert_awaited_once_with("U123")
