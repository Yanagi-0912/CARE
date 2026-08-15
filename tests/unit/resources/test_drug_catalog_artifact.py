"""守住已提交的藥證庫產出物。

這個檔案由 `python -m scripts.build_drug_catalog` 產生並提交進 repo（見
`openspec/changes/prescription-bag-scan/design.md` 決策 3：執行期不對外連線）。
它壞掉或消失時，`DrugCatalogService.load_from_path` 會安全降級成空服務——
所有藥名判為低信心、每份草稿都強制人工核對，應用照常啟動。那個降級方向是
對的，但它也代表**沒有任何東西會失敗**：藥袋辨識的唯一錯讀偵測機制就這樣
無聲地停止工作，而且外顯症狀（每份草稿都是 medium）看起來像是模型辨識不準。

所以守門放在這裡：產出物有問題時，讓測試大聲失敗。
"""

import json
from pathlib import Path

from app.services.medication.drug_catalog_service import DrugCatalogService

CATALOG = Path(__file__).resolve().parents[3] / "resources" / "drug_catalog.json"

# 食藥署全部藥品許可證資料集實測為六萬多筆。訂在一萬是為了擋住「抓到一小段
# 就中斷」或「欄位改名導致大量列被略過」這類壞掉的產出，而不是把測試綁死在
# 某個當下的筆數——藥證數量本來就會隨每 7 日的更新增減。
MINIMUM_ENTRIES = 10_000


def test_committed_catalog_exists_and_parses():
    assert CATALOG.is_file(), (
        f"{CATALOG} 不存在。執行 python -m scripts.build_drug_catalog 產生它。"
    )
    entries = json.loads(CATALOG.read_text(encoding="utf-8"))
    assert isinstance(entries, list)
    assert len(entries) >= MINIMUM_ENTRIES


def test_committed_catalog_has_no_empty_shells():
    """欄位名稱與資料集對不上時，每一列都會解析成有證號但沒有品名的空殼。

    這種產出物「載入成功」卻比對不到任何東西，是最難察覺的失敗模式。
    """
    entries = json.loads(CATALOG.read_text(encoding="utf-8"))
    shells = [e for e in entries if not e.get("name_zh") and not e.get("name_en")]
    assert not shells, f"有 {len(shells)} 筆條目中英文品名皆為空"
    assert all(e.get("license_number") for e in entries)


def test_committed_catalog_actually_matches_a_known_drug():
    """載入得起來不等於比對得到。用一個真實存在的藥證品名走完整條比對路徑。

    只斷言 `match()` 非 None 不足以守住這件事：`match()` 非 None 這件事
    本身可能只來自反方向含容比對（查詢字串剛好包含某個登記片段），跟
    「這張品名真的在藥證庫裡」是兩回事——把冠脂妥那筆條目整個刪掉，
    `match("冠脂妥膜衣錠10毫克")` 仍會因為藥證庫另有一張證號單獨以
    「膜衣錠」掛證（"康普萊"膜衣錠，正規化後廠商前綴被拿掉只剩
    「膜衣錠」，是這個查詢的子字串）而回傳非 None。真正證明這張品名
    在庫裡的方式，是斷言它的證號出現在候選清單中。

    不斷言 `license_number` 有值：候選其實有兩張（CRESTOR 本身，以及
    上述單獨掛證的「膜衣錠」），`license_number` 理應留空——藥名驗證
    與證號確定是兩件事（見 drug-appearance-photo spec）。
    """
    service = DrugCatalogService.load_from_path(str(CATALOG), threshold=0.88)
    assert not service.is_empty

    match = service.match("冠脂妥膜衣錠10毫克")
    assert match is not None
    assert "衛署藥輸字第024131號" in {c.license_number for c in match.candidates}
