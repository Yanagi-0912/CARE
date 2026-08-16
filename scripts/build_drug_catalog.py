"""從食藥署開放資料建出本地藥證庫。

用途是給 DrugCatalogService 當比對字典，偵測藥袋辨識的錯讀。資料每 7 日更新，
沒有即時性需求，所以做成建置期產出的靜態檔——執行期不對外連線，辨識延遲
不受政府站台可用性影響。

用法：
    python -m scripts.build_drug_catalog [--output resources/drug_catalog.json]

    # 另外下載藥丸照片並落地為縮圖（預設關閉，見 README「藥品外觀縮圖」）：
    python -m scripts.build_drug_catalog --fetch-images [--image-dir resources/drug_appearance]

主資料源是全部藥品許可證資料集（涵蓋所有核准藥證），藥品外觀資料集僅作補充。
反過來以外觀資料集為主會嚴重低估覆蓋率——它只有六千多筆。

未做：許可證資料集帶有「註銷狀態」與「有效日期」，目前不據以過濾。已註銷的
藥名留在庫裡仍是「這是一個真實藥名」的有效證據，而過濾需要先確認該欄位的
實際值域，猜錯會讓大量有效藥名被剔除。同資料集另有「適應症」與「主成分略述」，
可用來取代辨識結果中同名欄位（權威來源優於模型讀出的字串），值得後續評估。
"""

import argparse
import concurrent.futures as cf
import hashlib
import io
import json
import logging
import os
import shutil
import subprocess
import threading
import time
import urllib.request
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


# ── 藥丸照片抓取（--fetch-images，預設關閉）──────────────────────────────
#
# spec「外觀資料的來源與建置期落地」：照片 SHALL 於建置期下載並存為專案
# 自有靜態資源，執行期 SHALL NOT 連向 mcp.fda.gov.tw。以下把驗證用的
# 一次性抓取腳本折進建表腳本，成為往後每 7 日資料更新時的正式路徑，
# 細節見 design.md 決策 2、3、6。

# mcp.fda.gov.tw 對 Python 預設 UA 回 403 Forbidden；資料集主機
# data.fda.gov.tw 不擋，所以「建表腳本平常能跑」不代表圖檔主機也放行——
# 必須明確帶瀏覽器 UA，否則整批照片會全部失敗，而且看起來像照片消失了。
_IMAGE_FETCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}
DEFAULT_IMAGE_DIR = "resources/drug_appearance"
IMAGE_THUMBNAIL_PX = 160
IMAGE_THUMBNAIL_QUALITY = 82
# 實測原圖平均 3.7 MB、最大 37.8 MB；訂在 60 MB 是留餘裕擋住異常大檔，
# 而不是把上限貼著實測最大值——同時搭配 Content-Length 與實讀長度雙重
# 檢查，避免主機謊報標頭或以 chunked 編碼繞過。
IMAGE_MAX_BYTES = 60 * 1024 * 1024
IMAGE_FETCH_TIMEOUT_SECONDS = 120
# 轉檔是本機運算（縮放＋補白＋重新編碼一張最大 60 MB 的原圖），正常應在
# 一兩秒內完成；訂 30 秒是留給偶發的系統負載尖峰，而不是預期會用到——
# 沒有這個上限，卡住的 magick 行程會讓一個 worker thread 永久停擺，
# 且不留下任何診斷線索。
IMAGE_CONVERT_TIMEOUT_SECONDS = 30
IMAGE_FETCH_SLEEP_SECONDS = 0.35
IMAGE_FETCH_WORKERS = 3
_IMAGE_PROGRESS_EVERY = 250


def _require_magick(path: Optional[str] = None) -> None:
    """抓圖前先確認 `magick` 執行檔存在，在任何網路請求之前就失敗。

    沒有這道檢查時，`magick` 缺席只會在逐一轉檔時才被發現：
    `_fetch_and_thumbnail_one` 的 `except Exception` 吞下 `FileNotFoundError`
    並記一行帶完整 traceback 的紀錄，但下載在轉檔之前就已經發生——六千多次
    對 mcp.fda.gov.tw 的請求全部白費，而限速對象正是這個主機（design.md
    決策 3）。

    `path` 只給測試用：覆寫 `shutil.which` 要搜尋的目錄，讓「PATH 上沒有
    magick」這個情境不必真的更動使用者的 PATH 環境變數就能重現。
    """
    if shutil.which("magick", path=path) is None:
        raise RuntimeError(
            "找不到 ImageMagick 的 `magick` 執行檔——縮圖流程要靠它把原圖轉成 "
            f"{IMAGE_THUMBNAIL_PX}x{IMAGE_THUMBNAIL_PX}。macOS 可用 "
            "`brew install imagemagick` 安裝，見 README「藥品外觀縮圖」。"
        )


