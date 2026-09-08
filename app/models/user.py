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
    gender: Literal["male", "female", "unknown"] = Field(
        ...,
        description="使用者性別。值刻意與前端 i18n key 的最後一段同名，前端才能直接以值拼出翻譯 key",
    )
    height: float = Field(..., gt=0, description="身高（公分）")
    weight: float = Field(..., gt=0, description="體重（公斤）")
    age: int = Field(..., ge=0, le=130, description="年齡")
    # 慢性病拆成兩欄而非一個 "、" 串起來的字串：固定選項要依使用者語言翻譯，
    # 自行輸入的病名則必須原文照留。混在同一個字串裡就分不出哪個是哪個，
    # 讀取端只能猜，猜錯就會把使用者打的字拿去翻譯。
    chronic_diseases: list[str] = Field(
        ...,
        description="固定選項的慢性病 code（如 hypertension）。與前端 i18n key 的最後一段同名",
    )
    chronic_custom: list[str] = Field(
        ...,
        description="使用者自行輸入的慢性病名。原文照存，任何時候都不翻譯",
    )
    major_illness_history: str = Field(..., description="重大疾病史。空字串代表沒有")
    surgery_history: str = Field(..., description="手術病史。空字串代表沒有")


class ProxyHealthUpdate(BaseModel):
    """代理寫入健康資料的請求體（`PUT /api/profiles/{userId}`）。

    與 `UserProfileData` 分開宣告，因為兩條路徑對「必填」的定義相反：

    - `PUT /me/update` 是本人送出整份表單，缺欄位代表表單沒填完，該擋。
    - 代理寫入是家人補填**部分**欄位，而 `name` 這類欄位**不歸這條路徑管**
      （見 `PROXY_WRITE_FORBIDDEN_FIELDS`）。沿用 `UserProfileData` 會讓
      endpoint 要求一個它隨即丟棄的必填欄位——呼叫端唯一的過關方式是送一個
      假值，那不是驗證，是儀式。

    因此所有欄位皆可省略，並以 `model_dump(exclude_unset=True)` 取出實際帶到
    的鍵。**未帶到的欄位不會進 `$set`**，資料庫既有的值原封不動。

    不可寫的欄位仍然宣告在這裡而非直接拒收：規格要求「送出含 `display_name`
    或 `picture_url` 的請求 SHALL NOT 修改這兩個欄位」——是不修改，不是回錯。
    它們會被 router 剝除並在 `skipped_fields` 回報，呼叫端因此知道自己送了
    不該送的東西，而不是靜靜地以為寫進去了。
    """

    gender: Optional[Literal["male", "female", "unknown"]] = Field(default=None)
    height: Optional[float] = Field(default=None, gt=0, description="身高（公分）")
    weight: Optional[float] = Field(default=None, gt=0, description="體重（公斤）")
    age: Optional[int] = Field(default=None, ge=0, le=130, description="年齡")
    chronic_diseases: Optional[list[str]] = Field(default=None)
    chronic_custom: Optional[list[str]] = Field(default=None)
    major_illness_history: Optional[str] = Field(default=None)
    surgery_history: Optional[str] = Field(default=None)

    # 以下皆為 PROXY_WRITE_FORBIDDEN_FIELDS，收得下但一律不寫入。
    name: Optional[str] = Field(default=None)
    display_name: Optional[str] = Field(default=None)
    picture_url: Optional[str] = Field(default=None)
    role: Optional[str] = Field(default=None)
    settings: Optional[Dict[str, Any]] = Field(default=None)
    line_id: Optional[str] = Field(default=None)


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
    # 每日醫療消息卡（medical-news-push）。預設開啟，與其他兩個通知開關一致。
    #
    # 既有使用者的文件沒有這個欄位，讀回為缺席；排程端以 `.get(..., True)` 解讀，
    # 因此不需要 backfill——缺席即等同預設值。
    notify_medical_news: bool = Field(
        default=True, description="是否啟用每日醫療消息推播"
    )
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
    notify_medical_news: Optional[bool] = Field(
        default=None, description="是否啟用每日醫療消息推播"
    )
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
