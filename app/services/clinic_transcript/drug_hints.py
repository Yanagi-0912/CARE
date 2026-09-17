"""在看診逐字稿裡標出「這句可能在講你正在吃的某顆藥」。

### 為什麼比對的母體是長輩自己的用藥清單，不是全庫

看診逐字稿放棄了熱詞（custom_vocabulary 不能和語者分離並用，見
`app/services/speech/clinic_transcribe.py`），所以藥名一定會有聽錯的字。
直覺的補救是拿逐字稿去藥證庫模糊比對，但那條路比不修還糟：
`drug_catalog_service._match_by_fuzzy` 的實測註解記著，拿全庫 56,886 個品名當母體，
換掉一個劑量數字約 2% 必然釘錯，而且是「思樂康持續性藥效錠 30 毫克」配到
「300 毫克」這種差十倍的錯。語音辨識最常聽錯的偏偏就是數字。

這裡的母體改成這位長輩自己的用藥清單，通常是個位數。候選少兩個數量級，
配錯的機會完全是另一回事（門檻怎麼訂見 `MATCH_THRESHOLD` 的量測）。

### 只標示，不改寫

比對命中不會動逐字稿一個字。輸出是「逐字稿這裡寫 X，你清單上有 Y」，
兩邊並排讓人自己看。理由是命中只證明「這串字很像清單上的某顆藥」，
不證明「醫師講的就是那顆藥」——長輩可能正在講一顆還沒加進清單的新藥。

劑量數字更是連提示都不做（見 `_strip_dose`）：比對前就把劑量拿掉，
所以這裡永遠不會說「你聽錯劑量了」。那是全庫比對錯得最兇的地方，
在小母體上同樣沒有把握，而劑量講錯的後果是吃錯量。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable, Sequence

from app.services.medication.drug_catalog_service import normalize_drug_name

logger = logging.getLogger(__name__)

# 門檻 0.80。`scripts/measure_clinic_drug_hints.py` 2026-09-16 在真實藥證庫上量的
# （母體 56,702 個品名、抽 8 顆當一位長輩的清單、種子 20260916、每種各 3,000 筆）：
#
#   門檻    換掉一個字：標對／標錯藥／漏標      吞掉一個字：標對／標錯藥／漏標
#   0.75      89.8%  /  0.57%  /   9.6%         93.9%  /  0.67%  /   5.4%
#   0.78      80.6%  /  0.10%  /  19.3%         91.6%  /  0.23%  /   8.2%
#   0.80      80.2%  /  0.10%  /  19.7%         90.9%  /  0.23%  /   8.9%
#   0.82      68.2%  /  0.07%  /  31.7%         84.1%  /  0.00%  /  15.9%
#   0.85      56.7%  /  0.07%  /  43.2%         83.0%  /  0.00%  /  17.0%
#
# 兩種失敗不對稱：**標錯藥**（在講 A 藥卻標成清單上的 B 藥）會讓家人看錯，
# **漏標**只是少一個提示、逐字稿原文照樣在，所以門檻要往寧可漏標那邊偏。
#
# 0.75 → 0.78 之間標錯藥掉到約六分之一，是唯一明顯的轉折；再往上（0.82、0.85）
# 標錯藥最多再少 0.23 個百分點，漏標卻從 19.7% 爬到 43.2%。取剛過轉折的 0.80。
#
# 這是在乾淨的合成錯字上量的，真實的語音辨識錯誤會更雜（連錯好幾個字、整個詞聽成
# 別的詞），所以實際表現只會比這張表差。真的錄過幾次門診之後要拿真資料重量一次。
MATCH_THRESHOLD = 0.80

# 藥名比對前先拿掉劑量。「脈優錠5毫克」和「脈優錠10毫克」只差一個數字，
# 帶著劑量比對會讓相似度被數字主導；而我們本來就不打算對劑量發表意見。
_DOSE = re.compile(r"\d+(?:\.\d+)?\s*(?:MG|MCG|G|ML|IU|毫克|公絲|微克|克|毫升|單位)", re.I)
_DIGITS = re.compile(r"\d+(?:\.\d+)?")

# 逐字稿裡掃描窗長度相對藥名長度的伸縮範圍。聽錯常伴隨吞字或多字，
# 固定長度會漏掉；±2 是取捨後的值，沒有單獨量過。
_WINDOW_SLACK = 2

# 太短的藥名不比對。三個字是下限，不是隨便取的：「脈優錠」這種三字藥名很常見，
# 濾掉會漏掉真的藥；而三字的鍵要過 0.80 的門檻幾乎得整串對上（對中兩個字只有 0.67），
# 所以放到 3 不會把雜訊放進來。兩個字才會。
MIN_KEY_LENGTH = 3


@dataclass(frozen=True)
class DrugHint:
    """逐字稿某處可能在講清單上的某顆藥。

    `heard` 是逐字稿的原文（照抄，沒有修正），`medication_name` 是清單上的藥名。
    兩個都給，因為我們不宣稱哪一個才對。
    """

    medication_name: str
    heard: str
    start: int
    score: float


def _strip_dose(name: str) -> str:
    """去掉劑量後正規化。回傳空字串代表這個名稱不適合拿來比對。"""
    without_dose = _DOSE.sub("", name or "")
    return normalize_drug_name(without_dose)


def _normalized_with_index(text: str) -> tuple[str, list[int]]:
    """正規化逐字稿，同時記住每個字元在原文的位置，命中後才回得去原文。"""
    chars: list[str] = []
    positions: list[int] = []
    for index, char in enumerate(text):
        normalized = normalize_drug_name(char)
        if not normalized:
            continue
        for piece in normalized:
            chars.append(piece)
            positions.append(index)
    return "".join(chars), positions


def _best_window(haystack: str, key: str) -> tuple[float, int, int]:
    """在 haystack 裡找跟 key 最像的一段，回傳（分數, 起, 迄）。"""
    key_chars = set(key)
    best = (0.0, 0, 0)
    lengths = range(max(1, len(key) - _WINDOW_SLACK), len(key) + _WINDOW_SLACK + 1)
    for start in range(len(haystack)):
        # 便宜的前篩：窗的起點不在藥名用到的字裡，就不值得算相似度。
        if haystack[start] not in key_chars:
            continue
        for length in lengths:
            end = start + length
            if end > len(haystack):
                break
            score = SequenceMatcher(None, key, haystack[start:end]).ratio()
            if score > best[0]:
                best = (score, start, end)
    return best


def find_hints(
    transcript: str,
    medication_names: Iterable[str],
    threshold: float = MATCH_THRESHOLD,
) -> list[DrugHint]:
    """在逐字稿裡找出可能提到清單上藥品的位置。

    同一顆藥只回一則（分數最高的那處）：目的是提醒家人「這裡可能在講這顆藥」，
    重複標同一顆只會讓畫面變吵。
    """
    text = transcript or ""
    if not text.strip():
        return []

    haystack, positions = _normalized_with_index(text)
    if not haystack:
        return []

    hints: list[DrugHint] = []
    for name in medication_names:
        key = _strip_dose(name)
        if len(key) < MIN_KEY_LENGTH:
            continue
        score, start, end = _best_window(haystack, key)
        if score < threshold:
            continue
        origin_start = positions[start]
        origin_end = positions[min(end, len(positions)) - 1] + 1
        hints.append(
            DrugHint(
                medication_name=name,
                heard=text[origin_start:origin_end],
                start=origin_start,
                score=round(score, 3),
            )
        )

    hints.sort(key=lambda hint: hint.start)
    logger.info("stage=clinic_drug_hints hits=%d", len(hints))
    return hints


def dose_digits(text: str) -> list[str]:
    """把一段話裡的數字挑出來，只給畫面並排顯示用。

    刻意不做任何比較或修正：`_match_by_fuzzy` 的實測說劑量是全庫比對錯得最兇的地方，
    在小母體上也沒有把握，而講錯劑量的後果是吃錯量。並排讓人自己看就好。
    """
    return _DIGITS.findall(text or "")


def medication_names(medications: Sequence[object]) -> list[str]:
    """從用藥清單抽出可比對的名稱（品名與學名都算）。"""
    names: list[str] = []
    for medication in medications:
        for attribute in ("name", "generic_name"):
            value = getattr(medication, attribute, None)
            if isinstance(value, str) and value.strip():
                names.append(value.strip())
    return list(dict.fromkeys(names))