def thumbnail_filename(license_number: str) -> str:
    """縮圖檔名為證號 SHA-256 的前 16 字元。

    不可枚舉、不含使用者或用藥資訊，是靜態圖片對外路徑的識別碼（spec
    「靜態圖片資源的識別碼」、design.md 決策 4）。純函式，不碰網路或檔案
    系統，可直接單元測試。
    """
    return hashlib.sha256(license_number.encode("utf-8")).hexdigest()[:16] + ".jpg"


def image_fetch_targets(entries: Iterable[dict[str, str]]) -> list[tuple[str, str]]:
    """從建好的條目篩出「有證號且有外觀圖檔連結」的 (license_number, image_url)。

    純函式：只讀已經在記憶體裡的條目，不碰網路或檔案系統，可直接單元測試。
    """
    return [
        (entry["license_number"], entry["image_url"])
        for entry in entries
        if entry.get("license_number") and str(entry.get("image_url", "")).startswith("http")
    ]


def pending_image_targets(
    targets: Iterable[tuple[str, str]], image_dir: str
) -> list[tuple[str, str, str]]:
    """篩掉縮圖已存在的目標，回傳待抓的 (license_number, image_url, 目的路徑)。

    這是讓抓取可中斷續跑、日後只抓新增藥證的關鍵：已存在的縮圖一律跳過，
    不重新下載（design.md 決策 3）。只做檔案是否存在的判斷，不發網路
    請求，可用真實的暫存目錄單元測試，不必 monkeypatch。
    """
    pending = []
    for license_number, image_url in targets:
        out_path = os.path.join(image_dir, thumbnail_filename(license_number))
        if not os.path.exists(out_path):
            pending.append((license_number, image_url, out_path))
    return pending


def _fetch_and_thumbnail_one(image_url: str, out_path: str) -> str:
    """下載單張原圖、縮成 160×160 置中補白正方形、寫入 out_path。

    原圖只落地成同目錄下的暫存檔，轉檔完成或失敗都立刻刪除——全量原圖
    合計約 20 GB，峰值磁碟只能有一張原圖在場（design.md 決策 2）。等比
    縮放、置中補白、保留尺規、不裁切：消歧介面的候選常是同名藥，同色
    同形時尺規顯示的長度差是唯一能分辨的線索，裁掉尺規會讓這類候選變成
    不可分辨（design.md 決策 6）。輸出同樣先寫暫存檔再 `os.replace`：
    `magick` 是否曾經寫出部分結果就被中斷（逾時、被殺、磁碟滿）並無文件
    保證，直接寫 `out_path` 一旦真的發生就是一張半途而廢的縮圖，還會被
    `pending_image_targets` 當成「已存在」永遠跳過，不會再重抓。回傳
    "ok" / "toobig" / "fail" 供呼叫端彙總統計，任何例外都不得讓整批抓取
    中斷。
    """
    tmp_src_path = out_path + ".src"
    tmp_dst_path = out_path + ".tmp"
    try:
        request = urllib.request.Request(image_url, headers=_IMAGE_FETCH_HEADERS)
        with urllib.request.urlopen(request, timeout=IMAGE_FETCH_TIMEOUT_SECONDS) as response:
            declared_length = int(response.headers.get("Content-Length") or 0)
            if declared_length > IMAGE_MAX_BYTES:
                return "toobig"
            data = response.read(IMAGE_MAX_BYTES + 1)
        if len(data) > IMAGE_MAX_BYTES:
            return "toobig"
        with open(tmp_src_path, "wb") as tmp_file:
            tmp_file.write(data)
        subprocess.run(
            [
                "magick", tmp_src_path,
                "-resize", f"{IMAGE_THUMBNAIL_PX}x{IMAGE_THUMBNAIL_PX}>",
                "-background", "white",
                "-gravity", "center",
                "-extent", f"{IMAGE_THUMBNAIL_PX}x{IMAGE_THUMBNAIL_PX}",
                "-strip", "-quality", str(IMAGE_THUMBNAIL_QUALITY),
                tmp_dst_path,
            ],
            check=True,
            capture_output=True,
            timeout=IMAGE_CONVERT_TIMEOUT_SECONDS,
        )
        os.replace(tmp_dst_path, out_path)  # 同檔案系統內為原子操作，不會留下半成品
        return "ok"
    except Exception:
        logger.exception("縮圖失敗，略過：%s", image_url)
        return "fail"
    finally:
        if os.path.exists(tmp_src_path):
            os.remove(tmp_src_path)
        if os.path.exists(tmp_dst_path):
            os.remove(tmp_dst_path)


