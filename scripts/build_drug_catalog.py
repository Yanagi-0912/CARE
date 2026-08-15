"""從食藥署開放資料建出本地藥證庫。

用途是給 DrugCatalogService 當比對字典，偵測藥袋辨識的錯讀。資料每 7 日更新，
沒有即時性需求，所以做成建置期產出的靜態檔——執行期不對外連線，辨識延遲
不受政府站台可用性影響。

用法：
    python -m scripts.build_drug_catalog [--output resources/drug_catalog.json]

主資料源是全部藥品許可證資料集（涵蓋所有核准藥證），藥品外觀資料集僅作補充。
反過來以外觀資料集為主會嚴重低估覆蓋率——它只有六千多筆。

未做：許可證資料集帶有「註銷狀態」與「有效日期」，目前不據以過濾。已註銷的
藥名留在庫裡仍是「這是一個真實藥名」的有效證據，而過濾需要先確認該欄位的
實際值域，猜錯會讓大量有效藥名被剔除。同資料集另有「適應症」與「主成分略述」，
可用來取代辨識結果中同名欄位（權威來源優於模型讀出的字串），值得後續評估。
"""

import argparse
import io
import json
import logging
import zipfile
from typing import Any, Iterable, Optional

import requests

logger = logging.getLogger(__name__)

# 全部藥品許可證資料集（data.gov.tw dataset 9122）
LICENCE_DATASET_URL = "https://data.fda.gov.tw/data/opendata/export/36/json"
# 藥品外觀資料集（data.gov.tw dataset 9120）
APPEARANCE_DATASET_URL = "https://data.fda.gov.tw/data/opendata/export/42/json"

DEFAULT_OUTPUT = "resources/drug_catalog.json"
DOWNLOAD_TIMEOUT_SECONDS = 180


def read_dataset_zip(payload: bytes) -> list[dict[str, Any]]:
    """解開 export 端點回傳的 ZIP 並解析其中的 JSON。

    端點的路徑叫 /json，回傳的卻是 ZIP；直接 json.loads 會得到看似編碼錯誤的
    UnicodeDecodeError，很容易被誤判成資料有問題。
    """
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        name = archive.namelist()[0]
        with archive.open(name) as member:
            return json.loads(member.read().decode("utf-8"))


def _clean(value: Any) -> str:
    """開放資料的欄位常是 null 而非空字串。"""
    if value is None:
        return ""
    return str(value).strip()


# 外觀資料集的中文欄名 → 藥證庫條目使用的鍵名，比照既有 license_number／
# name_zh／name_en 的命名風格。順序即輸出 JSON 的欄位順序。
_APPEARANCE_FIELD_MAP = {
    "外觀圖檔連結": "image_url",
    "形狀": "shape",
    "顏色": "color",
    "刻痕": "score_line",
    "標註一": "mark_one",
    "標註二": "mark_two",
    "外觀尺寸": "size",
}

_EMPTY_APPEARANCE_FIELDS = dict.fromkeys(_APPEARANCE_FIELD_MAP.values(), "")


def _index_appearance_fields(
    appearances: Iterable[dict[str, Any]],
) -> dict[str, dict[str, str]]:
    """許可證字號 → 外觀欄位。同證號出現多筆時取第一筆，跟品名合併的
    去重規則一致。"""
    by_licence: dict[str, dict[str, str]] = {}
    for row in appearances:
        license_number = _clean(row.get("許可證字號"))
        if not license_number or license_number in by_licence:
            continue
        by_licence[license_number] = {
            field: _clean(row.get(raw_key)) for raw_key, field in _APPEARANCE_FIELD_MAP.items()
        }
    return by_licence


def build_entries(
    licences: Iterable[dict[str, Any]],
    appearances: Iterable[dict[str, Any]],
) -> list[dict[str, str]]:
    """合併兩個資料集，輸出 DrugCatalogService 讀得懂的條目。

    以許可證字號去重；許可證資料集優先，外觀資料集只補許可證資料集沒有的品項。
    沒有證號、或中英文品名都空的資料列直接略過——它們對比對沒有貢獻，
    留著只會拉低相似度比對的品質。

    外觀欄位（外觀圖檔連結、形狀、顏色、刻痕、標註一／二、外觀尺寸）的
    附掛規則跟上面的品名補充規則各自獨立：不論這張證號的品名最終取自
    哪個資料集，只要外觀資料集有對應紀錄就貼上外觀欄位；沒有外觀記錄的
    證號則全部留空字串。這樣才不會讓「外觀資料集只補許可證沒有的品項」
    這條規則被誤套用到外觀欄位本身——那條規則管的是名稱該聽誰的，
    不是外觀資料該不該附掛。
    """
    appearances = list(appearances)
    by_licence: dict[str, dict[str, str]] = {}

    for source in (licences, appearances):
        for row in source:
            license_number = _clean(row.get("許可證字號"))
            name_zh = _clean(row.get("中文品名"))
            name_en = _clean(row.get("英文品名"))
            if not license_number:
                continue
            if not name_zh and not name_en:
                continue
            if license_number in by_licence:
                continue
            by_licence[license_number] = {
                "license_number": license_number,
                "name_zh": name_zh,
                "name_en": name_en,
            }

    appearance_by_licence = _index_appearance_fields(appearances)
    for license_number, entry in by_licence.items():
        entry.update(appearance_by_licence.get(license_number, _EMPTY_APPEARANCE_FIELDS))

    return list(by_licence.values())


def _download(url: str) -> list[dict[str, Any]]:
    logger.info("下載 %s", url)
    response = requests.get(url, timeout=DOWNLOAD_TIMEOUT_SECONDS)
    response.raise_for_status()
    return read_dataset_zip(response.content)


def main(output_path: Optional[str] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="建置本地藥證庫")
    parser.add_argument("--output", default=output_path or DEFAULT_OUTPUT)
    args = parser.parse_args()

    licences = _download(LICENCE_DATASET_URL)
    appearances = _download(APPEARANCE_DATASET_URL)
    entries = build_entries(licences, appearances)

    with open(args.output, "w", encoding="utf-8") as output_file:
        json.dump(entries, output_file, ensure_ascii=False)

    logger.info("寫入 %s 筆藥證至 %s", len(entries), args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
