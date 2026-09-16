"""看診錄音 API：驗同意欄位必填、權限走 SENSITIVE、音檔不落地、404 不洩漏存在性。"""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.dependencies import (
    CurrentUser,
    get_clinic_transcript_service,
    get_current_user,
    get_family_authorization_service,
)
from app.main import app
from app.models.clinic_transcript import ClinicVisitRecord

client = TestClient(app)

_ME = "U_ME"
_ELDER = "U_ELDER"


def _record(**kwargs) -> ClinicVisitRecord:
    payload = {
        "id": "rec1",
        "user_id": _ME,
        "created_by_user_id": _ME,
        "consent": "doctor_agreed",
        **kwargs,
    }
    return ClinicVisitRecord(**payload)


@pytest.fixture()
def service():
    fake = AsyncMock()
    fake.start.return_value = _record()
    fake.list_records.return_value = [_record()]
    fake.get_record.return_value = _record()
    fake.delete_record.return_value = True
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(line_user_id=_ME)
    app.dependency_overrides[get_clinic_transcript_service] = lambda: fake
    yield fake
    app.dependency_overrides.clear()


@pytest.fixture()
def authz():
    fake = AsyncMock()
    app.dependency_overrides[get_family_authorization_service] = lambda: fake
    yield fake


def _upload(consent="doctor_agreed", content_type="audio/webm", data=b"audio-bytes", **form):
    return client.post(
        "/api/clinic-visits",
        files={"file": ("visit.webm", data, content_type)},
        data={"consent": consent, **form},
    )


def test_上傳後立刻回處理中的紀錄(service, authz):
    response = _upload()
    assert response.status_code == 202
    assert response.json()["status"] == "processing"


def test_同意欄位必填且只收兩種值(service, authz):
    assert _upload(consent="").status_code == 422
    assert _upload(consent="whatever").status_code == 422
    service.start.assert_not_called()


def test_沒有同意欄位就送不出去(service, authz):
    """一個有預設值的同意欄位，等於沒徵詢過也會存進一筆看起來徵詢過的紀錄。"""
    response = client.post(
        "/api/clinic-visits", files={"file": ("v.webm", b"x", "audio/webm")}
    )
    assert response.status_code == 422


def test_自己複述也是合法的同意方式(service, authz):
    """醫師不同意時的第二條路，功能要還活著。"""
    assert _upload(consent="self_recap").status_code == 202
    assert service.start.await_args.kwargs["consent"] == "self_recap"


def test_非音訊檔直接拒絕(service, authz):
    assert _upload(content_type="image/png").status_code == 415
    service.start.assert_not_called()


def test_空檔案拒絕(service, authz):
    assert _upload(data=b"").status_code == 422


def test_幫家人錄要走_SENSITIVE_的寫入權(service, authz):
    _upload(target_user_id=_ELDER)
    authz.authorize.assert_awaited_once()
    args = authz.authorize.await_args.args
    assert args[0] == _ME and args[1] == _ELDER
    assert args[2] == "SENSITIVE" and args[3] == "WRITE"
    # 嚴格判定：影子模式的「在族譜裡就放行」會讓只有一般讀取權的家人看到診間對話。
    assert authz.authorize.await_args.kwargs["has_legacy_equivalent"] is False


def test_錄自己的不必問權限(service, authz):
    _upload()
    authz.authorize.assert_not_awaited()


def test_查別人的清單要走_SENSITIVE_讀取權(service, authz):
    client.get("/api/clinic-visits", params={"target_user_id": _ELDER})
    args = authz.authorize.await_args.args
    assert args[2] == "SENSITIVE" and args[3] == "READ"


def test_查不到的紀錄回_404(service, authz):
    """不存在與不屬於這位使用者統一回 404，否則會變成探測他人 record_id 的管道。"""
    service.get_record.return_value = None
    assert client.get("/api/clinic-visits/nope").status_code == 404


def test_刪除成功回_204(service, authz):
    assert client.delete("/api/clinic-visits/rec1").status_code == 204


def test_刪不存在的回_404(service, authz):
    service.delete_record.return_value = False
    assert client.delete("/api/clinic-visits/rec1").status_code == 404


def test_回傳的紀錄不含任何音檔欄位(service, authz):
    """音檔轉完就刪，資料庫裡從來沒有它，API 更不該出現。"""
    body = _upload().json()
    assert not [key for key in body if "audio" in key or "file" in key]
