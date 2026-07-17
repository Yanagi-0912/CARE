from datetime import datetime
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field


class UserProfileData(BaseModel):
    """
    使用者健康資料的model

    設計目的：
    1. 集中定義 user profile 的欄位與驗證規則。
    2. 同一份欄位可被「API 請求模型」與「資料庫文件模型」重複使用。
    """

    # field 內的 ... 代表必填欄位。
    name: str = Field(..., min_length=1, description="使用者顯示名稱")
    gender: str = Field(..., min_length=1, description="使用者性別")
    height: float = Field(..., gt=0, description="身高（公分）")
    weight: float = Field(..., gt=0, description="體重（公斤）")
    age: int = Field(..., ge=0, le=130, description="年齡")

    # 慢性病史固定為字串格式。
    chronic_history: str = Field(..., description="慢性病史")

    # 下列兩個欄位目前以文字輸入
    major_illness_history: str = Field(..., description="重大疾病史")
    surgery_history: str = Field(..., description="手術病史")


class UserSettings(BaseModel):
    """
    使用者介面偏好設定。

    跟 picture_url 不同，這裡的欄位都跟「使用者可在前端自行調整的偏好」有關。
    其中 language 比較特別：首次登入時以 LINE 帳號語言為預設值（由 service 層
    在建立時帶入），之後若使用者手動變更，一律以資料庫的值為準，不再被 LINE 覆蓋；
    其餘欄位（字體大小、通知、語音回覆等）則完全跟 LINE 無關，預設值直接採用
    App 端目前的預設。
    """

    language: Optional[str] = Field(
        default=None,
        description=(
            "使用者顯示語言。首次登入時以 LINE 帳號語言為預設值，"
            "之後若使用者在前端手動變更，以資料庫的值為準，不再被 LINE 覆蓋。"
        ),
    )
    font_size: Literal["normal", "large", "xlarge"] = Field(
        default="large", description="字體大小"
    )
    high_contrast: bool = Field(default=True, description="是否啟用高對比模式")
    notify_reminder: bool = Field(default=True, description="是否啟用用藥提醒通知")
    notify_family: bool = Field(default=True, description="是否啟用家人健康通知")
    voice_reply_enabled: bool = Field(default=False, description="是否啟用語音回覆")


class UserSettingsUpdate(BaseModel):
    """
    更新使用者設定用的模型，所有欄位皆為可選。

    只有實際帶入（non-null）的欄位才會被更新，
    未帶入的欄位維持資料庫原值，避免部分更新時覆蓋掉其他設定。
    """

    language: Optional[str] = Field(default=None, description="使用者顯示語言")
    font_size: Optional[Literal["normal", "large", "xlarge"]] = Field(
        default=None, description="字體大小"
    )
    high_contrast: Optional[bool] = Field(default=None, description="是否啟用高對比模式")
    notify_reminder: Optional[bool] = Field(default=None, description="是否啟用用藥提醒通知")
    notify_family: Optional[bool] = Field(default=None, description="是否啟用家人健康通知")
    voice_reply_enabled: Optional[bool] = Field(default=None, description="是否啟用語音回覆")

    # 健康諮詢記錄使用 JSON 物件，預設空 dict
    health_consultations: Dict[str, Any] = Field(
        default_factory=dict,
        description="Health consultation records in JSON format",
    )

    # 使用者偏好：是否啟用語音回覆，預設為 True（啟用）
    voice_reply_enabled: bool = Field(
        default=True,
        description="Whether to enable voice reply from AI agent (default: True)",
    )

# 這裡userprofile繼承userprofiledata，並且加上line_id、created_at、updated_at等欄位
class UserProfile(UserProfileData):
    """
    資料庫中的 user profile 文件模型。

    相較於 UserProfileData，這裡額外補上：
    - line_id: 主識別（LINE UID）
    - created_at / updated_at: DB 寫入時的時間戳
    """

    line_id: str = Field(..., min_length=1, description="LINE 使用者 ID")
    picture_url: Optional[str] = Field(
        default=None,
        description="LINE 使用者頭像網址",
    )
    settings: UserSettings = Field(
        default_factory=UserSettings,
        description="使用者介面偏好設定（字體大小、高對比、通知、語音回覆等）",
    )
    created_at: Optional[datetime] = Field(
        default=None,
        description="資料建立時間（UTC）",
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        description="資料最後更新時間（UTC）",
    )

    @classmethod
    def from_upsert(cls, line_id: str, payload: Dict[str, Any]) -> "UserProfile":
        """
        由 upsert 參數建立 UserProfile，並在建立時觸發完整驗證。

        使用情境：
        - service 層拿到 API payload 後，先轉成模型再交給 repository
        - 可避免未驗證的 dict 直接進入 DB 操作
        """

        return cls(line_id=line_id, **payload)

    def to_payload(self) -> Dict[str, Any]:
        """
        將模型序列化成 repository 可直接寫入的 payload。
        """
        # 這裡排除 created_at 和 updated_at，因為這兩個欄位由 repository 在寫入時自動添加。
        return self.model_dump(exclude={"created_at", "updated_at"})


# __all__表示當其他文件使用 import * 時，僅會匯入下列類別。
__all__ = ["UserProfileData", "UserProfile", "UserSettings", "UserSettingsUpdate"]
