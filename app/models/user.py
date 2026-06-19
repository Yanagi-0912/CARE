from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class UserProfileData(BaseModel):
    """
    使用者健康資料的model

    設計目的：
    1. 集中定義 user profile 的欄位與驗證規則。
    2. 同一份欄位可被「API 請求模型」與「資料庫文件模型」重複使用。
    """

    # field 內的 ... 代表必填欄位。
    name: str = Field(..., min_length=1, description="User display name")
    gender: str = Field(..., min_length=1, description="User gender")
    height: float = Field(..., gt=0, description="Height in cm")
    weight: float = Field(..., gt=0, description="Weight in kg")
    age: int = Field(..., ge=0, le=130, description="Age in years")

    # 慢性病史固定為字串格式。
    chronic_history: str = Field(..., description="Chronic disease history")

    # 下列兩個欄位目前以文字輸入
    major_illness_history: str = Field(..., description="Major illness history")
    surgery_history: str = Field(..., description="Surgery history")

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

    line_id: str = Field(..., min_length=1, description="LINE user ID")
    picture_url: Optional[str] = Field(
        default=None,
        description="LINE user avatar URL",
    )
    created_at: Optional[datetime] = Field(
        default=None,
        description="UTC timestamp when profile was created",
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        description="UTC timestamp when profile was last updated",
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


# __all__表示當其他文件使用 import * 時，僅會匯入 UserProfileData 和
# UserProfile 這兩個類別。
__all__ = ["UserProfileData", "UserProfile"]
