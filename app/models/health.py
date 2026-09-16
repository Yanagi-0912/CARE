"""個人健康紀錄：血壓、血糖量測與提醒範圍、經期紀錄、計步工作階段、健康提醒
推播的節流 claim。

一個 change 涵蓋四種對外資源，各自的請求與回應模型都放在這裡
（openspec/changes/personal-health-tracking/design.md「資料格式」）：

- ``health_measurement``（血壓／血糖量測，SENSITIVE）
- ``health_alert_threshold``（提醒範圍，SENSITIVE）
- ``menstrual_record``（經期，PERSONAL——見
  ``app/models/family_authorization.py`` 對 PERSONAL 的說明）
- ``step_count``（計步，SENSITIVE；儲存文件 ``StepSession`` 是內部實作，
  SHALL NOT 出現在任何回應，因此刻意不登記進 ``FIELD_CLASSIFICATION``）

以及沒有回應模型的 ``health_alert_claims``（節流紀錄，同
``app/repositories/safety_alert_repository.py`` 的模式）。

輸入的合理範圍只攔截打錯字，不是臨床判定
（design.md「已確認的數值」；specs 各資源的對應 Requirement 亦同）。
"""

import re
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Optional
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TAIPEI_TZ = ZoneInfo("Asia/Taipei")

DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 合理輸入範圍（design.md「已確認的數值」）：只攔截打錯字，不是臨床判定。
SYSTOLIC_MIN, SYSTOLIC_MAX = 50, 300
DIASTOLIC_MIN, DIASTOLIC_MAX = 30, 200
PULSE_MIN, PULSE_MAX = 30, 250
GLUCOSE_MIN, GLUCOSE_MAX = 20, 800

# 量測時間 SHALL NOT 晚於送出時間 5 分鐘以上（容許手機時鐘誤差）；
# 早於送出時間的量測（補記）SHALL 被接受。
MEASURED_AT_MAX_FUTURE_MINUTES = 5

# 經期天數上限：結束日期與開始日期的間隔 SHALL NOT 超過 15 天（攔截輸入錯誤，
# 不是臨床判定——FIGO 的「正常經期 ≤ 8 天」是異常通知的門檻，不是這裡的輸入
# 範圍，見 health-alerts spec「經期異常只通知本人」）。
MENSTRUAL_MAX_SPAN_DAYS = 15

MeasurementKind = Literal["blood_pressure", "blood_glucose"]
MealContext = Literal["fasting", "before_meal", "after_meal", "bedtime", "random"]
HealthLevel = Literal["within_range", "above_range", "below_range", "no_threshold"]
MenstrualFlow = Literal["light", "medium", "heavy"]


def _today_taipei_str() -> str:
    return datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")


def _reject_more_than_5_minutes_future(
    value: Optional[datetime], field_label: str
) -> Optional[datetime]:
    """量測時間／工作階段開始時間可以是過去（補記／稍早開始），但 SHALL NOT
    晚於送出當下 5 分鐘以上（容許手機時鐘誤差）。

    未帶時區的 datetime 視為 UTC——同 ``app/models/medication.py`` 的
    ``ensure_aware_utc`` 慣例：Motor client 未啟用 tz_aware，時間經資料庫
    存取一圈後會變成 naive UTC，這裡的驗證器需要能接住兩種形狀。
    """
    if value is None:
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    if value > now + timedelta(minutes=MEASURED_AT_MAX_FUTURE_MINUTES):
        raise ValueError(
            f"{field_label}不得晚於送出時間 {MEASURED_AT_MAX_FUTURE_MINUTES} 分鐘以上"
        )
    return value


def _reject_far_future(value: Optional[datetime]) -> Optional[datetime]:
    return _reject_more_than_5_minutes_future(value, "量測時間")


# ── 血壓／血糖量測 ──────────────────────────────────────────────────────


