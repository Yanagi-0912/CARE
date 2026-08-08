"""
已知院所名稱的索引，用來判斷一段文字裡的「診所／醫院／藥局」是專名的一部分，
還是使用者在表達類型偏好。

## 為什麼需要這個模組

判斷「皇家診所在哪」的「診所」是專名還是泛稱，先前試過三輪字元規則
（連接詞白名單 → 緊鄰語法標記 → 標記分三層），每一輪都在新的方向開破口：

- 第一輪：擋掉「評價不錯的診所」這類合法泛稱
- 第二輪：「家」是標記字，但「皇家」「全家」「我家」「和睦家」都是真實院所名
- 第三輪：修好了 35 家誤判中的 33 家，代價是「好的診所」「多家診所」失效
  （類型詞前綴剛好兩個字時判不出來）

根本原因是用字元形態去猜一個**事實問題**：這串字是不是某家院所的名字。
這個問題資料庫裡有答案，不需要猜。實測 19,528 筆院所名稱：
先前所有誤判案例（皇家診所、全家診所、我家診所、和睦家診所、美的診所、
一家牙醫診所、遠東聯想牙醫診所）**全部**是資料庫中的完整院所名稱；
而 14 個泛稱片語（好的診所、多家診所、評價不錯的診所、綜合醫院…）
**沒有一個**與院所名稱碰撞。

## 為什麼還需要別名表

資料庫存的是正式名稱，使用者講的常是口語簡稱：「台大醫院」在資料庫裡叫
「國立臺灣大學醫學院附設醫院」，查不到。這類簡稱由
`medical_facility_matcher.FACILITY_ALIASES` 覆蓋（臺大／成大／馬偕／榮總…），
兩者互補：索引管登記名稱，別名表管口語簡稱。

## 索引沒載入時的行為

索引由 `app/dependencies.py` 在啟動時預載。未載入時（單元測試、啟動失敗）
`is_known_facility_name()` 一律回 False，判定會退回「視為泛稱」——
方向是漏判而非誤判，退回的是未套類型過濾的現況行為，不會給出錯誤結果。
"""

import logging

from app.services.medical.facility_type_matcher import normalize_facility_type_text

logger = logging.getLogger(__name__)

# 資料庫中最長的院所名稱為 34 字。比對候選子字串時以此為上界，
# 避免對整段使用者輸入做無界掃描。
MAX_FACILITY_NAME_LENGTH = 40

_facility_names: frozenset[str] = frozenset()


def configure_facility_names(names: frozenset[str] | set[str] | list[str]) -> None:
    """
    由 composition root 在啟動時注入院所名稱集合。

    名稱在此處就正規化，與查詢端使用同一個 `normalize_facility_type_text`——
    兩邊必須用同一套規則，否則比對必然落空：資料庫裡的
    「金小兒科診所(光榮聯合診所)」在查詢端會被去掉括號變成
    「金小兒科診所光榮聯合診所」，若索引存的是原始字串就永遠對不上。
    把正規化收在寫入端，呼叫者不可能忘記做。
    """
    global _facility_names
    _facility_names = frozenset(
        normalized for n in names if (normalized := normalize_facility_type_text(n))
    )
    logger.info(
        "[FacilityNameIndex] 已載入院所名稱索引，共 %s 筆", len(_facility_names)
    )


def is_index_loaded() -> bool:
    """索引是否已載入。未載入時所有判定一律回 False（漏判而非誤判）。"""
    return bool(_facility_names)


def is_known_facility_name(text: str) -> bool:
    """text 是否為資料庫中的完整院所名稱。"""
    return bool(text) and text in _facility_names


def covers_known_facility_name(text: str, start: int, end: int) -> bool:
    """
    `text[start:end]` 這一段，是否被某個已知院所名稱完整包住？

    用於判斷比對到的類型詞是不是專名的一部分：
    「皇家診所在哪」的「診所」落在名稱「皇家診所」內 → 是專名；
    「評價不錯的診所」的「診所」不在任何院所名稱內 → 是泛稱。

    刻意做「包住」而不是「前綴以名稱結尾」：類型詞未必在名稱的尾巴。
    實測資料庫有 16 筆院所的名稱中段就有類型詞，例如
    「金小兒科診所(光榮聯合診所)」正規化後為「金小兒科診所光榮聯合診所」，
    第一個「診所」在中間，只看前綴結尾會漏掉。

    搜尋範圍以 MAX_FACILITY_NAME_LENGTH 為界（資料庫最長名稱 34 字），
    因此成本與輸入長度無關，只與名稱長度上限有關。
    """
    if not _facility_names or start < 0 or end <= start:
        return False

    lo = max(0, end - MAX_FACILITY_NAME_LENGTH)
    hi = min(len(text), start + MAX_FACILITY_NAME_LENGTH)
    for name_start in range(lo, start + 1):
        for name_end in range(end, hi + 1):
            if text[name_start:name_end] in _facility_names:
                return True
    return False
