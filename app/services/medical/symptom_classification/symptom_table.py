"""
載入症狀／科別對照表，並在載入時就把科別轉成資料庫真的查得到的值。

為什麼要在載入時 fail-fast：
    對照表的原始資料來自醫院網頁，科別名稱是各院自己的掛牌（「胃腸科（含肝膽）」
    「腎臟病科」「15歲以下兒童」），沒有一個落在 medicalFacilities.departments
    裡。若改成「線上查到解析不了的就跳過」，帳面覆蓋率與實際覆蓋率會不一致，
    而覆蓋率正是這個功能能不能上線的判準。表壞掉就該讓服務起不來。

為什麼候選排序是「人工優先序 > 跨院共識 > 院所數」：
    沒有人工指定時，三家醫院都把某症狀掛在同一科比只有一家可信，共識相同再看
    院所數——建議一個全台只有個位數院所的科別，使用者接著搜尋多半查無結果。

    人工優先序（rank）排在最前面則是後來翻過來的。原本的順序是「共識優先、
    人工其次」，理由是「跨院共識是事實，人工指定是判斷，事實優先於判斷」。
    那句話本身沒錯，錯在它把「哪一科收錄了這個症狀」當成「該先掛哪一科」。
    坐骨神經痛是實例：神經外科與復健科各有來源，骨科只有一家，於是骨科永遠
    排第三——但非外傷性的坐骨神經痛第一線就是骨科與復健科，先被導去神經外科
    等於把人推向手術科別。來源共識描述的是三份表怎麼分類，不是臨床動線。
    rank 因此保留給「臨床上該先去哪裡」，且一旦指定就蓋過共識；沒指定的條目
    行為完全不變。
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
MAX_CANDIDATES = 5


class SymptomTableError(RuntimeError):
    """對照表載入失敗。刻意不降級——帶著壞掉的表提供服務比不提供更糟。"""


@dataclass(frozen=True)
class DepartmentCandidate:
    """一個建議科別。canonical 保證存在於資料庫，可直接送進科別搜尋。"""

    canonical: str
    subgroup: str | tuple[str, ...] | None
    """原本的次專科方向（「胃腸肝膽」「心臟」），供回覆時補充說明用。

    可以是多個：同一個症狀在不同來源被分到不同的次專科，而那不是矛盾，是真的
    有兩條路。漏斗胸是實例——成大列在胸腔外科，台大雲林列在小兒外科，成人與
    小孩本來就走不同診。初版這裡只能存一個字串，整併時等於擲筊選一個，另一家
    的分法連同「有兩條路」這件事一起消失；使用者看到的標籤因此可能是對他年齡
    層錯誤的那一個。讀取一律走 subgroups，不要直接用這個欄位。
    """

    @property
    def subgroups(self) -> tuple[str, ...]:
        """次專科方向，一律以序列呈現。空值、單一字串、多值都收斂到這裡。"""
        if not self.subgroup:
            return ()
        if isinstance(self.subgroup, str):
            return (self.subgroup,)
        return tuple(self.subgroup)

    facility_count: int
    sources: tuple[str, ...] = ()
    """把這個症狀掛在這一科的來源代碼（V／N／Y）。

    只留數量不留代碼曾讓卡片無法列出正確出處：來源區塊只好把三家醫院全列，
    但一個症狀不可能每次都剛好三家都收錄，於是每張卡的出處都一樣、也都不精確。
    出處的用途是讓使用者能自己去核對，列到沒收錄這個症狀的醫院就是在誤導。
    """

    @property
    def source_count(self) -> int:
        """幾家來源醫院把這個症狀掛在這一科，用於排序與觀測。"""
        return len(self.sources)

    rank: int | None = None
    """人工指定的臨床優先序，數字小的排前面。省略者一律排在有指定者之後。

    指定了就蓋過來源共識與院所數。存在的理由是「哪幾家表收錄了這個症狀」與
    「臨床上該先掛哪一科」是兩件事，而排序要答的是後者：
      - 帶狀皰疹三個候選都是補列的（source_count 全為 0），不指定就退到院所
        數量——家醫科 1633 家、皮膚科 595 家，於是家醫科第一，但抗病毒藥有
        72 小時黃金期，先掛家醫科再轉診可能錯過。
      - 坐骨神經痛的骨科只有一家來源，神經外科與復健科各有兩家與一家，不指定
        就把人先導向手術科別。
    只指定其中幾個候選是可以的：有指定的照 rank 排在前，其餘維持原本的
    共識與院所數順序。
    """

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


def _subgroup_of(symptom: dict) -> str | tuple[str, ...] | None:
    """讀出 subgroup 欄位。JSON 允許字串或字串陣列，多值一律轉成 tuple。

    轉 tuple 而不是留 list 是因為 DepartmentCandidate 是 frozen dataclass，
    留 list 會讓它不可雜湊，之後任何 set／dict 的用法都會在執行期才炸。
    """
    raw = symptom.get("subgroup")
    if isinstance(raw, list):
        values = tuple(item for item in raw if item)
        return values or None
    return raw or None


# 排在最後的 rank，給沒有人工指定的候選。用一個大數而不是 None，是為了讓
# tuple 比較不必處理 None，同時保證「有指定」永遠贏過「沒指定」。
_UNRANKED = 1_000_000


def _candidate_sort_key(candidate: DepartmentCandidate) -> tuple[int, int, int, int]:
    """人工臨床優先序 > 來源共識 > 院所數量。見 rank 欄位與模組說明。"""
    return (
        0 if candidate.rank is not None else 1,
        candidate.rank if candidate.rank is not None else _UNRANKED,
        -candidate.source_count,
        -candidate.facility_count,
    )


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
                    subgroup=_subgroup_of(symptom),
                    facility_count=facility_count,
                    sources=tuple(symptom.get("sources") or ()),
                    rank=symptom.get("rank"),
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