class CreateBloodPressureRequest(BaseModel):
    """新增一筆血壓紀錄（health-measurements spec「記錄血壓」）。

    ``measured_at`` 未提供時由服務層補上送出當下時間；這裡只驗證「有提供
    時」的範圍與上下限關係。

    ``extra="forbid"``：POST /api/health/measurements 的 body 型別是
    ``Union[CreateBloodPressureRequest, CreateBloodGlucoseRequest]``，兩者
    沒有共同的 ``kind`` 欄位可資區分，靠的是 Pydantic smart union 依「哪個
    模型驗證得過」來選。預設允許多餘欄位時，一份同時帶血壓與血糖欄位（或
    欄位打錯字、混進不相關欄位）的請求會被這個模型直接吃下、悄悄丟棄它看
    不懂的欄位並驗證成功，得到一筆種類錯誤的紀錄而不是 422。禁止多餘欄位
    後，這種請求兩個模型都驗證不過，才會如預期回 422。
    """

    model_config = ConfigDict(extra="forbid")

    systolic: int = Field(ge=SYSTOLIC_MIN, le=SYSTOLIC_MAX)
    diastolic: int = Field(ge=DIASTOLIC_MIN, le=DIASTOLIC_MAX)
    pulse: Optional[int] = Field(default=None, ge=PULSE_MIN, le=PULSE_MAX)
    measured_at: Optional[datetime] = None

    @field_validator("measured_at")
    @classmethod
    def _validate_measured_at(cls, value: Optional[datetime]) -> Optional[datetime]:
        return _reject_far_future(value)

    @model_validator(mode="after")
    def _validate_systolic_greater_than_diastolic(self) -> "CreateBloodPressureRequest":
        if self.systolic <= self.diastolic:
            raise ValueError("收縮壓必須大於舒張壓")
        return self


class CreateBloodGlucoseRequest(BaseModel):
    """新增一筆血糖紀錄（health-measurements spec「記錄血糖」）。單位固定為
    mg/dL；系統 SHALL NOT 接受 mmol/L——這裡的整數範圍本身已排除 mmol/L
    常見的個位數／十位數輸入，介面另外在輸入處標示單位。

    ``extra="forbid"``：見 ``CreateBloodPressureRequest`` 的同一段說明——
    兩個模型沒有共同的 ``kind`` 欄位，靠 Pydantic smart union 選型別；不禁止
    多餘欄位的話，混雜血壓欄位的請求會在這裡被悄悄吃下並驗證成功。
    """

    model_config = ConfigDict(extra="forbid")

    glucose_mg_dl: int = Field(ge=GLUCOSE_MIN, le=GLUCOSE_MAX)
    meal_context: MealContext
    measured_at: Optional[datetime] = None

    @field_validator("measured_at")
    @classmethod
    def _validate_measured_at(cls, value: Optional[datetime]) -> Optional[datetime]:
        return _reject_far_future(value)


class HealthMeasurement(BaseModel):
    """``health_measurements`` 的儲存與回應形狀
    （design.md「資料格式」＞``health_measurements``）。

    血壓與血糖共用同一份文件形狀，``kind`` 決定哪一組欄位有值。刻意不在這裡
    重跑輸入範圍驗證：範圍是攔截打錯字的門檻，寫入當下已經在
    ``CreateBloodPressureRequest``／``CreateBloodGlucoseRequest`` 檢查過，日後
    範圍常數若調整，不該讓舊紀錄連讀都讀不回來。

    每一個欄位都已在 ``app/models/family_authorization.py`` 的
    ``FIELD_CLASSIFICATION`` 登記為 SENSITIVE（見該檔案，以及
    ``tests/unit/models/test_family_authorization.py`` 的守門測試）。
    """

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    user_id: str
    kind: MeasurementKind
    measured_at: datetime
    recorded_by: str
    systolic: Optional[int] = None
    diastolic: Optional[int] = None
    pulse: Optional[int] = None
    glucose_mg_dl: Optional[int] = None
    meal_context: Optional[MealContext] = None
    level: HealthLevel
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ── 提醒範圍 ────────────────────────────────────────────────────────────


