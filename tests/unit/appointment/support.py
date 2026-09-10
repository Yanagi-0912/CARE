"""掛號提醒測試共用的替身。

`FakeCollection` 是一個只實作 AppointmentReminderRepository 用得到的那幾個
Motor 方法與運算子的記憶體 collection。刻意不用 MagicMock 斷言查詢字典：掛號
提醒的正確性幾乎全落在時間窗的邊界（T-1h 那一分鐘算不算、當日結束那一刻算
不算），斷言「查詢長這樣」驗不到「查詢真的挑中對的文件」。

它也照 Motor（未開 tz_aware）的行為把 aware datetime 以 naive UTC 存回——模型層
的時區還原若漏了一處，測試會在這裡看到相差 8 小時。
"""

import copy
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Iterable, Optional

from fastapi import HTTPException

from app.models.appointment import AppointmentReminder, day_end_for, utc_offset_minutes

TPE = timezone(timedelta(hours=8))

PATIENT = "U_PATIENT"
DAUGHTER = "U_DAUGHTER"
SON = "U_SON"
STRANGER = "U_STRANGER"

APPOINTMENT_AT = datetime(2026, 9, 15, 9, 30, tzinfo=TPE)

_MISSING = object()


def to_storage(value: Any) -> Any:
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    if isinstance(value, dict):
        return {key: to_storage(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_storage(item) for item in value]
    return value


def _compare(op: str, value: Any, arg: Any) -> bool:
    if op == "$in":
        return value in arg
    if op == "$ne":
        return value != arg
    if op == "$exists":
        return (value is not _MISSING) == bool(arg)
    if value is _MISSING or value is None:
        return False
    if op == "$lte":
        return value <= arg
    if op == "$lt":
        return value < arg
    if op == "$gte":
        return value >= arg
    if op == "$gt":
        return value > arg
    raise NotImplementedError(op)


def matches(doc: dict, query: dict) -> bool:
    for key, cond in query.items():
        if key == "$and":
            if not all(matches(doc, sub) for sub in cond):
                return False
            continue
        if key == "$or":
            if not any(matches(doc, sub) for sub in cond):
                return False
            continue
        value = doc.get(key, _MISSING)
        if isinstance(cond, dict) and cond and all(k.startswith("$") for k in cond):
            for op, arg in cond.items():
                current = None if value is _MISSING and op in ("$in", "$ne") else value
                if not _compare(op, current, to_storage(arg)):
                    return False
        else:
            actual = None if value is _MISSING else value
            if actual != to_storage(cond):
                return False
    return True


def _apply(doc: dict, update: dict) -> None:
    for op, fields in update.items():
        if op == "$set":
            for key, value in fields.items():
                doc[key] = to_storage(copy.deepcopy(value))
        elif op == "$inc":
            for key, value in fields.items():
                doc[key] = doc.get(key, 0) + value
        else:
            raise NotImplementedError(op)


class _Cursor:
    def __init__(self, docs: list[dict]):
        self._docs = docs

    def sort(self, key: str, direction: int = 1) -> "_Cursor":
        self._docs.sort(key=lambda doc: doc.get(key), reverse=direction < 0)
        return self

    async def to_list(self, length: Optional[int] = None) -> list[dict]:
        return list(self._docs)


class FakeCollection:
    def __init__(self) -> None:
        self.docs: list[dict] = []
        self.indexes: list[tuple] = []

    async def create_index(self, keys, **kwargs):
        self.indexes.append((keys, kwargs))
        return kwargs.get("name")

    async def insert_one(self, doc: dict):
        stored = to_storage(copy.deepcopy(doc))
        assert all(existing["_id"] != stored["_id"] for existing in self.docs)
        self.docs.append(stored)
        return SimpleNamespace(inserted_id=stored["_id"])

    async def find_one(self, query: dict):
        for doc in self.docs:
            if matches(doc, query):
                return copy.deepcopy(doc)
        return None

    def find(self, query: dict) -> _Cursor:
        return _Cursor([copy.deepcopy(doc) for doc in self.docs if matches(doc, query)])

    async def update_one(self, query: dict, update: dict):
        for doc in self.docs:
            if matches(doc, query):
                before = copy.deepcopy(doc)
                _apply(doc, update)
                return SimpleNamespace(matched_count=1, modified_count=int(doc != before))
        return SimpleNamespace(matched_count=0, modified_count=0)

    async def update_many(self, query: dict, update: dict):
        modified = 0
        for doc in self.docs:
            if matches(doc, query):
                before = copy.deepcopy(doc)
                _apply(doc, update)
                modified += int(doc != before)
        return SimpleNamespace(modified_count=modified)

    async def find_one_and_update(self, query: dict, update: dict, return_document=None):
        for doc in self.docs:
            if matches(doc, query):
                _apply(doc, update)
                return copy.deepcopy(doc)
        return None

    async def delete_one(self, query: dict):
        for index, doc in enumerate(self.docs):
            if matches(doc, query):
                del self.docs[index]
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)


