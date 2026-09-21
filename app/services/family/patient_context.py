"""發話者與實際照護對象的不可變人物脈絡。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from app.models.family_tree import FamilyMember
from app.services.family.person_resolution import PersonResolution

Gender = Literal["male", "female", "unknown"]


class ValueSource(str, Enum):
    MESSAGE = "message"
    PROFILE = "profile"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PatientCandidate:
    user_id: str
    display_label: str
    relationship: str | None


@dataclass(frozen=True)
class PatientContext:
    operator_id: str
    patient_kind: Literal[
        "self", "member", "ambiguous", "not_found", "conflict"
    ]
    patient_id: str | None = None
    display_label: str = ""
    relationship: str | None = None
    candidates: tuple[PatientCandidate, ...] = ()
    age: int | None = None
    age_source: ValueSource = ValueSource.UNKNOWN
    gender: Gender = "unknown"
    gender_source: ValueSource = ValueSource.UNKNOWN

    def __post_init__(self) -> None:
        if not self.operator_id:
            raise ValueError("operator_id 不可為空")
        if self.patient_kind in {"self", "member"} and not self.patient_id:
            raise ValueError(f"{self.patient_kind} 必須有 patient_id")
        if self.patient_kind not in {"self", "member"} and self.patient_id is not None:
            raise ValueError(f"{self.patient_kind} 不可帶 patient_id")


def build_patient_context(
    *,
    operator_id: str,
    resolution: PersonResolution,
    message_age: object = None,
    message_gender: object = None,
    profile_age: object = None,
    profile_gender: object = None,
) -> PatientContext:
    """把解析結果與可信資料合成 context；呼叫端仍須先完成 profile 授權。"""
    patient_id = _patient_id(operator_id, resolution)
    candidates = tuple(_candidate(member) for member in resolution.candidates)

    # 未連結、歧義與衝突人物沒有可安全歸屬的 profile。即使呼叫端誤傳，這裡也
    # 不使用；本輪訊息中明確附在該人物上的值仍可保留。
    may_use_profile = resolution.kind in {"self", "member"}
    age, age_source = _select_age(
        message_age,
        profile_age if may_use_profile else None,
    )
    gender, gender_source = _select_gender(
        message_gender,
        profile_gender if may_use_profile else None,
    )

    return PatientContext(
        operator_id=operator_id,
        patient_kind=resolution.kind,
        patient_id=patient_id,
        display_label=resolution.display_label,
        relationship=resolution.relationship,
        candidates=candidates,
        age=age,
        age_source=age_source,
        gender=gender,
        gender_source=gender_source,
    )


def _patient_id(operator_id: str, resolution: PersonResolution) -> str | None:
    if resolution.kind == "self":
        return operator_id
    if resolution.kind == "member":
        if resolution.member is None:
            raise ValueError("member 解析結果缺少 member")
        return resolution.member.user_id
    return None


def _candidate(member: FamilyMember) -> PatientCandidate:
    return PatientCandidate(
        user_id=member.user_id,
        display_label=(member.display_name or "").strip(),
        relationship=member.relationship_type,
    )


def _valid_age(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= 130 else None


def _select_age(message_value: object, profile_value: object) -> tuple[int | None, ValueSource]:
    message_age = _valid_age(message_value)
    if message_age is not None:
        return message_age, ValueSource.MESSAGE
    profile_age = _valid_age(profile_value)
    if profile_age is not None:
        return profile_age, ValueSource.PROFILE
    return None, ValueSource.UNKNOWN


def _valid_gender(value: object) -> Gender | None:
    return value if value in {"male", "female"} else None


def _select_gender(message_value: object, profile_value: object) -> tuple[Gender, ValueSource]:
    message_gender = _valid_gender(message_value)
    if message_gender is not None:
        return message_gender, ValueSource.MESSAGE
    profile_gender = _valid_gender(profile_value)
    if profile_gender is not None:
        return profile_gender, ValueSource.PROFILE
    return "unknown", ValueSource.UNKNOWN


__all__ = [
    "Gender",
    "PatientCandidate",
    "PatientContext",
    "ValueSource",
    "build_patient_context",
]