class UpdateHealthAlertThresholdRequest(BaseModel):
    """設定提醒範圍（health-alerts spec「使用者自訂提醒範圍」）。六項皆為
    選填；同一對上下限都設定時，上限 SHALL 大於下限——相等視為不合法，
    因為數值恰好等於上限或下限時判定為範圍內，上下限相等會讓「範圍內」
    永遠是空集合。
    """

    systolic_high: Optional[int] = Field(default=None, ge=SYSTOLIC_MIN, le=SYSTOLIC_MAX)
    systolic_low: Optional[int] = Field(default=None, ge=SYSTOLIC_MIN, le=SYSTOLIC_MAX)
    diastolic_high: Optional[int] = Field(default=None, ge=DIASTOLIC_MIN, le=DIASTOLIC_MAX)
    diastolic_low: Optional[int] = Field(default=None, ge=DIASTOLIC_MIN, le=DIASTOLIC_MAX)
    glucose_fasting_high: Optional[int] = Field(
        default=None, ge=GLUCOSE_MIN, le=GLUCOSE_MAX
    )
    glucose_nonfasting_high: Optional[int] = Field(
        default=None, ge=GLUCOSE_MIN, le=GLUCOSE_MAX
    )
    glucose_low: Optional[int] = Field(default=None, ge=GLUCOSE_MIN, le=GLUCOSE_MAX)

    @model_validator(mode="after")
    def _validate_upper_greater_than_lower(
        self,
    ) -> "UpdateHealthAlertThresholdRequest":
        # 血糖有兩個上限（空腹／餐前 vs. 餐後、睡前、隨機）共用同一個下限
        # （health-alerts spec「等級判定」），因此下限要對兩個上限各自比對一次。
        pairs = (
            ("systolic_high", "systolic_low"),
            ("diastolic_high", "diastolic_low"),
            ("glucose_fasting_high", "glucose_low"),
            ("glucose_nonfasting_high", "glucose_low"),
        )
        for upper_field, lower_field in pairs:
            upper = getattr(self, upper_field)
            lower = getattr(self, lower_field)
            if upper is not None and lower is not None and upper <= lower:
                raise ValueError(f"{upper_field} 必須大於 {lower_field}")
        return self


class HealthAlertThreshold(BaseModel):
    """``health_alert_thresholds`` 的儲存與回應形狀。沒有文件等同全部未設定
    （design.md）；唯一鍵是 ``user_id``，不是 ``_id``。

    刻意不繼承 ``UpdateHealthAlertThresholdRequest``、不重複它的 ``ge``/
    ``le`` 範圍與「上限必須大於下限」的 pair 驗證——同 ``HealthMeasurement``
    的設計（見該類別 docstring）：範圍是攔截打錯字的門檻，寫入當下已經在
    請求模型檢查過；若這裡也繼承驗證，日後範圍常數調整、或上下限關係的
    規則調整，會讓不再符合新規則的舊文件連讀（``get``／``upsert`` 的
    ``find_one`` 回讀）都讀不回來，這正是 ``HealthMeasurement`` 要避免的
    失效模式。
    """

    model_config = ConfigDict(populate_by_name=True)

    user_id: str
    systolic_high: Optional[int] = None
    systolic_low: Optional[int] = None
    diastolic_high: Optional[int] = None
    diastolic_low: Optional[int] = None
    glucose_fasting_high: Optional[int] = None
    glucose_nonfasting_high: Optional[int] = None
    glucose_low: Optional[int] = None
    updated_by: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ── 經期 ────────────────────────────────────────────────────────────────


