"""看診錄音在 LINE 聊天室裡的「等錄音」狀態（clinic_recording_sessions）。

聊天室沒有頁面可以放狀態：使用者按了「看診錄音」、選了醫師同意與否之後，下一則
語音才是看診錄音，而不是要問 agent 的問題。這一步到那一步中間可能隔好幾個小時
（候診），也可能遇到 backend 重啟，所以存資料庫而不是放記憶體。

一位使用者同時只有一筆（`recorder_id` 唯一）：重按一次就是重新開始。

過期交給 Mongo 的 TTL 索引，同 clinic_visit_records。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from app.db.mongodb import MongoDBManager
from app.models.clinic_transcript import ConsentMode

logger = logging.getLogger(__name__)

# 從掛號提醒按下「看診時錄音」到真的錄完。提醒在看診前 1 小時，台灣門診候診
# 一兩個小時是常態，再加上看診本身；超過就當成忘了，下一則語音照常當問題處理。
SESSION_TTL = timedelta(hours=3)

SessionStep = Literal["awaiting_consent", "awaiting_audio"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ClinicRecordingSession(BaseModel):
    # 按下錄音的人（LINE user id）。家人陪診時跟 user_id 不同。
    recorder_id: str
    # 就診者。
    user_id: str
    step: SessionStep = "awaiting_consent"
    consent: Optional[ConsentMode] = None
    appointment_id: Optional[str] = None
    hospital_name: str = ""
    department: str = ""
    # 先傳了長錄音、還沒回答同意與否：先記下訊息 ID，答完再去 LINE 下載。
    pending_message_id: Optional[str] = None
    pending_file_name: Optional[str] = None
    expires_at: datetime = Field(default_factory=lambda: _now() + SESSION_TTL)


def _collection(collection: Optional[Any] = None) -> Any:
    if collection is not None:
        return collection
    return MongoDBManager.get_database()["clinic_recording_sessions"]


class ClinicRecordingSessionRepository:
    @staticmethod
    async def ensure_indexes(collection: Optional[Any] = None) -> None:
        col = _collection(collection)
        await col.create_index(
            [("expires_at", 1)],
            name="clinic_recording_sessions_expires_at_ttl",
            expireAfterSeconds=0,
        )
        await col.create_index(
            [("recorder_id", 1)], name="clinic_recording_sessions_recorder", unique=True
        )

    @staticmethod
    async def save(
        session: ClinicRecordingSession, collection: Optional[Any] = None
    ) -> None:
        await _collection(collection).replace_one(
            {"recorder_id": session.recorder_id}, session.model_dump(), upsert=True
        )

    @staticmethod
    async def get(
        recorder_id: str, collection: Optional[Any] = None
    ) -> Optional[ClinicRecordingSession]:
        # TTL 索引每 60 秒才掃一次，過期但還沒被刪的要自己濾掉。
        document = await _collection(collection).find_one(
            {"recorder_id": recorder_id, "expires_at": {"$gt": _now()}}
        )
        if document is None:
            return None
        document.pop("_id", None)
        return ClinicRecordingSession.model_validate(document)

    @staticmethod
    async def delete(recorder_id: str, collection: Optional[Any] = None) -> bool:
        result = await _collection(collection).delete_one({"recorder_id": recorder_id})
        return bool(result.deleted_count)
