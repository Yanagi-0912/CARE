"""使用者年齡的 request-scoped ContextVar。

與 user_language、user_font_size 採同一套模式：Webhook 進來時由 handler 設定，
LangChain tool 這類拿不到使用者參數的地方則直接讀 ContextVar。

為什麼症狀科別建議需要它：
    對照表有 11 條症狀同時掛在兒科與成人科別（腹痛、發燒、咳嗽…），因為那些
    症狀本來就大人小孩都會有。少了年齡，成人問「我肚子好痛要掛哪一科」會拿到
    「內科、兒科」——兒科那一項對他毫無意義，卻佔掉了三個候選的其中一個。

為什麼不從訊息裡猜年齡：
    猜錯的方向不對稱且無法驗證。年齡是使用者自己填的事實（UserSettings.age），
    直接讀比從「我肚子痛」推論可靠得多。訊息只用來補一種年齡蓋不到的情況：
    成人帳號幫小孩問（見 symptom_classification.normalizer.mentions_child）。
"""

from __future__ import annotations

from contextvars import ContextVar, Token

# 兒科的年齡界線。三份來源不一致（14／15／18 歲皆有），本專案採 15 歲以下，
# 決策記於 symptom_department_reference.json 的 resolved_questions。
PEDIATRIC_AGE_LIMIT = 15

# 預設 None 而非某個數字：拿不到年齡時應該表現為「不知道」，由呼叫端決定要
# 保守還是寬鬆，而不是被一個假的預設值誤導。
_request_age: ContextVar[int | None] = ContextVar("care_request_age", default=None)


def normalize_user_age(age: object) -> int | None:
    """非整數、負數或超出人類範圍的值一律視為未知，不讓髒資料影響科別篩選。"""
    if isinstance(age, bool) or not isinstance(age, int):
        return None
    if 0 <= age <= 130:
        return age
    return None


def get_request_age() -> int | None:
    return normalize_user_age(_request_age.get())


def set_request_age(age: object) -> Token:
    return _request_age.set(normalize_user_age(age))


def reset_request_age(token: Token) -> None:
    _request_age.reset(token)


def is_pediatric_age(age: int | None) -> bool:
    """年齡未知時回 False——未知不等於是小孩。"""
    return age is not None and age < PEDIATRIC_AGE_LIMIT
