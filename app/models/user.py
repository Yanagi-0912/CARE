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
    language: Optional[str] = Field(
        default=None,
        description=(
            "使用者顯示語言。首次登入時以 LINE 帳號語言為預設值，"
            "之後若使用者在前端手動變更，以資料庫的值為準，不再被 LINE 覆蓋。"
        ),
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


# __all__表示當其他文件使用 import * 時，僅會匯入 UserProfileData 和
# UserProfile 這兩個類別。
__all__ = ["UserProfileData", "UserProfile"]
