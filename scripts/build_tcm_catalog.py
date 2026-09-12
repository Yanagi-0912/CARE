"""從衛福部中醫藥司的公開查詢系統建出本地中藥庫。

## 為什麼需要這支腳本

`scripts/build_drug_catalog.py` 建出來的藥證庫**一筆中藥都沒有**：它的資料源是
食藥署的「全部藥品許可證資料集」（dataset 9122），而該資料集的說明頁明文寫著
「中藥藥品，請至此網站查詢」——中藥不歸食藥署管，歸衛福部中醫藥司。實測
66,503 筆藥證的字號前綴全部是西藥類（藥製／藥輸／成製／成輸／菌疫／罕藥），
拿「小青龍湯」「加味逍遙散」「六味地黃丸」去撈都是 0 筆。

這個缺口的後果有兩層：

1. **中藥被判成可疑來源。** `risk_rules.assess()` 對藥證庫查無的藥回 `low`，
   於是中醫診所正常拿的藥可能被提示「查不到這個藥」。
2. **中藥完全不參與用藥安全比對。** 使用者同時吃著中藥，系統當作他沒吃。

第 2 層才是真正嚴重的。白名單裡的 `DL-METHYLEPHEDRINE HCL`（1,153 種成藥含它，
納入理由是「升血壓、心跳加快，長者常有高血壓」）其天然來源就是**麻黃**，而
200 個基準方裡有 17 方含麻黃——葛根湯、小青龍湯、麻黃湯全在其中，全是長輩
感冒最常拿的科學中藥。「綜合感冒藥 ＋ 葛根湯」在藥理上就是同一個成分吃兩份，
正是成分重複規則要抓的事，但中藥那一側現在看不見。

## 資料源與授權

來源是 `www.cmthp.mohw.gov.tw`（臺灣中藥典及中西藥併用查詢系統，衛生福利部
中醫藥司）。**該站沒有開放資料授權、沒有 API、沒有批次下載**，頁尾標示
「Copyrights © 衛生福利部版權所有」——這與 `build_drug_catalog.py` 的資料源
（政府資料開放授權條款第 1 版）不同，是本腳本與那支腳本最重要的差別。

原本的 dataset 8578「中藥藥品許可證查詢」已下架：後設資料顯示它只有 4 筆
`title`／`link` 兩欄、品檢未過、2017 年後未更新，下載連結指向一個已失效的
Google Drive 檔——它從來就不是藥證資料，只是一組指向網頁的連結。

抓取節制：每次請求間隔 `REQUEST_INTERVAL_SECONDS`，總量 200 + 9 頁，一次完整
建表約 200 次請求。資料本身變動極慢（基準方是部訂標準），沒有頻繁重跑的理由。

## gov.tw 的 TLS 中繼憑證

本站與 `www.mohw.gov.tw` 一樣只送 leaf 憑證，不附中繼憑證。Python 的 ssl 模組
不會依 AIA 欄位自動補抓，於是直接拋 `CERTIFICATE_VERIFY_FAILED`，看起來像站台
掛掉。因此一律走 `app.core.ca_bundle.get_ca_bundle()`——**不是 verify=False**，
憑證驗證仍然完整有效，只是額外信任那張已釘選的中繼憑證。

用法：
    python -m scripts.build_tcm_catalog [--output resources/tcm_catalog.json]
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import pathlib
import re
import ssl
import time
import urllib.request
from typing import Any, Iterable, Optional

from app.core.ca_bundle import get_ca_bundle

logger = logging.getLogger(__name__)

BASE_URL = "https://www.cmthp.mohw.gov.tw"
FORMULA_DETAIL_URL = BASE_URL + "/Home/prescriptionDetail?textKeyword={code}"
ITEM_LIST_URL = BASE_URL + "/Search/CSearch?v=4&p={page}&m={mode}"

# 基準方的編號是連續的 001–200，直接列舉而不解析清單頁：清單頁用 footable
# 分頁，一次只渲染 50 列，解析它反而會靜默漏掉四分之三。
FORMULA_CODE_RANGE = range(1, 201)
# 中藥材（m=0）分 8 頁、濃縮製劑（m=1）1 頁，皆為第四版。
ITEM_PAGES = ((0, 8), (1, 1))

DEFAULT_OUTPUT = "resources/tcm_catalog.json"
REQUEST_TIMEOUT_SECONDS = 30
REQUEST_INTERVAL_SECONDS = 0.25
_USER_AGENT = "Mozilla/5.0 (compatible; CARE-build-tcm-catalog/1.0)"

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]

# 處方欄位的尾綴，例如「(一日飲片量28公克)。傳統製劑加蜂蜜適量。」——那是
# 劑量與製法說明，不是藥材，切在這裡。
_FORMULA_TAIL = re.compile(r"[（(][^）)]*[）)]|。.*$")
# 藥材後面的份量數字（「葛根6」「麻黃4.5」）。份量對成分比對沒有貢獻，
# 而保留它會讓同一味藥因為不同方裡份量不同而被當成不同的東西。
_HERB_QUANTITY = re.compile(r"[\d.．]+\s*$")


def resolve_output_path(raw: str, *, argument: str) -> str:
    """把 CLI 給的輸出路徑收斂成專案目錄內的絕對路徑，越界就拒絕。

    與 `build_drug_catalog.resolve_output_path` 同一套理由與作法：帶錯的參數
    足以讓腳本在專案目錄外覆寫檔案。先 `resolve()` 再比對，同時收斂 `..`
    並跟隨符號連結。
    """
    candidate = pathlib.Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    resolved = candidate.resolve()
    if resolved != PROJECT_ROOT and PROJECT_ROOT not in resolved.parents:
        raise ValueError(f"{argument} 必須指向專案目錄內的位置，收到 {raw!r}")
    return str(resolved)


def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=get_ca_bundle())


def fetch(url: str, context: Optional[ssl.SSLContext] = None) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(
        request, timeout=REQUEST_TIMEOUT_SECONDS, context=context or _ssl_context()
    ) as response:
        return response.read().decode("utf-8", errors="replace")


def _visible_lines(markup: str) -> list[str]:
    """把 HTML 攤成可見文字行。詳細頁是「標籤／值」交錯的版面，取值靠的是
    「在標籤那一行的下一行」，因此必須保留行的順序。"""
    stripped = re.sub(r"<script.*?</script>|<style.*?</style>", "", markup, flags=re.S)
    stripped = re.sub(r"<[^>]+>", "\n", stripped)
    return [line.strip() for line in html.unescape(stripped).split("\n") if line.strip()]


def _value_after(lines: list[str], label: str) -> str:
    try:
        return lines[lines.index(label) + 1]
    except (ValueError, IndexError):
        return ""


def parse_herbs(formula: str) -> list[str]:
    """把處方欄位切成藥材清單。

    「葛根6、麻黃4.5、桂枝3、白芍3、炙甘草3、生薑4.5、大棗4 (一日飲片量28公克)。」
    → ['葛根', '麻黃', '桂枝', '白芍', '炙甘草', '生薑', '大棗']

    份量刻意去掉：比對要問的是「這一方含不含麻黃」，不是「含多少」。保留份量
    會讓同一味藥在不同方裡變成不同的字串，交集永遠是空的。

    **炮製前綴（炙甘草 vs 甘草）不合併。** 兩者的藥性在中醫理論裡有別，合併
    是一個我們沒有依據的藥理判斷；而分開的代價只是多列一項，遠小於猜錯。
    實測 200 方裡甘草出現 73 次、炙甘草 57 次，兩者都保留。
    """
    if not formula:
        return []
    body = _FORMULA_TAIL.sub("", formula)
    herbs = []
    for chunk in re.split(r"[、，,]", body):
        herb = _HERB_QUANTITY.sub("", chunk.strip()).strip()
        if herb:
            herbs.append(herb)
    return herbs


def parse_formula_detail(code: str, markup: str) -> Optional[dict[str, Any]]:
    """把一頁基準方詳細頁轉成條目。名稱或處方任一為空就回 None——沒有組成的
    方對比對沒有貢獻，留著只會讓下游以為它有資料。"""
    lines = _visible_lines(markup)
    name = _value_after(lines, "名稱")
    formula = _value_after(lines, "處方")
    if not name or not formula:
        return None
    return {
        "code": f"P{code}",
        "kind": "formula",
        "name_zh": name,
        "name_latin": "",
        "name_en": "",
        "source": _value_after(lines, "出典"),
        "herbs": parse_herbs(formula),
        "formula_raw": formula,
        "effect": _value_after(lines, "效能"),
        # 適應症會直接揭露病情，與 `Medication.indication` 同一條慣例：
        # 僅供 LIFF 內呈現，SHALL NOT 進入任何推播訊息。
        "indication": _value_after(lines, "適應症"),
    }


def parse_item_rows(markup: str, kind: str) -> list[dict[str, Any]]:
    """把品項查詢頁的表格轉成條目（中藥材與濃縮製劑共用同一個表格結構）。"""
    entries = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", markup, flags=re.S):
        cells = [
            html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()
            for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S)
        ]
        if len(cells) < 4 or not cells[0].isdigit():
            continue
        code, name_zh, name_latin, name_en = cells[:4]
        if not name_zh:
            continue
        entries.append(
            {
                "code": f"H{code}",
                "kind": kind,
                "name_zh": name_zh,
                "name_latin": re.sub(r"\s+", " ", name_latin),
                "name_en": name_en,
                "source": "",
                # 單味藥的「組成」就是它自己：讓下游用同一個欄位比對，
                # 不必為單味藥與複方各寫一條路徑。
                "herbs": [name_zh],
                "formula_raw": "",
                "effect": "",
                "indication": "",
            }
        )
    return entries


def build_entries(
    formula_pages: Iterable[tuple[str, str]],
    item_pages: Iterable[tuple[str, str]],
) -> list[dict[str, Any]]:
    """合併基準方與品項清單，以 `code` 去重後依 code 排序。

    排序讓同一份輸入永遠產生同一份檔案——這個檔案會進 git，順序不穩定會讓
    每次重建都產生無意義的差異。
    """
    by_code: dict[str, dict[str, Any]] = {}
    for code, markup in formula_pages:
        entry = parse_formula_detail(code, markup)
        if entry and entry["code"] not in by_code:
            by_code[entry["code"]] = entry
    for kind, markup in item_pages:
        for entry in parse_item_rows(markup, kind):
            by_code.setdefault(entry["code"], entry)
    return [by_code[code] for code in sorted(by_code)]


def main(output_path: Optional[str] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="建置本地中藥庫")
    parser.add_argument("--output", default=output_path or DEFAULT_OUTPUT)
    args = parser.parse_args([] if output_path else None)
    resolved = resolve_output_path(args.output, argument="--output")

    context = _ssl_context()

    formula_pages = []
    for number in FORMULA_CODE_RANGE:
        code = f"{number:03d}"
        formula_pages.append((code, fetch(FORMULA_DETAIL_URL.format(code=code), context)))
        time.sleep(REQUEST_INTERVAL_SECONDS)
        if number % 50 == 0:
            logger.info("基準方 %d/%d", number, len(FORMULA_CODE_RANGE))

    item_pages = []
    for mode, pages in ITEM_PAGES:
        kind = "herb" if mode == 0 else "concentrated"
        for page in range(1, pages + 1):
            item_pages.append(
                (kind, fetch(ITEM_LIST_URL.format(page=page, mode=mode), context))
            )
            time.sleep(REQUEST_INTERVAL_SECONDS)

    entries = build_entries(formula_pages, item_pages)
    with open(resolved, "w", encoding="utf-8") as output_file:
        json.dump(entries, output_file, ensure_ascii=False, indent=1)

    formulas = sum(1 for e in entries if e["kind"] == "formula")
    logger.info(
        "寫入 %d 筆至 %s（基準方 %d、藥材與濃縮製劑 %d）",
        len(entries),
        resolved,
        formulas,
        len(entries) - formulas,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