def fetch_images(entries: Iterable[dict[str, str]], image_dir: str) -> dict[str, int]:
    """對條目裡有圖檔連結的藥證抓圖、縮圖，落地到 image_dir。

    `--fetch-images` 的實作主體。已存在的縮圖一律跳過（可中斷續跑）；
    請求之間限速，避免對政府主機造成過大負載——單次完整抓取是六千多次
    請求、約 20 GB，這是明確選用的步驟，不在 CI 或部署路徑上
    （design.md 決策 3）。

    第一件事是確認 `magick` 存在：這個檢查必須在任何 `urlopen` 之前，
    否則「工具沒裝」會被包裝成六千多次對政府主機的下載，全部下載完才
    在轉檔那一步失敗。
    """
    _require_magick()
    os.makedirs(image_dir, exist_ok=True)
    targets = image_fetch_targets(entries)
    pending = pending_image_targets(targets, image_dir)
    stat = {"ok": 0, "fail": 0, "toobig": 0, "skip": len(targets) - len(pending)}
    logger.info(
        "外觀圖檔連結 %d 筆，已有縮圖略過 %d 筆，本次待抓 %d 筆",
        len(targets), stat["skip"], len(pending),
    )

    lock = threading.Lock()

    def _run(item: tuple[str, str, str]) -> None:
        _, image_url, out_path = item
        result = _fetch_and_thumbnail_one(image_url, out_path)
        time.sleep(IMAGE_FETCH_SLEEP_SECONDS)
        with lock:
            stat[result] += 1
            done = stat["ok"] + stat["fail"] + stat["toobig"]
        if done % _IMAGE_PROGRESS_EVERY == 0:
            logger.info(
                "[%d/%d] ok=%d fail=%d toobig=%d",
                done, len(pending), stat["ok"], stat["fail"], stat["toobig"],
            )

    with cf.ThreadPoolExecutor(max_workers=IMAGE_FETCH_WORKERS) as executor:
        list(executor.map(_run, pending))

    logger.info(
        "完成。ok=%d fail=%d toobig=%d skip=%d，縮圖目錄 %s",
        stat["ok"], stat["fail"], stat["toobig"], stat["skip"], image_dir,
    )
    return stat


def _download(url: str) -> list[dict[str, Any]]:
    logger.info("下載 %s", url)
    response = requests.get(url, timeout=DOWNLOAD_TIMEOUT_SECONDS)
    response.raise_for_status()
    return read_dataset_zip(response.content)


def main(output_path: Optional[str] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="建置本地藥證庫")
    parser.add_argument("--output", default=output_path or DEFAULT_OUTPUT)
    parser.add_argument(
        "--fetch-images",
        action="store_true",
        help=(
            "額外下載藥丸照片並落地為縮圖（預設關閉）。單次完整抓取約 20 GB、"
            "86 分鐘，且對政府主機發出請求，是明確選用的步驟，見 README"
            "「藥品外觀縮圖」。已存在的縮圖會跳過，可中斷續跑。"
        ),
    )
    parser.add_argument(
        "--image-dir",
        default=DEFAULT_IMAGE_DIR,
        help="縮圖輸出目錄，僅在 --fetch-images 時使用",
    )
    args = parser.parse_args()

    licences = _download(LICENCE_DATASET_URL)
    appearances = _download(APPEARANCE_DATASET_URL)
    entries = build_entries(licences, appearances)

    with open(args.output, "w", encoding="utf-8") as output_file:
        json.dump(entries, output_file, ensure_ascii=False)

    logger.info("寫入 %s 筆藥證至 %s", len(entries), args.output)

    if args.fetch_images:
        fetch_images(entries, args.image_dir)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
