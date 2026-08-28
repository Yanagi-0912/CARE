"""
載入症狀／科別對照表，並在載入時就把科別轉成資料庫真的查得到的值。

為什麼要在載入時 fail-fast：
    對照表的原始資料來自醫院網頁，科別名稱是各院自己的掛牌（「胃腸科（含肝膽）」
    「腎臟病科」「15歲以下兒童」），沒有一個落在 medicalFacilities.departments
    裡。若改成「線上查到解析不了的就跳過」，帳面覆蓋率與實際覆蓋率會不一致，
    而覆蓋率正是這個功能能不能上線的判準。表壞掉就該讓服務起不來。

為什麼候選排序是「跨院共識優先、院所數其次」：
    三家醫院都把某症狀掛在同一科，比只有一家這樣分類更可信。共識相同時，
    院所數多的排前面——建議一個全台只有個位數院所的科別，使用者接著搜尋
    多半查無結果，等於把「找不到」的責任轉嫁給使用者。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from app.services.medical.department_matcher import resolve_department

logger = logging.getLogger(__name__)

LOGGER_HEADER_TEXT = "[Services:SymptomTable]"

DEFAULT_TABLE_PATH = (
    Path(__file__).resolve().parents[4]
    / "resources"
    / "symsptom_department_table"
    / "symptom_department_reference.json"
)

# 超過這個數量的候選就代表這個症狀本來就不該由對照表回答（腹痛可以是內科、
# 外科、婦產科、泌尿科…），改走保底建議，不硬挑三個充數。
MAX_CANDIDATES = 3


class SymptomTableError(RuntimeError):
    """對照表載入失敗。刻意不降級——帶著壞掉的表提供服務比不提供更糟。"""


@dataclass(frozen=True)
class DepartmentCandidate:
    """一個建議科別。canonical 保證存在於資料庫，可直接送進科別搜尋。"""

    canonical: str
    subgroup: str | None
    """原本的次專科方向（「胃腸肝膽」「心臟」），供回覆時補充說明用。"""

    facility_count: int
    source_count: int
    """幾家來源醫院把這個症狀掛在這一科，用於排序與觀測。"""

    note: str | None = None


@dataclass(frozen=True)
class SymptomEntry:
    term: str
    kind: str
    candidates: tuple[DepartmentCandidate, ...]

    @property
    def is_too_broad(self) -> bool:
        """候選過多代表這個症狀跨科，對照表不該給出方向。"""
        return len(self.candidates) > MAX_CANDIDATES


class SymptomTable:
    """症狀 → 候選科別的查表。建構時完成所有驗證，之後純讀取。"""

    def __init__(self, entries: dict[str, SymptomEntry], *, verified: bool) -> None:
        self._entries = entries
        self._verified = verified

    @property
    def verified(self) -> bool:
        return self._verified

    @property
    def terms(self) -> tuple[str, ...]:
        """所有可比對的症狀條目；同時是正規化層 LLM 兜底的封閉候選集合。"""
        return tuple(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def lookup(self, term: str) -> SymptomEntry | None:
        return self._entries.get(term)


def _candidate_sort_key(candidate: DepartmentCandidate) -> tuple[int, int]:
    return (-candidate.source_count, -candidate.facility_count)


def load_symptom_table(path: Path | None = None) -> SymptomTable:
    """
    從 JSON 載入對照表。任何科別無法解析成部定專科即拋錯。

    回傳的 SymptomTable 只包含 symptoms。對照表 JSON 裡的 red_flags 區塊刻意
    不載入：急迫度改由 urgency.py 的語意判斷器負責，而那份 red_flags 是爬蟲以
    關鍵字初篩出來的，實測明顯過寬（「中風復健」被標成急症），拿來當急迫度來源
    只會把大量一般問句推去急診。
    """
    table_path = path or DEFAULT_TABLE_PATH
    try:
        raw = json.loads(table_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SymptomTableError(f"找不到症狀對照表：{table_path}") from exc
    except json.JSONDecodeError as exc:
        raise SymptomTableError(f"症狀對照表不是合法 JSON：{table_path}") from exc

    verified = raw.get("status") == "verified"
    if not verified:
        # 不擋載入：目前的表就是 unverified，擋了功能永遠開不起來。這裡的責任
        # 是讓「線上跑的是未審定資料」在啟動日誌留下紀錄，而不是靜靜地用下去。
        logger.warning(
            f"{LOGGER_HEADER_TEXT} 對照表 status=%r（非 verified），"
            "內容尚未經人工審定，不應用於正式回覆",
            raw.get("status"),
        )

    departments = raw.get("departments")
    if not isinstance(departments, list) or not departments:
        raise SymptomTableError("症狀對照表缺少 departments")

    collected: dict[str, list[DepartmentCandidate]] = {}
    kinds: dict[str, str] = {}

    for block in departments:
        raw_canonical = block.get("canonical")
        match = resolve_department(raw_canonical or "")
        if match is None:
            raise SymptomTableError(
                f"科別 {raw_canonical!r} 無法解析為資料庫存在的部定專科，"
                "請先修正對照表或補上 DEPARTMENT_ALIASES"
            )

        facility_count = int(block.get("db_facility_count") or 0)

        for symptom in block.get("symptoms", []):
            term = (symptom.get("term") or "").strip()
            if not term:
                continue

            collected.setdefault(term, []).append(
                DepartmentCandidate(
                    canonical=match.canonical,
                    subgroup=symptom.get("subgroup"),
                    facility_count=facility_count,
                    source_count=len(symptom.get("sources") or ()),
                    note=symptom.get("note"),
                )
            )
            kinds.setdefault(term, symptom.get("kind") or "symptom")

    if not collected:
        raise SymptomTableError("症狀對照表沒有任何可用條目")

    entries = {
        term: SymptomEntry(
            term=term,
            kind=kinds[term],
            candidates=tuple(sorted(candidates, key=_candidate_sort_key)),
        )
        for term, candidates in collected.items()
    }

    logger.info(
        f"{LOGGER_HEADER_TEXT} 載入完成：%d 個症狀條目、%d 個科別、verified=%s",
        len(entries),
        len(departments),
        verified,
    )
    return SymptomTable(entries, verified=verified)


@dataclass(frozen=True)
class SourceReference:
    """對照表的來源醫院。卡片的「參考來源」逐條列出的就是這些。"""

    code: str
    name: str
    url: str


def load_source_references(path: Path | None = None) -> tuple[SourceReference, ...]:
    """
    讀出對照表的 sources 區塊，供 Flex 卡片條列來源用。

    與 load_symptom_table 分開的理由：卡片只需要來源的名稱與網址，不該為了顯示
    一段連結而載入整張表；同時這裡刻意 fail-soft——來源列不出來只是少了一段
    出處，不值得讓回覆整個失敗。
    """
    table_path = path or DEFAULT_TABLE_PATH
    try:
        raw = json.loads(table_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning(f"{LOGGER_HEADER_TEXT} 無法讀取來源清單：%s", table_path)
        return ()

    references = []
    for code, meta in (raw.get("sources") or {}).items():
        name = (meta or {}).get("name")
        url = (meta or {}).get("url")
        if name and url:
            references.append(SourceReference(code=code, name=name, url=url))
    return tuple(references)
