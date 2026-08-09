from datetime import datetime
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field


class UserProfileData(BaseModel):
    """
    使用者健康資料的 model。

    設計目的：
    1. 集中定義 user profile 的欄位與驗證規則。
    2. 同一份欄位可被 API 請求模型與資料庫文件模型重複使用。
    """

    name: str = Field(..., min_length=1, description="使用者顯示名稱")
    gender: str = Field(..., min_length=1, description="使用者性別")
    height: float = Field(..., gt=0, description="身高（公分）")
    weight: float = Field(..., gt=0, description="體重（公斤）")
    age: int = Field(..., ge=0, le=130, description="年齡")
    chronic_history: str = Field(..., description="慢性病史")
    major_illness_history: str = Field(..., description="重大疾病史")
    surgery_history: str = Field(..., description="手術病史")


class UserSettings(BaseModel):
    """
    使用者介面偏好設定。

    language 在首次登入時以 LINE 帳號語言作為預設值，之後以資料庫值為準。
    其他欄位則屬於前端可自行調整的本地偏好。
    """

    language: Optional[str] = Field(
        default=None,
        description=(
            "使用者顯示語言。首次登入時以 LINE 帳號語言為預設值，"
            "之後若使用者在前端手動變更，以資料庫的值為準。"
        ),
    )
    font_size: Literal["normal", "large", "xlarge"] = Field(
        default="large",
        description="字體大小",
    )
    high_contrast: bool = Field(default=True, description="是否啟用高對比模式")
    notify_reminder: bool = Field(default=True, description="是否啟用用藥提醒通知")
    notify_family: bool = Field(default=True, description="是否啟用家人健康通知")
    voice_reply_enabled: bool = Field(default=False, description="是否啟用語音回覆")
    voice_rate: Literal["slow", "normal", "fast"] = Field(
        default="normal",
        description="語音回覆語速",
    )
    voice_gender: Literal["female", "male"] = Field(
        default="female",
        description="語音回覆音色性別",
    )


class UserSettingsUpdate(BaseModel):
    """
    更新使用者設定用的模型，所有欄位皆為可選。
    """

    language: Optional[str] = Field(default=None, description="使用者顯示語言")
    font_size: Optional[Literal["normal", "large", "xlarge"]] = Field(
        default=None,
        description="字體大小",
    )
    high_contrast: Optional[bool] = Field(default=None, description="是否啟用高對比模式")
    notify_reminder: Optional[bool] = Field(default=None, description="是否啟用用藥提醒通知")
    notify_family: Optional[bool] = Field(default=None, description="是否啟用家人健康通知")
    voice_reply_enabled: Optional[bool] = Field(default=None, description="是否啟用語音回覆")
    voice_rate: Optional[Literal["slow", "normal", "fast"]] = Field(
        default=None,
        description="語音回覆語速",
    )
    voice_gender: Optional[Literal["female", "male"]] = Field(
        default=None,
        description="語音回覆音色性別",
    )


class UserProfile(UserProfileData):
    """資料庫中的 user profile 文件模型。"""

    line_id: str = Field(..., min_length=1, description="LINE 使用者 ID")
    role: Literal["admin", "user"] = Field(default="user", description="使用者角色")
    picture_url: Optional[str] = Field(default=None, description="LINE 使用者頭像網址")
    settings: UserSettings = Field(
        default_factory=UserSettings,
        description="使用者介面偏好設定",
    )
    created_at: Optional[datetime] = Field(default=None, description="資料建立時間（UTC）")
    updated_at: Optional[datetime] = Field(default=None, description="資料最後更新時間（UTC）")

    @classmethod
    def from_upsert(cls, line_id: str, payload: Dict[str, Any]) -> "UserProfile":
        return cls(line_id=line_id, **payload)

    def to_payload(self) -> Dict[str, Any]:
        return self.model_dump(exclude={"created_at", "updated_at"})


__all__ = ["UserProfileData", "UserProfile", "UserSettings", "UserSettingsUpdate"]
