"""年齡的正規化與兒科界線。

刻意沒有「發話者年齡」的 request-scoped ContextVar：年齡屬於看診者，不屬於
發話者。「我兒子發燒」要看的是兒子幾歲，發話者 40 歲與此無關；科別建議改從
PatientContext 取年齡（tasks 10.8），那個 ContextVar 在 10.20 確認沒有讀者後刪除，
避免日後又有人拿它當成病人的年齡（design 決策 16）。
"""

from __future__ import annotations

# 兒科的年齡界線。三份來源不一致（玉里 15 歲以下、成大 18 歲含以下、台大雲林
# 18 歲以下），本專案採未滿 15 歲，滿 15 歲即對應成人科別；決策記於
# symptom_department_reference.json 兒科區塊的 age_note。
PEDIATRIC_AGE_LIMIT = 15

def normalize_user_age(age: object) -> int | None:
    """非整數、負數或超出人類範圍的值一律視為未知，不讓髒資料影響科別篩選。"""
    if isinstance(age, bool) or not isinstance(age, int):
        return None
    if 0 <= age <= 130:
        return age
    return None


def is_pediatric_age(age: int | None) -> bool:
    """年齡未知時回 False——未知不等於是小孩。"""
    return age is not None and age < PEDIATRIC_AGE_LIMIT