def make_appointment(at: datetime = APPOINTMENT_AT, **overrides: Any) -> AppointmentReminder:
    offset = utc_offset_minutes(at)
    fields: dict[str, Any] = {
        "user_id": PATIENT,
        "creator_user_id": PATIENT,
        "appointment_at": at,
        "appointment_utc_offset_minutes": offset,
        "day_end_at": day_end_for(at, offset),
        "facility_id": "abc123",
        "hospital_name": "台大醫院",
        "department": "心臟內科",
        "created_at": at - timedelta(days=3),
        "updated_at": at - timedelta(days=3),
    }
    fields.update(overrides)
    return AppointmentReminder(**fields)


class RecordingReplier:
    """記錄每一則推播與回覆；`failing` 裡的收件人推播一律失敗（push_flex 回 False）。"""

    def __init__(self, failing: Iterable[str] = ()) -> None:
        self.failing = set(failing)
        self.pushes: list[tuple[str, Any]] = []
        self.replies: list[dict] = []
        self.flex_replies: list[dict] = []

    async def push_flex(self, user_id: str, flex_message: Any) -> bool:
        self.pushes.append((user_id, flex_message))
        return user_id not in self.failing

    async def reply(self, **kwargs: Any) -> None:
        self.replies.append(kwargs)

    async def reply_flex(self, **kwargs: Any) -> None:
        self.flex_replies.append(kwargs)


def rendered(flex_message: Any) -> str:
    """把一則 Flex 攤平成字串（含 altText）供內容斷言。altText 會出現在聊天室
    列表與推播通知上，隱私斷言必須連它一起檢查。"""
    return json.dumps(
        {"alt": flex_message.alt_text, "contents": flex_message.contents.to_dict()},
        ensure_ascii=False,
    )


class FakeProfiles:
    def __init__(self, profiles: Optional[dict[str, dict]] = None) -> None:
        self.profiles = profiles or {}

    async def get_user_profile(self, user_id: str):
        return self.profiles.get(user_id)


class FakeAuthz:
    """家庭授權的替身：誰對就診者有 GENERAL 寫入權、家屬名單是誰。"""

    def __init__(
        self,
        writers: Iterable[str] = (),
        recipients: Iterable[str] = (),
        fail_recipients: bool = False,
    ) -> None:
        self.writers = set(writers)
        self.recipients = list(recipients)
        self.fail_recipients = fail_recipients
        self.recipient_calls: list[tuple[str, str]] = []
        self.authorize_calls: list[tuple] = []

    async def authorize(self, operator_id, target_owner_id, classification, action, **kwargs):
        self.authorize_calls.append((operator_id, target_owner_id, classification, action))
        if operator_id == target_owner_id or operator_id in self.writers:
            return "GUARDIAN"
        raise HTTPException(
            status_code=403,
            detail=f"權限不足：您對此使用者的{classification}資料沒有{action}權限",
        )

    async def notification_recipients(self, subject_owner_id, kind, now=None):
        self.recipient_calls.append((subject_owner_id, kind))
        if self.fail_recipients:
            raise RuntimeError("tree lookup failed")
        return list(self.recipients)