class CreateMenstrualRecordRequest(BaseModel):
    """新增一筆經期紀錄（menstrual-cycle-log spec「記錄經期」）。日期以台北
    時區的日曆日表示，格式固定 ``YYYY-MM-DD``。
    """

    start_date: str
    end_date: Optional[str] = None
    flow: Optional[MenstrualFlow] = None
    note: Optional[str] = Field(default=None, max_length=200)

    @field_validator("start_date")
    @classmethod
    def _validate_start_date(cls, value: str) -> str:
        if not DATE_PATTERN.match(value):
            raise ValueError("start_date 格式須為 YYYY-MM-DD")
        if value > _today_taipei_str():
            raise ValueError("開始日期不得晚於今天")
        return value

    @field_validator("end_date")
    @classmethod
    def _validate_end_date_format(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not DATE_PATTERN.match(value):
            raise ValueError("end_date 格式須為 YYYY-MM-DD")
        return value

    @model_validator(mode="after")
    def _validate_end_date_range(self) -> "CreateMenstrualRecordRequest":
        if self.end_date is None:
            return self
        start = date.fromisoformat(self.start_date)
        end = date.fromisoformat(self.end_date)
        if end < start:
            raise ValueError("結束日期不得早於開始日期")
        if (end - start).days > MENSTRUAL_MAX_SPAN_DAYS:
            raise ValueError(f"經期天數不得超過 {MENSTRUAL_MAX_SPAN_DAYS} 天")
        return self


class UpdateMenstrualRecordRequest(BaseModel):
    """部分更新一筆經期紀錄（menstrual-cycle-log spec「事後補上結束日期」：
    「本人 SHALL 能事後補上結束日期或修正紀錄」）。四個欄位皆選填，服務層以
    ``model_dump(exclude_unset=True)`` 決定要覆寫哪些欄位——沒帶到的維持
    既有值；明確帶 ``end_date: null`` 則代表清除既有結束日期，把這筆紀錄
    重新打開為進行中（dispatch notes「PATCH 語意」）。

    這裡刻意只驗證「有帶值時」單一欄位自己的形狀（日期格式、``note`` 長度、
    ``flow`` 列舉），不驗證跨欄位的一致性（結束不早於開始、間隔不超過 15
    天、開始不晚於今天）——PATCH 可能只帶其中一個欄位，此時另一個欄位的值
    來自既有紀錄，這個模型看不到。整筆的一致性由服務層在把這次的更新併入
    既有紀錄之後重新驗證（同 ``CreateMenstrualRecordRequest`` 的規則）。
    """

    model_config = ConfigDict(extra="forbid")

    start_date: Optional[str] = None
    end_date: Optional[str] = None
    flow: Optional[MenstrualFlow] = None
    note: Optional[str] = Field(default=None, max_length=200)

    @field_validator("start_date")
    @classmethod
    def _validate_start_date_format(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not DATE_PATTERN.match(value):
            raise ValueError("start_date 格式須為 YYYY-MM-DD")
        return value

    @field_validator("end_date")
    @classmethod
    def _validate_end_date_format(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not DATE_PATTERN.match(value):
            raise ValueError("end_date 格式須為 YYYY-MM-DD")
        return value


class MenstrualRecord(BaseModel):
    """``menstrual_records`` 的儲存與回應形狀（design.md「資料格式」）。

    ``cycle_length_days``／``period_length_days`` 由服務層依「前一筆的開始
    日期」與「本筆的結束日期」算好後以 ``model_copy`` 回填；寫入時、以及單純
    從資料庫讀出一筆時一律是 ``None``，不落地存進資料庫——同
    ``app/models/medication.py`` 的 ``Medication.thumbnail_url`` 慣例（見該
    欄位註解），計算結果依存於其他文件，不該在自己的文件裡存一份必然過期
    的快照。

    ``note`` 刻意不重複 ``CreateMenstrualRecordRequest`` 的 ``max_length=200``
    ——同 ``HealthAlertThreshold`` 不重複請求模型的範圍驗證的理由
    （見該類別 docstring）：長度上限是攔截輸入的門檻，寫入當下已經在請求
    模型檢查過；這裡若也套用，日後上限調整會讓超過新上限的舊紀錄讀不回來。

    全部欄位皆登記為 PERSONAL（``app/models/family_authorization.py``），
    任何跨使用者回應一律被剔除。
    """

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    user_id: str
    start_date: str
    end_date: Optional[str] = None
    flow: Optional[MenstrualFlow] = None
    note: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    cycle_length_days: Optional[int] = None
    period_length_days: Optional[int] = None


# ── 計步 ────────────────────────────────────────────────────────────────


class StepSessionSyncRequest(BaseModel):
    """``PUT /api/health/steps/sessions/{session_id}`` 的 body（step-counter
    spec「同步的冪等性」「日期歸屬」「合理性檢查」）。前端回報的是工作階段
    **目前的累計值**，SHALL NOT 是增量；後端保留每個工作階段收過的最大值
    （見 ``StepSessionRepository.sync_progress``）。

    ``session_id`` 是路徑參數（``pydantic.UUID4``，見 router），不在這個
    body 裡；``model_config = ConfigDict(extra="forbid")`` 讓誤帶它的請求
    直接 422，不被悄悄忽略。

    ``started_at`` 是客戶端記錄的工作階段開始時間，只在後端**第一次**收到
    這個工作階段時採用，用來換算日期歸屬（台北日曆日）與合理性檢查的起點；
    之後的同步即使帶了不同的 ``started_at`` 也 SHALL 被忽略，一律沿用第一次
    的值——同 ``measured_at`` 的道理，SHALL NOT 晚於送出當下 5 分鐘以上。

    「自工作階段開始起算、平均每秒超過 5 步」的合理性檢查需要工作階段既有
    的 ``started_at``（存在資料庫裡，不一定是這次請求帶的值）：讀到既有工作
    階段之後才能算，留給服務層（``app/services/health/step_service.py``），
    這裡只驗證這次請求帶的 ``started_at`` 本身的形狀。
    """

    model_config = ConfigDict(extra="forbid")

    steps: int = Field(ge=0)
    started_at: datetime

    @field_validator("started_at")
    @classmethod
    def _validate_started_at(cls, value: datetime) -> datetime:
        validated = _reject_more_than_5_minutes_future(value, "started_at")
        assert validated is not None  # started_at 是必填欄位，不會是 None
        return validated


class StepSession(BaseModel):
    """``step_sessions`` 的儲存形狀。內部實作，SHALL NOT 出現在任何 API
    回應（``session_id``／``started_at``／``last_synced_at`` 皆是內部欄位，
    見 ``StepCount``）——因此刻意不登記進 ``FIELD_CLASSIFICATION``，也不在
    ``CROSS_USER_MODELS`` 的守門測試範圍內。
    """

    user_id: str
    session_id: str
    date: str
    steps: int = Field(ge=0)
    started_at: datetime
    last_synced_at: datetime


class StepCount(BaseModel):
    """某一天的步數彙總——``step_sessions`` 內同一位使用者、同一天所有工作
    階段的 ``steps`` 總和（step-counter spec「同步的冪等性」：「每日步數
    SHALL 為當日所有工作階段累計值的總和」）。是計步唯一會出現在回應中的
    模型。
    """

    user_id: str
    date: str
    steps: int = Field(ge=0)


# ── 健康提醒推播的節流 claim ────────────────────────────────────────────


class HealthAlertClaim(BaseModel):
    """``health_alert_claims``：health-alerts spec「重複推播的節流」的節流
    紀錄。沒有回應模型——只在 ``HealthAlertClaimRepository.try_claim`` 內部
    使用，同 ``app/repositories/safety_alert_repository.py`` 的
    ``SafetyAlertRecord`` 模式。
    """

    user_id: str
    alert_key: str
    claimed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime
