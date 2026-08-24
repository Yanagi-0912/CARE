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
import re
import os
import pathlib
import shutil
import subprocess
import threading
import time
import urllib.request
import zipfile
from typing import Any, Iterable, Optional

import requests
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# 全部藥品許可證資料集（data.gov.tw dataset 9122）
LICENCE_DATASET_URL = "https://data.fda.gov.tw/data/opendata/export/36/json"
# 藥品外觀資料集（data.gov.tw dataset 9120）
APPEARANCE_DATASET_URL = "https://data.fda.gov.tw/data/opendata/export/42/json"

DEFAULT_OUTPUT = "resources/drug_catalog.json"
DOWNLOAD_TIMEOUT_SECONDS = 180


# 這支腳本會建立目錄、寫檔、覆寫與刪除暫存檔，而三個輸出位置全部來自 CLI
# 參數。帶錯的參數——人手誤、CI 設定錯，或由模型組出來的指令——足以讓它在
# 專案目錄外動手：`--image-dir /` 會在根目錄寫進數千個縮圖，`--output` 指到
# 別處則能覆寫任意檔案。因此所有輸出路徑一律收斂到專案目錄內。
PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


def resolve_output_path(raw: str, *, argument: str) -> str:
    """把 CLI 給的輸出路徑收斂成專案目錄內的絕對路徑，越界就拒絕。

    先 `resolve()` 再比對，兩件事都要：

    - `resolve()` 會把 `..` 收斂掉，因此 `resources/../resources/x.json` 這種
      合法寫法不會被誤擋；
    - 它同時會**跟隨符號連結**，這是關鍵——只比對字串前綴的話，一個字面上
      位於專案內、實際指向外部的連結就能整個繞過檢查。

    刻意只用在 `main()` 的參數解析，不下沉到函式庫層：抓圖與寫檔的函式仍接受
    任意路徑，測試才能用 `tmp_path` 直接驗證它們。要擋的是「錯的 CLI 參數」，
    不是「這些函式本身」。
    """
    candidate = pathlib.Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    resolved = candidate.resolve()
    if resolved != PROJECT_ROOT and PROJECT_ROOT not in resolved.parents:
        raise ValueError(
            f"{argument} 必須指向專案目錄內的位置，"
            f"但 {raw!r} 收斂後是 {resolved}（專案目錄：{PROJECT_ROOT}）。"
            "這個限制是為了讓帶錯的參數不會在專案外建檔或覆寫檔案。"
        )
    return str(resolved)


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


# ── 仿單適應症 ──────────────────────────────────────────────────────────

DEFAULT_INDICATION_OUTPUT = "resources/drug_indications.json"

# 「這段原文需不需要摘要」的機械判定。實測整份藥證庫僅 18%（12,172 筆）符合，
# 其餘 65% 已經是「緩解便祕。」這種可以直接給人看的句子——對它們呼叫模型沒有
# 收益，只有成本與改壞的風險。見 openspec/changes/drug-indication/design.md
# 決策 2。
INDICATION_PLAIN_MAX_CHARS = 40
_LATIN_RUN = re.compile(r"[A-Za-z]{4,}")
_NUMBERED_LIST = re.compile(r"(^|\n)\s*[1-9１-９]\s*[.、）)]")


def needs_summary(text: str) -> bool:
    """原文是否需要摘要。

    四個條件任一成立即需要：過長、含換行、夾雜四個以上連續英文字母（藥學名詞
    與菌株學名的訊號），或含編號清單。這些正是仿單語言的外顯特徵，而不是
    「這段話難不難懂」的直接量測——後者沒有機械判準，硬做只會得到一條調不動
    的規則。判準寬鬆一點沒關係：多摘要幾筆的代價只是建置期多幾次呼叫。
    """
    if not text:
        return False
    return (
        len(text) > INDICATION_PLAIN_MAX_CHARS
        or "\n" in text
        or bool(_LATIN_RUN.search(text))
        or bool(_NUMBERED_LIST.search(text))
    )


def _text_digest(text: str) -> str:
    """摘要的冪等鍵：原文的 sha256 前綴。

    資料每 7 日更新但多數條目不變，重跑時原文未變就不該重算摘要——這與
    `--fetch-images` 的「已存在的縮圖一律跳過」是同一個設計，讓抓取可中斷
    續跑、日後只處理新增或異動的藥證。
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def load_existing_indications(path: str) -> dict[str, dict[str, str]]:
    """讀回上次的產出，供冪等比對。檔案不存在或損毀時當成空的重頭來過——
    這一步失敗不該讓整個建置中斷，最壞情況只是全部重新摘要一次。"""
    try:
        with open(path, "r", encoding="utf-8") as existing_file:
            data = json.load(existing_file)
        if isinstance(data, dict):
            return data
        logger.warning("既有的 %s 不是物件，忽略並重頭建置", path)
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        logger.warning("既有的 %s 無法讀取（%s），忽略並重頭建置", path, exc)
    return {}


def build_indications(
    licences: Iterable[dict[str, Any]],
    existing: Optional[dict[str, dict[str, str]]] = None,
) -> dict[str, dict[str, str]]:
    """從許可證資料集取出適應症，產出「證號 → {text, summary, summary_of}」。

    以證號去重取第一筆，與 `build_entries` 的去重規則一致。沒有證號或沒有
    適應症的資料列直接略過——留一個空的 text 進來，只會讓呈現面多一個永遠
    是空的區塊。

    `existing` 帶入上次的產出時，原文未變的條目會沿用既有的 summary 與
    summary_of；原文變了則清空 summary，交給摘要步驟重算。**摘要不會憑空
    保留**：原文改了而摘要沒跟著改，等於拿舊藥的說明去描述新的適應症。
    """
    existing = existing or {}
    result: dict[str, dict[str, str]] = {}
    for row in licences:
        license_number = _clean(row.get("許可證字號"))
        text = _clean(row.get("適應症"))
        if not license_number or not text or license_number in result:
            continue
        digest = _text_digest(text)
        prior = existing.get(license_number) or {}
        carried = prior.get("summary", "") if prior.get("summary_of") == digest else ""
        result[license_number] = {
            "text": text,
            "summary": carried,
            "summary_of": digest,
        }
    return result


def pending_summary_targets(
    indications: dict[str, dict[str, str]],
) -> list[str]:
    """回傳還需要產生摘要的證號。

    需要摘要（needs_summary）且目前 summary 為空者才算待辦。不需要摘要的
    條目 summary 恆為空字串，呈現面據此直接顯示原文——空字串在這裡是
    「不需要」與「產不出來」共用的表示，兩者對呈現面的意義相同（都退回原文），
    刻意不分成兩種狀態徒增判斷。
    """
    return [
        license_number
        for license_number, entry in indications.items()
        if needs_summary(entry.get("text", "")) and not entry.get("summary")
    ]


# 摘要的約束一字不改地寫進 prompt。這幾條不是文風偏好，是安全要求：
# 摘要會被當成醫療資訊顯示給使用者看，漏掉一個適應症等於告訴他這個藥不能治
# 那個病；寫成「建議服用」則是系統在給醫療建議。見
# openspec/changes/drug-indication/specs/drug-indication/spec.md
# 的「摘要的生成約束」。
INDICATION_SUMMARY_PROMPT = """你要把一段台灣藥品仿單的「適應症」濃縮成一句話，給不熟悉醫學名詞的長輩看。

規則（違反任何一條就回傳空字串）：

1. 只能濃縮原文已有的內容。不得新增、不得推論、不得用常識補齊。
2. 不得遺漏原文列出的任何一個適應症。原文列了幾種病，摘要就要涵蓋幾種。
3. 不得寫成療效保證或用藥建議。不要出現「可以治好」「建議服用」「應該吃」。
4. 只輸出一句話，不超過 {max_chars} 個字，純中文，不要編號、不要換行、不要標題。
5. 無法在上述限制下完成時，直接回傳空字串，不要勉強生成。

原文：
{text}

