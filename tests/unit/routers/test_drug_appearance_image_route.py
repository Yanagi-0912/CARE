"""藥丸縮圖靜態路由：比照 tests/unit/routers/test_tts.py 用 TestClient 打真實
路由與真實檔案，不 monkeypatch。resources/drug_appearance/ 已提交 6,267 張
縮圖（見 tests/unit/resources/test_drug_appearance_images.py 的守門），任取
一張驗證路由真的能把它服務出來。
"""

from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app

client = TestClient(app)

IMAGE_DIR = Path(settings.DRUG_APPEARANCE_IMAGE_DIR)


def _any_committed_thumbnail() -> Path:
    return next(IMAGE_DIR.glob("*.jpg"))


def test_drug_appearance_image_route_serves_a_committed_thumbnail():
    thumbnail = _any_committed_thumbnail()

    response = client.get(f"/drug-appearance/{thumbnail.name}")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/jpeg")
    assert response.content == thumbnail.read_bytes()


def test_drug_appearance_image_route_404s_for_unknown_filename():
    response = client.get("/drug-appearance/0000000000000000.jpg")

    assert response.status_code == 404


def test_drug_appearance_image_route_rejects_non_hash_filenames():
    # 不符合「16 碼十六進位 + .jpg」形狀的請求一律 404，不進檔案系統查找
    for filename in ["not-a-hash.jpg", "0035efa548799046.png", "0035EFA548799046.jpg"]:
        assert client.get(f"/drug-appearance/{filename}").status_code == 404


def test_drug_appearance_image_route_blocks_path_traversal():
    response = client.get("/drug-appearance/..%2Fapp%2Fmain.py")

    assert response.status_code == 404
