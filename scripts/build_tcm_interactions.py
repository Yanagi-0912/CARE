"""從衛福部中西藥交互作用資料庫建出本地配對表。

## 它補的是哪一格

`resources/tcm_catalog.json` 建好之後，中藥有了名稱與組成，但**接不上西藥**：
中藥材是中文名（麻黃、甘草），西藥成分是英文學名（DL-METHYLEPHEDRINE HCL、
FUROSEMIDE），`find_overlap` 做的是字串相等比對，兩邊永遠交集不到。麻黃與
麻黃鹼在藥理上是同一件事，在字串上完全無關。

要橋接就得有一張「中藥名 × 西藥名」的對照，而那是藥理知識——不能由我們推測。
這支腳本抓的正是這張表：衛福部自己維護的中西藥交互作用資料庫，每一筆都附
機制摘要與建議處理方式。

## 資料源與授權

`www.cmdhi.mohw.gov.tw`（中西藥交互作用資料庫，衛生福利部）。與
`build_tcm_catalog.py` 的來源同一個站群，因此有同樣的限制：**沒有開放資料
授權、沒有 API、沒有批次下載**，頁尾標示「Copyrights © 2023 衛生福利部版權
所有」。

該站另有兩項自述必須跟著資料一起保留，否則會誤用：

1. 「本資料庫系統目前**僅提供中藥與西藥之交互作用**，建議您參考其他資料來源
   進行西藥藥物交互作用比對。」——它不做西藥對西藥。
2. 「本藥物交互作用資料查詢**僅供藥師參考**，不宜使用在疾病判斷及治療。」
   ——因此下游訊息 SHALL 導向藥師，SHALL NOT 給劑量建議或指示停藥，與
   `otc_alert_service` 既有的措辭紅線一致。

## 兩種西藥列，只有一種現在用得上

實測 2,640 筆有效列裡：

- **2,600 筆是具名成分**（Warfarin、Aspirin、Ibuprofen…），可直接與藥證庫的
  `ingredients` 比對，`western_kind` 標為 `ingredient`。
- **40 筆是藥理類別**（抗凝血劑、NSAID類、降血壓藥、利尿劑…共 13 種寫法），
  中文類別名對不上任何成分字串。它們**照樣收進檔案但標為 `class`**，理由是
  丟掉等於讓後人以為這個資料源沒有這些——真正該做的是之後用 ATC 碼把類別
  對應起來（`drug_catalog.json` 現已有 `atc_codes`），那是另一個決定。

## 名稱正規化踩到的坑

中藥名必須先做 NFKC。實測交互作用表的「小青龍湯」用的是 CJK 相容表意字
`U+F9C4`（龍），而中藥庫用的是常用的 `U+9F8D`——**肉眼完全一樣，字串不相等**。
沒有 NFKC 的話，含麻黃的關鍵方劑之一會靜默地永遠比不到。這與
`drug_catalog_service.normalize_drug_name` 用 NFKC 是同一條理由。

用法：
    python -m scripts.build_tcm_interactions [--output resources/tcm_drug_interactions.json]
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
import unicodedata
import urllib.request
from typing import Any, Iterable, Optional

from app.core.ca_bundle import get_ca_bundle
from app.services.safety.tcm_interaction import normalize_tcm_name

logger = logging.getLogger(__name__)

BASE_URL = "https://www.cmdhi.mohw.gov.tw"
# 西藥索引。中藥索引（MatchListC）是同一份資料的另一種排序，抓其中一邊即可。
LIST_URL = BASE_URL + "/Interactions/MatchListE?p={page}&s=0"
LIST_PAGES = 53

DEFAULT_OUTPUT = "resources/tcm_drug_interactions.json"
REQUEST_TIMEOUT_SECONDS = 30
REQUEST_INTERVAL_SECONDS = 0.3
_USER_AGENT = "Mozilla/5.0 (compatible; CARE-build-tcm-interactions/1.0)"

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]

_CJK = re.compile(r"[一-鿿]")
# 頁面的 JavaScript 樣板會漏進 HTML，產生 53 列字面上的
# `" + response.owner[i].HERBNAME + "`。它不是資料。
_TEMPLATE_ARTIFACT = re.compile(r"response\.\w+")

# 實測的資料瑕疵。逐一列舉而不做模糊比對：猜錯會把兩種不同的藥合併，
# 而這裡的每一項都是人眼確認過的。
_NAME_FIXES = {
    "Ciproﬂoxacin": "Ciprofloxacin",          # ﬂ 合字（U+FB02）
    "Cyclosporin": "Cyclosporine",            # 同一成分的兩種拼法
    "Nifedipne": "Nifedipine",                # 拼字錯誤
    "MAO 抑制劑": "MAO抑制劑",                  # 同一類別的兩種寫法
}
# 無法判讀的列。`Risperidone Quetiapine Quetiapine` 是三個藥名黏在一起且重複，
# 對不出「這一列講的是哪一個藥」，因此整列丟棄而不是猜一個。
# 比對前一律大寫化，與 `normalize_western_name` 的輸出對齊——存原始大小寫
# 會讓這道過濾靜默失效（實測就是這樣漏掉的）。
_UNPARSEABLE_NAMES = frozenset({"RISPERIDONE QUETIAPINE QUETIAPINE"})


def resolve_output_path(raw: str, *, argument: str) -> str:
    candidate = pathlib.Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    resolved = candidate.resolve()
    if resolved != PROJECT_ROOT and PROJECT_ROOT not in resolved.parents:
        raise ValueError(f"{argument} 必須指向專案目錄內的位置，收到 {raw!r}")
    return str(resolved)


# `normalize_tcm_name` 由 app.services.safety.tcm_interaction 提供：建置期與
# 執行期必須用同一份實作，兩份分歧會讓檔案裡的鍵與比對時的鍵悄悄對不上。


def normalize_western_name(name: str) -> str:
    """西藥名的比對鍵。具名成分一律大寫（與藥證庫的 `ingredients` 一致），
    類別名維持中文原樣。"""
    text = unicodedata.normalize("NFKC", name or "").strip()
    text = _NAME_FIXES.get(text, text)
    return text if _CJK.search(text) else text.upper()


def parse_list_page(markup: str) -> list[dict[str, Any]]:
    """把一頁交互作用清單轉成配對列。

    表格的第二欄是「西藥 <i/> 中藥」兩個名字夾著一個圖示元素，靠那個元素切開；
    第三欄是機制摘要加一個「詳細內容」連結，連結裡帶著該筆的 id。
    """
    body = markup.split('<tbody id="ebody">')[-1]
    pairs = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", body, flags=re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S)
        if len(cells) != 3:
            continue
        names = [
            html.unescape(re.sub(r"<[^>]+>", "", part)).strip()
            for part in re.split(r"<i[^>]*></i>", cells[1])
        ]
        if len(names) != 2:
            continue
        western_raw, tcm_raw = names
        if _TEMPLATE_ARTIFACT.search(western_raw) or _TEMPLATE_ARTIFACT.search(tcm_raw):
            continue
        western = normalize_western_name(western_raw)
        if not western or western.upper() in _UNPARSEABLE_NAMES:
            continue
        tcm = normalize_tcm_name(tcm_raw)
        if not tcm:
            continue
        detail = re.search(r"Detail\?id=(\d+)", cells[2])
        summary = html.unescape(re.sub(r"<[^>]+>", "", cells[2])).replace("詳細內容", "")
        # 來源的摘要夾雜 \r\r\n 等原始換行，收斂成單一空白：這段文字會被
        # 呈現面讀到，保留原始換行只會在卡片與純文字裡產生破碎的排版。
        summary = re.sub(r"\s+", " ", summary).strip()
        pairs.append(
            {
                "tcm": tcm,
                "western": western,
                # `class` 是中文藥理類別名（抗凝血劑、NSAID類…），對不上任何
                # 成分字串；下游必須據此決定要不要參與比對。
                "western_kind": "class" if _CJK.search(western) else "ingredient",
                "summary": summary,
                "detail_id": detail.group(1) if detail else "",
            }
        )
    return pairs


def build_payload(pages: Iterable[str]) -> dict[str, Any]:
    """把各頁的配對列合併去重，排序後包成可出貨的檔案。"""
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for markup in pages:
        for pair in parse_list_page(markup):
            seen.setdefault((pair["tcm"], pair["western"]), pair)
    pairs = [seen[key] for key in sorted(seen)]
    return {
        "_readme": [
            "中藥 × 西藥交互作用配對表。來源：衛生福利部中西藥交互作用資料庫",
            f"（{BASE_URL}），Copyrights © 2023 衛生福利部版權所有。",
            "",
            "來源站自述的兩項限制，使用時必須一併遵守：",
            "  1. 本資料庫僅提供中藥與西藥之交互作用，**不含西藥對西藥**。",
            "  2. 資料僅供藥師參考，不宜使用在疾病判斷及治療——因此下游訊息",
            "     SHALL 導向藥師，SHALL NOT 給劑量建議或指示停藥。",
            "",
            "western_kind 有兩種：",
            "  ingredient — 具名成分（大寫），可直接與 drug_catalog.json 的",
            "               ingredients 比對。",
            "  class      — 中文藥理類別名（抗凝血劑、NSAID類…），對不上成分",
            "               字串。收進來是為了不讓後人以為資料源沒有這些；要",
            "               用得先以 ATC 碼建立類別對應，那是另一個決定。",
            "",
            "中藥名一律經 NFKC 正規化並去除劑型標記與括號補述，理由見",
            "scripts/build_tcm_interactions.py 的 normalize_tcm_name。",
        ],
        "pairs": pairs,
    }


def main(output_path: Optional[str] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="建置中西藥交互作用配對表")
    parser.add_argument("--output", default=output_path or DEFAULT_OUTPUT)
    args = parser.parse_args([] if output_path else None)
    resolved = resolve_output_path(args.output, argument="--output")

    context = ssl.create_default_context(cafile=get_ca_bundle())
    pages = []
    for page in range(1, LIST_PAGES + 1):
        request = urllib.request.Request(
            LIST_URL.format(page=page), headers={"User-Agent": _USER_AGENT}
        )
        with urllib.request.urlopen(
            request, timeout=REQUEST_TIMEOUT_SECONDS, context=context
        ) as response:
            pages.append(response.read().decode("utf-8", errors="replace"))
        time.sleep(REQUEST_INTERVAL_SECONDS)
        if page % 10 == 0:
            logger.info("清單頁 %d/%d", page, LIST_PAGES)

    payload = build_payload(pages)
    with open(resolved, "w", encoding="utf-8") as output_file:
        json.dump(payload, output_file, ensure_ascii=False, indent=1)

    by_kind: dict[str, int] = {}
    for pair in payload["pairs"]:
        by_kind[pair["western_kind"]] = by_kind.get(pair["western_kind"], 0) + 1
    logger.info("寫入 %d 組配對至 %s（%s）", len(payload["pairs"]), resolved, by_kind)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