只輸出摘要本身，不要任何前綴或說明。"""

SUMMARY_TIMEOUT_SECONDS = 60
SUMMARY_WORKERS = 8
# 每完成這麼多筆就存檔一次。全量約 1.2 萬筆、耗時以小時計，只在最後寫檔的話
# 中途一次網路中斷就得全部重跑；定期落地讓 build_indications 的冪等沿用真的
# 派得上用場（原文未變即帶回既有摘要），下次接著跑而不是從頭。
SUMMARY_CHECKPOINT_EVERY = 250
SUMMARY_SLEEP_SECONDS = 0.1


def _summary_is_acceptable(summary: str, max_chars: int) -> bool:
    """把 prompt 的硬性限制在程式端再驗一次。

    模型不一定聽話，而不合格的摘要一旦寫進產出物就會直接顯示給使用者。
    這裡只驗機械可驗的部分（長度、換行、編號、明顯的建議語氣）——「有沒有
    遺漏適應症」無法機械驗證，那一條只能靠 prompt 與抽樣人工檢視（見 tasks 3.4）。
    """
    if not summary:
        return False
    if len(summary) > max_chars or "\n" in summary:
        return False
    if _NUMBERED_LIST.search(summary):
        return False
    return not any(
        phrase in summary
        for phrase in ("建議服用", "可以治好", "應該服用", "請服用", "療效保證")
    )


# 配額耗盡的訊號。只認「每日配額」這一類——逾時、連線中斷、500 這種單筆
# 失敗下一筆可能就成功了，不該讓整批停下來。
_QUOTA_MARKERS = (
    "RESOURCE_EXHAUSTED",
    "generate_requests_per_model_per_day",
    "GenerateRequestsPerDayPerProjectPerModel",
)


def is_quota_exhausted(exc: BaseException) -> bool:
    """這個例外是不是「今天的配額用完了」。

    用字串比對而非例外型別：langchain 把底層的 google 例外包成自己的型別，
    比對型別會綁死在某個版本的包裝方式上，改版就失效。訊息裡的
    RESOURCE_EXHAUSTED 與那兩個 quota metric 名稱是 API 契約的一部分，
    比包裝型別穩定。
    """
    message = str(exc)
    return any(marker in message for marker in _QUOTA_MARKERS)


def summarize_indications(
    indications: dict[str, dict[str, str]],
    targets: list[str],
    max_chars: int,
    model_name: Optional[str] = None,
    checkpoint: Optional[Any] = None,
    model: Optional[Any] = None,
) -> dict[str, int]:
    """就地為 `targets` 產生摘要，回傳統計。

    不合格或呼叫失敗一律留空字串，SHALL NOT 寫入不合格的結果——呈現面看到
    空摘要就退回顯示原文，那是安全的降級；寫進一個漏了適應症的摘要則不是。
    """
    stat = {"ok": 0, "rejected": 0, "failed": 0, "quota_exhausted": False}
    if not targets:
        return stat

    if model is not None:
        return _run_summaries(indications, targets, max_chars, model, checkpoint, stat)

    from langchain_google_genai import ChatGoogleGenerativeAI

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        # 明確報錯而非靜默產出空摘要：後者會讓整批「成功完成」卻一個摘要都沒有，
        # 而外顯症狀（全部顯示原文）看起來像是原文本來就都很短。
        raise RuntimeError(
            "缺少 GEMINI_API_KEY，無法產生摘要。"
            "只要原文不要摘要請加 --skip-summaries。"
        )

    model = ChatGoogleGenerativeAI(
        model=model_name or os.getenv("MODEL_NAME", "gemini-2.5-flash"),
        google_api_key=api_key,
        timeout=SUMMARY_TIMEOUT_SECONDS,
    )
    return _run_summaries(indications, targets, max_chars, model, checkpoint, stat)


def _run_summaries(indications, targets, max_chars, model, checkpoint, stat):
    """實際跑摘要。與 summarize_indications 分開，讓測試能直接注入假的 model。

    配額耗盡時提早中止：每日配額要等隔天才重置，繼續送出的請求不可能成功，
    只是白白拖慢收尾並繼續打對方的服務。實測全量批次撞到上限後仍硬打了
    約 2,309 次，全部拿 429。中止後未處理的目標維持空摘要，呈現面退回顯示
    原文；下次重跑時 build_indications 的冪等沿用會讓已完成的部分不必重算。

    刻意用循序而非 ThreadPoolExecutor.map：後者要等整批送完才停得下來，
    偵測到配額耗盡的當下已經多送了一整批。這裡改成小批送出、每批之間檢查，
    兼顧併發與可中止。
    """
    pending = list(targets)
    index = 0
    with cf.ThreadPoolExecutor(max_workers=SUMMARY_WORKERS) as pool:
        while pending and not stat["quota_exhausted"]:
            batch, pending = pending[:SUMMARY_WORKERS], pending[SUMMARY_WORKERS:]
            futures = {
                pool.submit(_summarize_one, indications, key, max_chars, model): key
                for key in batch
            }
            for future in cf.as_completed(futures):
                key, summary, outcome, quota_hit = future.result()
                indications[key]["summary"] = summary
                stat[outcome] += 1
                if quota_hit:
                    stat["quota_exhausted"] = True
                index += 1
                if index % SUMMARY_CHECKPOINT_EVERY == 0:
                    logger.info("摘要進度 %s/%s %s", index, len(targets), stat)
                    if checkpoint is not None:
                        checkpoint(indications)
    if stat["quota_exhausted"]:
        logger.warning(
            "配額耗盡，提早中止：已完成 %s 筆，尚有 %s 筆未處理。"
            "配額重置後重跑同一個指令即可接續（原文未變的條目會沿用既有摘要）。",
            stat["ok"],
            len(targets) - index,
        )
    return stat


def _summarize_one(indications, license_number, max_chars, model):
    """單筆摘要。回傳 (證號, 摘要, 統計鍵, 是否為配額耗盡)。"""
    text = indications[license_number]["text"]
    prompt = INDICATION_SUMMARY_PROMPT.format(max_chars=max_chars, text=text)
    try:
        raw = model.invoke(prompt)
        summary = (getattr(raw, "content", "") or "").strip()
    except Exception as exc:  # noqa: BLE001 - 建置期批次，單筆失敗不該中斷整批
        if is_quota_exhausted(exc):
            return license_number, "", "failed", True
        logger.warning("摘要失敗 %s：%s", license_number, exc)
        return license_number, "", "failed", False
    if not _summary_is_acceptable(summary, max_chars):
        return license_number, "", "rejected", False
    return license_number, summary, "ok", False


def build_indication_file(
    licences: Iterable[dict[str, Any]],
    output_path: str,
    skip_summaries: bool = False,
    max_chars: Optional[int] = None,
) -> dict[str, dict[str, str]]:
    """`--fetch-indications` 的實作主體。"""
    max_chars = max_chars or int(os.getenv("DRUG_INDICATION_SUMMARY_MAX_CHARS", "60"))
    existing = load_existing_indications(output_path)
    indications = build_indications(licences, existing)
    targets = pending_summary_targets(indications)
    carried = sum(1 for e in indications.values() if e.get("summary"))
    logger.info(
        "仿單適應症 %s 筆，需摘要 %s 筆（沿用既有摘要 %s 筆）",
        len(indications),
        len(targets),
        carried,
    )

    def _write(data: dict[str, dict[str, str]]) -> None:
        with open(output_path, "w", encoding="utf-8") as output_file:
            json.dump(data, output_file, ensure_ascii=False)

    if skip_summaries:
        logger.info("--skip-summaries：略過摘要，呈現面將顯示原文")
    else:
        stat = summarize_indications(
            indications, targets, max_chars, checkpoint=_write
        )
        logger.info("摘要完成：%s", stat)

    _write(indications)
    logger.info("寫入 %s 筆仿單適應症至 %s", len(indications), output_path)
    return indications


def main(output_path: Optional[str] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # 本腳本原本零環境相依，--fetch-indications 的摘要步驟才需要 GEMINI_API_KEY。
    # 明確指定路徑而非 find_dotenv()：後者從呼叫端檔案往上找，以 -m 執行或從
    # 別的工作目錄呼叫時會找不到，症狀是「金鑰明明設了卻說缺少」。
    load_dotenv(pathlib.Path(__file__).resolve().parents[1] / ".env")
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
    parser.add_argument(
        "--fetch-indications",
        action="store_true",
        help=(
            "額外產出仿單適應症（預設關閉）。原文取自已下載的許可證資料集、"
            "不需額外連線；其中約 18% 過長或夾雜藥學名詞的條目會呼叫 LLM 產生"
            "摘要，因此需要 GEMINI_API_KEY。原文未變的條目會沿用既有摘要，"
            "可中斷續跑。未帶此旗標時本腳本行為與加入本功能前完全相同。"
        ),
    )
    parser.add_argument(
        "--indication-output",
        default=DEFAULT_INDICATION_OUTPUT,
        help="仿單適應症輸出路徑，僅在 --fetch-indications 時使用",
    )
    parser.add_argument(
        "--skip-summaries",
        action="store_true",
        help=(
            "僅產出仿單原文，不呼叫 LLM 產生摘要（僅在 --fetch-indications 時"
            "使用）。摘要為空時呈現面會退回顯示原文，因此這個模式產出的檔案"
            "本身就是可用的，只是可讀性較差。"
        ),
    )
    args = parser.parse_args()

    # 先驗路徑再做任何 I/O：下載要花好幾分鐘，等寫檔才發現參數不合法，
    # 使用者已經白等一次，而且此時可能已經建了目錄。
    output_path_resolved = resolve_output_path(args.output, argument="--output")
    image_dir_resolved = (
        resolve_output_path(args.image_dir, argument="--image-dir")
        if args.fetch_images
        else None
    )
    indication_output_resolved = (
        resolve_output_path(args.indication_output, argument="--indication-output")
        if args.fetch_indications
        else None
    )

    licences = _download(LICENCE_DATASET_URL)
    appearances = _download(APPEARANCE_DATASET_URL)
    entries = build_entries(licences, appearances)

    with open(output_path_resolved, "w", encoding="utf-8") as output_file:
        json.dump(entries, output_file, ensure_ascii=False)

    logger.info("寫入 %s 筆藥證至 %s", len(entries), output_path_resolved)

    if args.fetch_images:
        fetch_images(entries, image_dir_resolved)

    if args.fetch_indications:
        build_indication_file(
            licences,
            indication_output_resolved,
            skip_summaries=args.skip_summaries,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
