"""邀請 QR 圖片端點。

這支端點的行為與其他 API 不同，值得單獨守著：它一律回 200 與一張圖，
「查無此邀請」和「邀請已失效」刻意不分開——分開就成了枚舉有效邀請碼的
預言機，而回 404 會讓 Flex 卡片顯示破圖。
"""

from io import BytesIO
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.core.config import settings
from app.dependencies import get_family_tree_service
from app.main import app
from app.services.family.invite_qr_service import (
    EXPIRED_IMAGE_PATH,
    QR_PIXEL_SIZE,
    build_invite_url,
    render_invite_qr_png,
)

VALID_CODE = "abcDEF12_-x"
LIFF_ID = "1234567890-abcdefgh"


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def liff_id(monkeypatch):
    # .env 會被 conftest 載入，實際值因機器而異。釘死才有可預期的 URL。
    monkeypatch.setattr(settings, "LIFF_ID", LIFF_ID)
    return LIFF_ID


@pytest.fixture()
def usable_invitations():
    """把「邀請是否可用」換成可控的替身，回傳的是那個替身。"""
    service = AsyncMock()
    app.dependency_overrides[get_family_tree_service] = lambda: service
    yield service
    app.dependency_overrides.clear()


def _get_qr(client, code: str):
    return client.get(f"/api/family/invites/{code}/qr.png")


def test_serves_a_png_for_a_usable_invitation(client, liff_id, usable_invitations):
    usable_invitations.is_invitation_usable.return_value = True

    response = _get_qr(client, VALID_CODE)

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    # 路由要把使用者請求的那組邀請碼原封不動畫進圖裡。
    assert response.content == render_invite_qr_png(VALID_CODE)
    usable_invitations.is_invitation_usable.assert_awaited_once_with(VALID_CODE)


def test_qr_is_square_and_sized_for_the_flex_card(client, liff_id, usable_invitations):
    usable_invitations.is_invitation_usable.return_value = True

    image = Image.open(BytesIO(_get_qr(client, VALID_CODE).content))

    # 與失效圖同尺寸，Flex 卡片在兩者之間切換時版面不會跳動；並且要留在
    # Flex image component 的 1024x1024 上限之內。
    width, height = image.size
    assert width == height
    assert abs(width - QR_PIXEL_SIZE) <= QR_PIXEL_SIZE * 0.1
    assert width <= 1024


def test_serves_the_expired_image_when_the_invitation_is_not_usable(
    client, liff_id, usable_invitations
):
    usable_invitations.is_invitation_usable.return_value = False

    response = _get_qr(client, VALID_CODE)

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == EXPIRED_IMAGE_PATH.read_bytes()


def test_malformed_codes_never_reach_the_database(client, liff_id, usable_invitations):
    # 形狀不對就直接回失效圖，不查詢、也不進檔案系統。`..%2F` 那筆是路徑穿越，
    # 帶點的那筆會撞到路徑裡的 `.png` 字面量。
    for code in ["../../etc/passwd", "..%2Fapp%2Fmain.py", "short", "has space", ""]:
        response = _get_qr(client, code)

        assert response.status_code in (200, 404), code
        if response.status_code == 200:
            assert response.content == EXPIRED_IMAGE_PATH.read_bytes(), code

    usable_invitations.is_invitation_usable.assert_not_awaited()


def test_response_is_not_cached(client, liff_id, usable_invitations):
    # 有快取的話，邀請失效後拿到的還是舊 QR，「已失效」那張圖就永遠不會出現。
    usable_invitations.is_invitation_usable.return_value = True

    response = _get_qr(client, VALID_CODE)

    assert response.headers["cache-control"] == "no-store"


def test_endpoint_needs_no_authentication(client, liff_id, usable_invitations):
    # Flex Message 的圖是 LINE 的伺服器去抓的，帶不了使用者的 JWT。
    usable_invitations.is_invitation_usable.return_value = True

    response = client.get(
        f"/api/family/invites/{VALID_CODE}/qr.png",
        headers={"Authorization": ""},
    )

    assert response.status_code == 200


def test_qr_encodes_the_liff_url(liff_id):
    """掃描後要落在 LIFF 內的 /join，不是站台網址。

    這是整條路最容易寫錯、也最難從圖片上看出來的一段，所以直接針對組網址的
    純函式驗，不繞過圖片編碼。
    """
    assert (
        build_invite_url(VALID_CODE)
        == f"https://liff.line.me/{LIFF_ID}/join?code={VALID_CODE}"
    )


def test_missing_liff_id_fails_loudly_instead_of_drawing_a_broken_qr(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(settings, "LIFF_ID", "  ")

    # 連結那條路可以降級（前端會退回站台網址）……
    assert build_invite_url(VALID_CODE) is None

    # ……但 QR 不行：一張指向 `liff.line.me//join` 的圖會變成「掃了沒反應」的
    # 鬼故事，比直接失敗難查得多。
    with pytest.raises(HTTPException) as excinfo:
        render_invite_qr_png(VALID_CODE)

    assert excinfo.value.status_code == 500
