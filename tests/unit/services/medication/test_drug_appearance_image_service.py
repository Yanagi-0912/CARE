"""drug_appearance_image_service 的純函式測試。

一律以顯式參數（`image_dir`/`public_base_url`/`url_path`）餵入 tmp_path 與
假的 base url，不 monkeypatch 全域的 settings 單例——比照
tests/unit/services/medication/test_drug_catalog_service.py 以建構子參數
傳入固定資料集的作法。
"""

import logging
import re
from pathlib import Path

import pytest

from app.services.medication.drug_appearance_image_service import (
    resolve_drug_appearance_image_url,
    thumbnail_filename,
)

LICENSE_WITH_THUMBNAIL = "衛署藥製字第000001號"
LICENSE_WITHOUT_THUMBNAIL = "衛署藥製字第999999號"
BASE_URL = "https://care.example.com"
URL_PATH = "/drug-appearance"


def _seed_thumbnail(image_dir: Path, license_number: str) -> Path:
    image_dir.mkdir(parents=True, exist_ok=True)
    path = image_dir / thumbnail_filename(license_number)
    path.write_bytes(b"fake-jpeg-bytes")
    return path


def _resolve(license_number: str, image_dir: Path, **overrides) -> str | None:
    kwargs = {"public_base_url": BASE_URL, "url_path": URL_PATH, "image_dir": image_dir}
    kwargs.update(overrides)
    return resolve_drug_appearance_image_url(license_number, **kwargs)


def test_licence_with_thumbnail_on_disk_returns_url(tmp_path):
    _seed_thumbnail(tmp_path, LICENSE_WITH_THUMBNAIL)

    url = _resolve(LICENSE_WITH_THUMBNAIL, tmp_path)

    expected_filename = thumbnail_filename(LICENSE_WITH_THUMBNAIL)
    assert url == f"{BASE_URL}{URL_PATH}/{expected_filename}"


def test_licence_without_thumbnail_returns_none(tmp_path):
    # 目錄存在（另一張證號有縮圖），但查詢的這張沒有
    _seed_thumbnail(tmp_path, LICENSE_WITH_THUMBNAIL)

    url = _resolve(LICENSE_WITHOUT_THUMBNAIL, tmp_path)

    assert url is None


def test_url_contains_no_sequential_or_predictable_identifier_and_no_pii(tmp_path):
    """spec「靜態圖片資源的識別碼」：路徑不可枚舉、不含使用者或用藥資訊。"""
    _seed_thumbnail(tmp_path, LICENSE_WITH_THUMBNAIL)

    url = _resolve(LICENSE_WITH_THUMBNAIL, tmp_path)

    assert url is not None
    assert "?" not in url  # 不帶查詢字串，不可能夾帶使用者或藥品 id
    assert LICENSE_WITH_THUMBNAIL not in url  # 檔名不是證號本身

    filename = url.rsplit("/", 1)[-1]
    assert re.fullmatch(r"[0-9a-f]{16}\.jpg", filename)  # 是雜湊，不是遞增序號


def test_two_licences_produce_unrelated_filenames(tmp_path):
    """雜湊值之間不該有可預期的順序關係，用兩個「看起來相鄰」的證號驗證。"""
    a = thumbnail_filename("衛署藥製字第000001號")
    b = thumbnail_filename("衛署藥製字第000002號")

    assert a != b
    assert a[:8] != b[:8]  # 雪崩效應：相鄰輸入的雜湊前綴不該相近


@pytest.mark.parametrize(
    "license_number",
    ["", "   ", "not-a-real-licence-number", "🙂" * 20, "\x00\x01"],
)
def test_unknown_empty_or_malformed_licence_returns_none_without_raising(
    tmp_path, license_number
):
    _seed_thumbnail(tmp_path, LICENSE_WITH_THUMBNAIL)

    url = _resolve(license_number, tmp_path)

    assert url is None


def test_missing_image_directory_returns_none_without_raising(tmp_path, caplog):
    missing_dir = tmp_path / "does-not-exist"

    with caplog.at_level(logging.ERROR):
        url = _resolve(LICENSE_WITH_THUMBNAIL, missing_dir)

    assert url is None
    assert any("藥丸縮圖目錄不存在" in record.message for record in caplog.records)


def test_missing_image_directory_degrades_for_every_licence(tmp_path):
    """缺目錄時任何證號都回 None，而不是只有部分——mirrors「藥證庫缺席」的降級方向。"""
    missing_dir = tmp_path / "does-not-exist"

    assert _resolve(LICENSE_WITH_THUMBNAIL, missing_dir) is None
    assert _resolve(LICENSE_WITHOUT_THUMBNAIL, missing_dir) is None
    assert _resolve("", missing_dir) is None


def test_thumbnail_exists_but_public_base_url_unset_returns_none(tmp_path):
    """PUBLIC_BASE_URL 未設定時無法組出可用 URL，寧可回 None 也不回半成品。"""
    _seed_thumbnail(tmp_path, LICENSE_WITH_THUMBNAIL)

    url = _resolve(LICENSE_WITH_THUMBNAIL, tmp_path, public_base_url="")

    assert url is None
