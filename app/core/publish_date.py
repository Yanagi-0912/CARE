"""知識庫文件發布日期的解析與呈現格式。

這裡的邏輯原本只存在於 `medical_news/relevance.py`（每日推播用它排序與過濾
時效）。RAG 答案要在來源旁標示發布日期之後，第二個呼叫端出現了，而兩邊對
「什麼字串算一個日期」必須有同一個答案：推播判定為「沒有可用日期」而排除的
文件，在 RAG 來源列上也不該顯示出一個日期來。

只認得 `YYYY-MM-DD`、`YYYY/MM/DD`、`YYYY.MM.DD` 與民國年的同款格式。ETL 端
不做正規化（`CARE-data/scraper_api.py` 是把來源字串原樣寫入的），所以同一個
欄位裡本來就混著不同寫法，解析要在讀取端做。
"""

from __future__ import annotations

import re
from datetime import date, datetime

# 明顯不是日期的佔位字串。判定「有沒有可用的日期」時，這些等同於沒有。
NON_DATE_PLACEHOLDERS: frozenset[str] = frozenset({"不詳", "未提供", "無", "-", "N/A"})

_DATE_PATTERN = re.compile(r"^(\d{2,4})[-/.](\d{1,2})[-/.](\d{1,2})$")

# 民國年與西元年的界線。三位數（含）以下一律當成民國年——西元年不可能是三位數。
#
# **訂正（2026-09-04）**：本行原本寫著「食藥署與衛福部的頁面普遍以民國年呈現」，
# 那是未經查證的推測。實際量測 `health_articles_chunks` 中 `chunk_index=1` 且有
# 網址的 2,422 筆，**民國格式 0 筆**——國健署新聞 1,011 筆、TFC 823 筆、
# 食藥署闢謠專區 587 筆全部是西元 `YYYY-MM-DD`（食藥署的抽取 regex 本身就寫死
# `\d{4}`）。民國年的支援仍然保留：gov.tw 各頁面的日期呈現本來就不一致，上游
# 改版時多認一種格式的成本是零，而少認一種會讓整批文章安靜地消失。
_ROC_YEAR_OFFSET = 1911
_ROC_YEAR_MAX = 999

# 呈現用的統一格式。庫裡混著 `2024-01-01` 與 `2024/01/01` 兩種寫法（來源不同），
# 同一張卡片上兩種並陳看起來像是資料壞掉。
_DISPLAY_FORMAT = "%Y-%m-%d"


def parse_publish_date(value: str | None) -> date | None:
    """把 `2026-08-30` 或民國年的 `115-09-01` 解析成 date；失敗回 None。"""
    if not value:
        return None
    cleaned = value.strip()
    if not cleaned or cleaned.upper() in NON_DATE_PLACEHOLDERS:
        return None

    match = _DATE_PATTERN.match(cleaned)
    if match is None:
        return None

    year, month, day = (int(part) for part in match.groups())
    if year <= _ROC_YEAR_MAX:
        year += _ROC_YEAR_OFFSET
    try:
        return datetime(year, month, day).date()
    except ValueError:
        return None


# 頁面內文自己標示的日期。前面必須有標籤（「更新日期：」「發布時間」…），
# 裸日期一律不認——衛教文內文出現的日期多半是別的東西（統計年度、活動日期、
# 法規施行日），抓來當發布日只會標錯。
#
# 實測（2026-09-27，線上 22 篇由知識回報收錄的文章）：4 篇的內文有這種標籤，
# 其餘 18 篇整頁沒有任何日期標示。Firecrawl 的 metadata 則完全沒有日期欄位
# （兩個臺北榮總頁面實測，回的鍵只有 title／og:*／contentType 那些）。
_STATED_DATE = re.compile(
    r"(?:發[布佈]|刊登|更新|修改|維護|公告|上稿|異動)\s*(?:日期|時間)?\s*[:：]\s*"
    r"(?:民國\s*)?(\d{2,4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})"
)


def extract_stated_publish_date(text: str | None) -> str:
    """從頁面內文抓它自己標示的日期，回 `YYYY-MM-DD`；沒有就回空字串。

    取**最後一個**符合的：gov.tw 的頁面把「更新日期」放在頁尾，而頁首常有
    導覽列或前一篇文章的日期。
    """
    if not text:
        return ""
    matches = _STATED_DATE.findall(text)
    if not matches:
        return ""
    year, month, day = matches[-1]
    return format_publish_date(f"{year}-{month}-{day}")


def format_publish_date(value: str | None) -> str:
    """呈現用的 `YYYY-MM-DD`；解析不出來回空字串。

    解析失敗時**不**退回原字串。原字串可能是「不詳」、時間戳、或抽錯欄位的
    產物，把它照樣印在來源旁邊等於宣稱那是發布日期。顯示不出日期的來源就
    維持原本沒有日期的樣子——少一個資訊，好過給一個錯的。
    """
    parsed = parse_publish_date(value)
    return parsed.strftime(_DISPLAY_FORMAT) if parsed is not None else ""
