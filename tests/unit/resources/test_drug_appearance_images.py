"""守住已提交的藥丸照片縮圖。

這批縮圖由 `python -m scripts.build_drug_catalog --fetch-images` 產生並提交進
repo（見 `openspec/changes/drug-appearance-photo/design.md` 決策 2：執行期不
對外連線，縮圖是建置期產出的靜態資源）。它壞掉或消失時，呼叫端只是拿不到
`license_number` 對應的縮圖檔——這條路徑原本就要容忍照片缺席（spec「照片
缺席時的降級」），於是介面照常顯示純文字版面，**不會有任何地方報錯**。這正
是最危險的失敗模式：外觀圖檔集體消失或損毀，症狀只是「這批藥都沒有照片」，
跟資料集本來就沒收錄外觀資料長得一模一樣，沒有測試把關就沒人會發現。

所以守門放在這裡：縮圖目錄、內容、檔名與藥證庫的對應關係有問題時，讓測試
大聲失敗，而不是任由這個能力無聲地退化成「一張照片都沒有」。

這份檔案本身出過這種事：早期版本用固定抽樣（sorted 後每隔一段取樣 30 張，
每次執行結果都相同）驗證解碼與尺寸，把
`resources/drug_appearance/0035efa548799046.jpg` 截斷成 0 byte 後整批測試
仍是全綠——抽樣永遠不會選到它。全量解碼 6,267 張實測約 1.4 秒（PIL），
沒有效能理由需要抽樣，所以改成每一張都驗，見
`test_all_files_decode_and_are_160x160`。
"""

import hashlib
import json
from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[3]
CATALOG = REPO_ROOT / "resources" / "drug_catalog.json"
IMAGE_DIR = REPO_ROOT / "resources" / "drug_appearance"

# 食藥署藥品外觀資料集實測約 6,273 筆帶圖檔連結，其中 6,267 張下載成功
# （少數幾筆因原站台個別失敗，佔比不到 0.1%，屬預期中的容錯）。訂在 5,000
# 是為了擋住「抓到一小段就中斷」「檔名規則改掉導致大量檔案對不上」這類
# 壞掉的產出，而不是把測試綁死在某個當下的筆數——外觀資料集會隨每 7 日
# 的更新增減。
MINIMUM_FILES = 5_000
THUMBNAIL_PX = 160


def _license_numbers_with_image_url() -> set[str]:
    entries = json.loads(CATALOG.read_text(encoding="utf-8"))
    return {
        entry["license_number"]
        for entry in entries
        if str(entry.get("image_url", "")).startswith("http")
    }


def _expected_filenames() -> set[str]:
    """帶外觀圖檔連結的藥證，其縮圖「應該」使用的檔名——跟
    `scripts.build_drug_catalog.thumbnail_filename` 用同一條規則獨立算一次，
    而不是直接呼叫該函式，這樣才是真的在核對「產出物」跟「規則」兩者
    是否仍然一致，不會因為規則本身壞掉而測不出來。
    """
    return {
        hashlib.sha256(license_number.encode("utf-8")).hexdigest()[:16] + ".jpg"
        for license_number in _license_numbers_with_image_url()
    }


def test_image_directory_exists_and_has_plausible_count():
    assert IMAGE_DIR.is_dir(), (
        f"{IMAGE_DIR} 不存在。執行 python -m scripts.build_drug_catalog --fetch-images 產生它。"
    )
    files = list(IMAGE_DIR.glob("*.jpg"))
    assert len(files) >= MINIMUM_FILES, (
        f"僅有 {len(files)} 張縮圖，低於下限 {MINIMUM_FILES}，像是抓取中斷或目錄搞錯"
    )


def test_all_files_decode_and_are_160x160():
    """解碼**每一張**已提交的縮圖，而不是只看副檔名或檔案大小——0 byte 或
    半途寫壞的檔案，光靠存在與否測不出來。

    這裡刻意不抽樣：全量解碼 6,267 張實測約 1.4 秒，沒有效能理由要犧牲
    覆蓋率換取速度，而抽樣正是本檔案模組說明裡「集體損毀卻無人發現」
    這個失敗模式的具體重現——固定抽樣一次就會漏掉抽樣點以外的所有壞檔，
    而且是永遠、確定地漏掉同一批。"""
    files = sorted(IMAGE_DIR.glob("*.jpg"))
    assert files, "縮圖目錄是空的"

    for path in files:
        with Image.open(path) as image:
            image.load()  # 強制完整解碼，不只是讀檔頭
            assert image.format == "JPEG", f"{path.name} 不是有效的 JPEG"
            assert image.size == (THUMBNAIL_PX, THUMBNAIL_PX), (
                f"{path.name} 尺寸為 {image.size}，預期 {THUMBNAIL_PX}x{THUMBNAIL_PX}"
            )


def test_filenames_correspond_to_licences_with_image_url_and_have_no_orphans():
    """檔名必須是「有外觀圖檔連結的藥證」證號雜湊，且沒有孤兒檔案。

    孤兒檔案（檔名對不上任何帶圖藥證）是路徑可被公開讀取的靜態資源，
    若其中混進了不對應任何藥證的檔案，代表產出流程有雜訊沒濾乾淨；
    覆蓋率過低則代表抓取半途而廢，兩者都要在這裡擋下來，而不是等到
    使用者看見對不上的照片或永遠缺照片才發現。
    """
    expected = _expected_filenames()
    actual = {path.name for path in IMAGE_DIR.glob("*.jpg")}

    orphans = actual - expected
    assert not orphans, (
        f"有 {len(orphans)} 個縮圖檔名對不上任何帶圖藥證，例如 {sorted(orphans)[:5]}"
    )

    coverage = len(actual & expected) / len(expected)
    assert coverage >= 0.95, (
        f"僅 {coverage:.1%} 的帶圖藥證有對應縮圖，遠低於預期——像是抓取大量失敗"
    )
