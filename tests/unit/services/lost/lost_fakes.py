"""走失求救測試共用的替身。

FakeLostRepository 照 LostSessionRepository 的語意在記憶體裡實作（同一位長輩
只有一次進行中、推播權只有一方搶得到、軌跡上限）。查詢語法本身另外在真的 Mongo
上驗過；這裡要測的是服務在這些語意之上的行為。
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

from linebot.v3.messaging import FlexMessage

from app.repositories.lost_session_repository import ACTIVE, TRAIL_MAX_POINTS

T0 = datetime(2026, 9, 16, 8, 0, tzinfo=timezone.utc)

ELDER = "U_ELDER"
DAUGHTER = "U_DAUGHTER"
SON = "U_SON"


class Clock:
    def __init__(self, now: datetime = T0):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now = self.now + timedelta(**kwargs)


class FakeLostRepository:
    def __init__(self):
        self.docs: list[dict] = []

    async def create_active(self, document):
        for doc in self.docs:
            if doc["user_id"] == document["user_id"] and doc["status"] == ACTIVE:
                return copy.deepcopy(doc), False
        self.docs.append(copy.deepcopy(document))
        return copy.deepcopy(document), True

    def _active(self, user_id):
        for doc in self.docs:
            if doc["user_id"] == user_id and doc["status"] == ACTIVE:
                return doc
        return None

    async def find_active(self, user_id):
        doc = self._active(user_id)
        return copy.deepcopy(doc) if doc else None

    async def find_latest(self, user_id):
        mine = [d for d in self.docs if d["user_id"] == user_id]
        if not mine:
            return None
        return copy.deepcopy(max(mine, key=lambda d: d["started_at"]))

    async def append_location(self, user_id, point, received_at):
        doc = self._active(user_id)
        if doc is None:
            return None
        doc["last_location"] = dict(point)
        doc["last_seen_at"] = received_at
        doc["stale_notified_at"] = None
        doc["trail"].append({"lat": point["lat"], "lng": point["lng"], "at": received_at})
        doc["trail"] = doc["trail"][-TRAIL_MAX_POINTS:]
        return copy.deepcopy(doc)

    async def claim_notice(self, session_id, field, now, extra_filter=None):
        for doc in self.docs:
            if doc["session_id"] != session_id or doc.get(field) is not None:
                continue
            if extra_filter and any(doc.get(k) != v for k, v in extra_filter.items()):
                return False
            doc[field] = now
            return True
        return False

    async def end_active(self, user_id, status, ended_by, now, extra_filter=None):
        doc = self._active(user_id)
        if doc is None:
            return None
        if extra_filter and any(doc.get(k) != v for k, v in extra_filter.items()):
            return None
        doc.update(status=status, ended_at=now, ended_by=ended_by)
        return copy.deepcopy(doc)

    async def find_stale(self, cutoff):
        return [
            copy.deepcopy(d)
            for d in self.docs
            if d["status"] == ACTIVE
            and d["last_seen_at"] is not None
            and d["last_seen_at"] < cutoff
            and d["stale_notified_at"] is None
        ]

    async def find_due_to_end(self, now):
        return [
            copy.deepcopy(d)
            for d in self.docs
            if d["status"] == ACTIVE and d["auto_end_at"] <= now
        ]


class FakeReplier:
    """貼齊 LineReplier：push_flex 收 SDK FlexMessage、回 bool。"""

    def __init__(self, flex_ok=True):
        self.flex: list[tuple[str, FlexMessage]] = []
        self.texts: list[tuple[str, str]] = []
        self.replied_flex: list[dict] = []
        self.replies: list[dict] = []
        self._flex_ok = flex_ok

    async def push_flex(self, user_id, flex):
        assert isinstance(flex, FlexMessage)
        if not self._flex_ok:
            return False
        self.flex.append((user_id, flex))
        return True

    async def push_text(self, user_id, text):
        self.texts.append((user_id, text))
        return True

    async def reply_flex(self, reply_token, flex_message, user_id):
        assert isinstance(flex_message, FlexMessage)
        self.replied_flex.append(
            {"reply_token": reply_token, "flex": flex_message, "user_id": user_id}
        )
        return True

    async def reply(self, **kwargs):
        self.replies.append(kwargs)
        return True

    def flex_to(self, user_id):
        return [f for uid, f in self.flex if uid == user_id]

    def texts_to(self, user_id):
        return [text for uid, text in self.texts if uid == user_id]


class FakeAuthorization:
    def __init__(self, recipients=(DAUGHTER, SON), error=None):
        self.recipients = list(recipients)
        self._error = error
        self.calls = []

    async def notification_recipients(self, owner_id, kind):
        self.calls.append((owner_id, kind))
        if self._error is not None:
            raise self._error
        return list(self.recipients)


class FakeProfiles:
    def __init__(self, profiles=None):
        self.profiles = profiles or {
            ELDER: {"name": "王阿公", "settings": {"language": "zh-TW"}},
            DAUGHTER: {"name": "美玲", "settings": {"language": "zh-TW"}},
            SON: {"name": "John", "settings": {"language": "en", "font_size": "xlarge"}},
        }

    async def get_user_profile(self, user_id):
        return copy.deepcopy(self.profiles.get(user_id))
