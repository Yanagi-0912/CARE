"""
載入症狀／科別對照表，並在載入時就把科別轉成資料庫真的查得到的值。

為什麼要在載入時 fail-fast：
    對照表的原始資料來自醫院網頁，科別名稱是各院自己的掛牌（「胃腸科（含肝膽）」
    「腎臟病科」「15歲以下兒童」），沒有一個落在 medicalFacilities.departments
    裡。若改成「線上查到解析不了的就跳過」，帳面覆蓋率與實際覆蓋率會不一致，
    而覆蓋率正是這個功能能不能上線的判準。表壞掉就該讓服務起不來。

    來源與欄位比照辦理：沒有來源的候選、未登記的來源代碼、白名單以外的欄位、
    重複的「症狀＋科別」一律在載入時拒絕。這張表之後會併入更多醫院，手工合併
    最容易出的錯——新增一列而不是補代碼、打錯代碼、順手加個欄位——都該在啟動時
    炸開，而不是在卡片上變成錯的家數。

為什麼表裡只有來源所載的對應：
    表在這個功能裡的角色是轉述醫院怎麼分類。本專案曾自行加入醫院表上沒有的
    候選、並以人工指定順序，但那是臨床判斷，混在同一張表、用同一種卡片呈現，
    使用者分不出哪一項有醫院佐證（design 決策 14）。缺口由併入更多醫院補齊，
    不在這裡補。

為什麼候選排序是「跨院共識 > 院所數」：
    多家醫院都把某症狀掛在同一科比只有一家可信；共識相同再看院所數——建議一個
    全台只有個位數院所的科別，使用者接著搜尋多半查無結果。院所數代表的是
    「找不找得到」，不是建議的信心度。
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
    / "symptom_department_table"
    / "symptom_department_reference.json"
)

# 超過這個數量的候選就代表這個症狀本來就不該由對照表回答（腹痛可以是內科、
# 外科、婦產科、泌尿科…），改走保底建議，不硬挑五個充數。
MAX_CANDIDATES = 5

# 症狀條目允許的欄位（spec「對照表載入時強制轉為部定專科並驗證來源」）。出現其他
# 欄位即載入失敗：只擋已知的壞欄位（rank、origin）擋不住下一個。
_ALLOWED_SYMPTOM_FIELDS: frozenset[str] = frozenset(
    {
        # 症狀條目名稱，也是比對層的封閉候選集合：使用者的口語會被對應到其中一條。
        # 依 design 決策 11 為改寫後的用語，不是醫院原文。
        "term",
        # 把此症狀列在此科的來源醫院代碼，每個代碼都須登記在表頭 sources 區塊。
        "sources",
        # 次專科方向，即掛號時要指名的診（如「胸腔內科」）。字串、字串陣列或 null；
        # 陣列代表有多條路徑，卡片以標籤呈現。
        "subgroup",
        # 條目類型（symptom 症狀／disease 疾病名／service 服務／procedure 處置／
        # screening 篩檢），僅供維護者分類，程式不讀。
        "kind",
        # 維護紀錄（改寫前原文、整併依據），程式不讀，SHALL NOT 出現在回覆中。
        "note",
        # 依 downgrade_rule 併入母科別時，記錄原本的科別。程式不讀。
        "downgraded_from",
    }
)


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
    """把這個症狀掛在這一科的來源代碼，對應表頭 sources 區塊。

    只留數量不留代碼曾讓卡片無法列出正確出處：來源區塊只好把三家醫院全列，
    但一個症狀不可能每次都剛好三家都收錄，於是每張卡的出處都一樣、也都不精確。
    出處的用途是讓使用者能自己去核對，列到沒收錄這個症狀的醫院就是在誤導。
    """

    @property
    def source_count(self) -> int:
        """幾家來源醫院把這個症狀掛在這一科，即卡片標註中的 M。"""
        return len(self.sources)


@dataclass(frozen=True)
class SymptomEntry:
    term: str
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


def _candidate_sort_key(candidate: DepartmentCandidate) -> tuple[int, int]:
    """跨院共識 > 院所數量。見模組說明。"""
    return (-candidate.source_count, -candidate.facility_count)


def _facility_count_of(block: dict) -> int:
    """
    讀出科別區塊的院所數。必須是正整數，否則拋錯。

    缺值不當成 0、字串與小數不做轉換：那代表資料寫錯了，而院所數決定同家數
    候選的先後，靜默帶著錯值上線只會讓排序悄悄跑掉。bool 是 int 的子類別，
    要先排除。
    """
    value = block.get("db_facility_count")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SymptomTableError(
            f"科別 {block.get('canonical')!r} 的 db_facility_count 必須是正整數，"
            f"實際為 {value!r}"
        )
    return value


def _checked_term(symptom: dict, registered_sources: set[str], *, where: str) -> str:
    """驗證一筆症狀條目，回傳去除空白後的 term。任何一項不合即拋錯。"""
    extra = set(symptom) - _ALLOWED_SYMPTOM_FIELDS
    if extra:
        raise SymptomTableError(
            f"科別 {where!r} 的症狀條目 {symptom.get('term')!r} 含不允許的欄位 "
            f"{sorted(extra)}"
        )

    term = (symptom.get("term") or "").strip()
    if not term:
        raise SymptomTableError(f"科別 {where!r} 有一筆症狀條目的 term 為空")

    sources = symptom.get("sources")
    if not isinstance(sources, list) or not sources:
        raise SymptomTableError(f"{where!r}／{term!r} 的 sources 為空或不是陣列")
    if len(sources) != len(set(sources)):
        raise SymptomTableError(f"{where!r}／{term!r} 的來源代碼重複：{sources}")
    unregistered = [code for code in sources if code not in registered_sources]
    if unregistered:
        raise SymptomTableError(
            f"{where!r}／{term!r} 含未登記的來源代碼 {unregistered}，"
            "請先在表頭 sources 區塊登記"
        )
    return term


def load_symptom_table(path: Path | None = None) -> SymptomTable:
    """
    從 JSON 載入對照表。任何一項不合規格即拋錯，不做部分載入。

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
        # 不擋載入：審定狀態是給人看的訊號，不是開關（本能力不設功能旗標，見
        # design 決策 10）。這裡的責任是讓「線上跑的是未審定資料」在啟動日誌
        # 留下紀錄，而不是靜靜地用下去。
        logger.warning(
            f"{LOGGER_HEADER_TEXT} 對照表 status=%r（非 verified），"
            "內容尚未經人工審定，不應用於正式回覆",
            raw.get("status"),
        )

    departments = raw.get("departments")
    if not isinstance(departments, list) or not departments:
        raise SymptomTableError("症狀對照表缺少 departments")

    registered_sources = set(raw.get("sources") or {})
    collected: dict[str, list[DepartmentCandidate]] = {}
    seen: set[tuple[str, str]] = set()

    for block in departments:
        raw_canonical = block.get("canonical")
        match = resolve_department(raw_canonical or "")
        if match is None:
            raise SymptomTableError(
                f"科別 {raw_canonical!r} 無法解析為資料庫存在的部定專科，"
                "請先修正對照表或補上 DEPARTMENT_ALIASES"
            )

        facility_count = _facility_count_of(block)

        for symptom in block.get("symptoms", []):
            term = _checked_term(symptom, registered_sources, where=raw_canonical)

            # 以解析後的部定專科判斷重複：兩個科別區塊可能解析成同一科。
            key = (term, match.canonical)
            if key in seen:
                raise SymptomTableError(
                    f"重複的症狀與科別：{term!r} 在 {match.canonical!r} 出現超過一次。"
                    "併入新醫院時應把代碼加進既有那一列的 sources，而不是新增一列"
                )
            seen.add(key)

            collected.setdefault(term, []).append(
                DepartmentCandidate(
                    canonical=match.canonical,
                    subgroup=_subgroup_of(symptom),
                    facility_count=facility_count,
                    sources=tuple(symptom["sources"]),
                )
            )

    if not collected:
        raise SymptomTableError("症狀對照表沒有任何可用條目")

    entries = {
        term: SymptomEntry(
            term=term,
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
