"""app/models/health.py 的輸入驗證邊界測試（task-2-brief 2.1）。

涵蓋：每個輸入範圍的邊界、收縮壓不大於舒張壓、量測時間晚於現在 5 分鐘以上、
提醒範圍上限不大於下限、經期超過 15 天、工作階段識別碼非 UUID v4。
"""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.models.health import (
    TAIPEI_TZ,
    CreateBloodGlucoseRequest,
    CreateBloodPressureRequest,
    CreateMenstrualRecordRequest,
    HealthAlertClaim,
    HealthAlertThreshold,
    HealthMeasurement,
    MenstrualRecord,
    StepCount,
    StepSession,
    StepSessionSyncRequest,
    UpdateHealthAlertThresholdRequest,
    UpdateMenstrualRecordRequest,
)


# ── 血壓 ────────────────────────────────────────────────────────────────


def test_blood_pressure_accepts_valid_input():
    request = CreateBloodPressureRequest(systolic=128, diastolic=82, pulse=70)
    assert request.systolic == 128
    assert request.diastolic == 82


@pytest.mark.parametrize("systolic", [49, 301])
def test_blood_pressure_rejects_systolic_out_of_range(systolic):
    with pytest.raises(ValidationError):
        CreateBloodPressureRequest(systolic=systolic, diastolic=80)


@pytest.mark.parametrize("systolic", [50, 300])
def test_blood_pressure_accepts_systolic_boundary(systolic):
    # 邊界值本身合法（50–300），但仍要滿足收縮壓 > 舒張壓。
    request = CreateBloodPressureRequest(systolic=systolic, diastolic=30)
    assert request.systolic == systolic


@pytest.mark.parametrize("diastolic", [29, 201])
def test_blood_pressure_rejects_diastolic_out_of_range(diastolic):
    with pytest.raises(ValidationError):
        CreateBloodPressureRequest(systolic=250, diastolic=diastolic)


@pytest.mark.parametrize("diastolic", [30, 200])
def test_blood_pressure_accepts_diastolic_boundary(diastolic):
    request = CreateBloodPressureRequest(systolic=250, diastolic=diastolic)
    assert request.diastolic == diastolic


@pytest.mark.parametrize("pulse", [29, 251])
def test_blood_pressure_rejects_pulse_out_of_range(pulse):
    with pytest.raises(ValidationError):
        CreateBloodPressureRequest(systolic=120, diastolic=80, pulse=pulse)


@pytest.mark.parametrize("pulse", [30, 250])
def test_blood_pressure_accepts_pulse_boundary(pulse):
    request = CreateBloodPressureRequest(systolic=120, diastolic=80, pulse=pulse)
    assert request.pulse == pulse


def test_blood_pressure_pulse_is_optional():
    request = CreateBloodPressureRequest(systolic=120, diastolic=80)
    assert request.pulse is None


def test_blood_pressure_rejects_systolic_not_greater_than_diastolic():
    with pytest.raises(ValidationError):
        CreateBloodPressureRequest(systolic=80, diastolic=120)


def test_blood_pressure_rejects_systolic_equal_to_diastolic():
    with pytest.raises(ValidationError):
        CreateBloodPressureRequest(systolic=90, diastolic=90)


def test_blood_pressure_rejects_measured_at_more_than_five_minutes_future():
    future = datetime.now(timezone.utc) + timedelta(minutes=10)
    with pytest.raises(ValidationError):
        CreateBloodPressureRequest(systolic=120, diastolic=80, measured_at=future)


def test_blood_pressure_accepts_measured_at_within_five_minutes_future():
    # 容許手機時鐘誤差：5 分鐘以內的未來時間 SHALL 被接受。
    near_future = datetime.now(timezone.utc) + timedelta(minutes=4)
    request = CreateBloodPressureRequest(
        systolic=120, diastolic=80, measured_at=near_future
    )
    assert request.measured_at == near_future


def test_blood_pressure_accepts_past_measured_at_as_backfill():
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    request = CreateBloodPressureRequest(
        systolic=120, diastolic=80, measured_at=yesterday
    )
    assert request.measured_at == yesterday


def test_blood_pressure_treats_naive_measured_at_as_utc():
    near_future_naive = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
        minutes=4
    )
    request = CreateBloodPressureRequest(
        systolic=120, diastolic=80, measured_at=near_future_naive
    )
    assert request.measured_at is not None


def test_blood_pressure_rejects_unknown_field():
    """POST /api/health/measurements 的 body 是
    ``Union[CreateBloodPressureRequest, CreateBloodGlucoseRequest]``——兩者
    沒有共同的 ``kind`` 欄位，靠 Pydantic smart union 依「哪個模型驗證得過」
    來選。若允許多餘欄位，一份打錯字或混進血糖欄位的請求會被這個模型悄悄
    吃下、丟棄看不懂的欄位並驗證成功，得到一筆種類錯誤的紀錄。"""
    with pytest.raises(ValidationError):
        CreateBloodPressureRequest(systolic=120, diastolic=80, glucose_mg_dl=100)


# ── 血糖 ────────────────────────────────────────────────────────────────


def test_blood_glucose_accepts_valid_input():
    request = CreateBloodGlucoseRequest(glucose_mg_dl=112, meal_context="fasting")
    assert request.glucose_mg_dl == 112
    assert request.meal_context == "fasting"


@pytest.mark.parametrize("glucose", [19, 801])
def test_blood_glucose_rejects_out_of_range(glucose):
    with pytest.raises(ValidationError):
        CreateBloodGlucoseRequest(glucose_mg_dl=glucose, meal_context="fasting")


@pytest.mark.parametrize("glucose", [20, 800])
def test_blood_glucose_accepts_boundary(glucose):
    request = CreateBloodGlucoseRequest(glucose_mg_dl=glucose, meal_context="fasting")
    assert request.glucose_mg_dl == glucose


def test_blood_glucose_requires_meal_context():
    with pytest.raises(ValidationError):
        CreateBloodGlucoseRequest(glucose_mg_dl=112)


def test_blood_glucose_rejects_invalid_meal_context():
    with pytest.raises(ValidationError):
        CreateBloodGlucoseRequest(glucose_mg_dl=112, meal_context="dinner")


def test_blood_glucose_rejects_measured_at_more_than_five_minutes_future():
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    with pytest.raises(ValidationError):
        CreateBloodGlucoseRequest(
            glucose_mg_dl=112, meal_context="fasting", measured_at=future
        )


def test_blood_glucose_rejects_unknown_field():
    """見 ``test_blood_pressure_rejects_unknown_field`` 的同一段理由。"""
    with pytest.raises(ValidationError):
        CreateBloodGlucoseRequest(glucose_mg_dl=112, meal_context="fasting", systolic=120)


# ── 提醒範圍 ────────────────────────────────────────────────────────────


def test_threshold_accepts_upper_greater_than_lower():
    request = UpdateHealthAlertThresholdRequest(systolic_high=140, systolic_low=100)
    assert request.systolic_high == 140


def test_threshold_rejects_upper_not_greater_than_lower():
    with pytest.raises(ValidationError):
        UpdateHealthAlertThresholdRequest(systolic_high=100, systolic_low=120)


def test_threshold_rejects_upper_equal_to_lower():
    with pytest.raises(ValidationError):
        UpdateHealthAlertThresholdRequest(diastolic_high=80, diastolic_low=80)


def test_threshold_glucose_low_is_checked_against_both_uppers():
    with pytest.raises(ValidationError):
        UpdateHealthAlertThresholdRequest(glucose_fasting_high=100, glucose_low=100)
    with pytest.raises(ValidationError):
        UpdateHealthAlertThresholdRequest(glucose_nonfasting_high=100, glucose_low=100)


def test_threshold_allows_clearing_one_side_of_a_pair():
    # 只設定一邊（另一邊留空）SHALL 通過——清除一項不該連帶被上下限規則擋下。
    request = UpdateHealthAlertThresholdRequest(diastolic_high=90)
    assert request.diastolic_low is None


@pytest.mark.parametrize("value", [19, 801])
def test_threshold_rejects_glucose_out_of_range(value):
    with pytest.raises(ValidationError):
        UpdateHealthAlertThresholdRequest(glucose_low=value)


def test_threshold_response_model_does_not_revalidate_ranges_or_pairs():
    """儲存／回應形狀刻意不重跑範圍與上下限驗證（同 HealthMeasurement 的
    設計）：日後範圍常數或上下限規則調整，不該讓舊文件連讀都讀不回來。
    這裡用「上限不大於下限」與「超出目前合理範圍」兩種本來會被請求模型
    擋下的形狀，證明儲存模型仍能建構成功。
    """
    threshold = HealthAlertThreshold(
        user_id="U1",
        updated_by="U1",
        systolic_high=100,
        systolic_low=120,
        glucose_low=9000,
    )
    assert threshold.systolic_high == 100
    assert threshold.glucose_low == 9000


# ── 經期 ────────────────────────────────────────────────────────────────


def test_menstrual_record_accepts_start_date_only():
    request = CreateMenstrualRecordRequest(start_date="2026-09-01")
    assert request.end_date is None


def test_menstrual_record_rejects_start_date_in_the_future():
    # 用遠遠超過台北時區誤差的天數，避免測試在時區邊界附近偶發失敗。
    far_future = (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y-%m-%d")
    with pytest.raises(ValidationError):
        CreateMenstrualRecordRequest(start_date=far_future)


def test_menstrual_record_accepts_start_date_of_today_in_taipei():
    today_taipei = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
    request = CreateMenstrualRecordRequest(start_date=today_taipei)
    assert request.start_date == today_taipei


def test_menstrual_record_rejects_start_date_of_tomorrow_in_taipei():
    tomorrow_taipei = (datetime.now(TAIPEI_TZ) + timedelta(days=1)).strftime(
        "%Y-%m-%d"
    )
    with pytest.raises(ValidationError):
        CreateMenstrualRecordRequest(start_date=tomorrow_taipei)


def test_menstrual_record_rejects_end_date_before_start_date():
    with pytest.raises(ValidationError):
        CreateMenstrualRecordRequest(start_date="2026-09-05", end_date="2026-09-01")


def test_menstrual_record_accepts_span_of_exactly_fifteen_days():
    request = CreateMenstrualRecordRequest(start_date="2026-09-01", end_date="2026-09-16")
    assert request.end_date == "2026-09-16"


def test_menstrual_record_rejects_span_over_fifteen_days():
    with pytest.raises(ValidationError):
        CreateMenstrualRecordRequest(start_date="2026-09-01", end_date="2026-09-17")


def test_menstrual_record_rejects_note_over_200_chars():
    with pytest.raises(ValidationError):
        CreateMenstrualRecordRequest(start_date="2026-09-01", note="x" * 201)


def test_menstrual_record_accepts_note_at_200_chars():
    request = CreateMenstrualRecordRequest(start_date="2026-09-01", note="x" * 200)
    assert len(request.note) == 200


def test_menstrual_record_rejects_invalid_flow():
    with pytest.raises(ValidationError):
        CreateMenstrualRecordRequest(start_date="2026-09-01", flow="super_heavy")


# ── 經期：部分更新（PATCH） ───────────────────────────────────────────────


def test_menstrual_update_accepts_an_empty_body():
    """PATCH 全部欄位皆選填：一個都不帶也 SHALL 通過（exclude_unset 由服務
    層決定要不要真的送出更新）。"""
    request = UpdateMenstrualRecordRequest()
    assert request.model_dump(exclude_unset=True) == {}


def test_menstrual_update_distinguishes_omitted_from_explicit_null_end_date():
    """明確帶 end_date: null 代表重新打開這筆紀錄——與「完全沒帶這個欄位」
    在 exclude_unset 下必須是兩種不同的結果。"""
    omitted = UpdateMenstrualRecordRequest(note="x")
    assert "end_date" not in omitted.model_dump(exclude_unset=True)

    reopened = UpdateMenstrualRecordRequest(end_date=None)
    dumped = reopened.model_dump(exclude_unset=True)
    assert "end_date" in dumped
    assert dumped["end_date"] is None


def test_menstrual_update_rejects_malformed_start_date_format():
    with pytest.raises(ValidationError):
        UpdateMenstrualRecordRequest(start_date="2026/09/01")


def test_menstrual_update_rejects_malformed_end_date_format():
    with pytest.raises(ValidationError):
        UpdateMenstrualRecordRequest(end_date="09-01-2026")


def test_menstrual_update_rejects_note_over_200_chars():
    with pytest.raises(ValidationError):
        UpdateMenstrualRecordRequest(note="x" * 201)


def test_menstrual_update_rejects_invalid_flow():
    with pytest.raises(ValidationError):
        UpdateMenstrualRecordRequest(flow="super_heavy")


def test_menstrual_update_rejects_unknown_field():
    with pytest.raises(ValidationError):
        UpdateMenstrualRecordRequest(unknown_field="x")


def test_menstrual_update_does_not_check_cross_field_coherence():
    """PATCH 可能只帶其中一個欄位，此時另一個欄位的值來自既有紀錄，這個
    模型看不到——因此不在這裡驗證「結束不早於開始」「間隔不超過 15 天」
    「開始不晚於今天」，那些留給服務層在合併既有紀錄之後整筆重驗
    （dispatch notes「PATCH 語意」）。"""
    request = UpdateMenstrualRecordRequest(start_date="2026-09-05", end_date="2026-09-01")
    assert request.start_date == "2026-09-05"
    assert request.end_date == "2026-09-01"


def test_menstrual_record_response_model_defaults_computed_fields_to_none():
    record = MenstrualRecord(user_id="U1", start_date="2026-09-01")
    assert record.cycle_length_days is None
    assert record.period_length_days is None


def test_menstrual_record_response_model_does_not_revalidate_note_length():
    """儲存／回應形狀刻意不重跑 note 的 max_length（同 HealthAlertThreshold
    不重跑範圍驗證的理由）：長度上限是攔截輸入的門檻，寫入當下已經在
    CreateMenstrualRecordRequest 檢查過；這裡若也套用，日後上限調整會讓
    超過新上限的舊紀錄讀不回來。"""
    record = MenstrualRecord(user_id="U1", start_date="2026-09-01", note="x" * 500)
    assert len(record.note) == 500


# ── 計步 ────────────────────────────────────────────────────────────────


def test_step_session_sync_accepts_uuid_v4():
    request = StepSessionSyncRequest(
        session_id="8f14e45f-ceea-4c9c-8f77-4f3c3e2b2b1a", steps=300
    )
    assert request.steps == 300


def test_step_session_sync_rejects_non_uuid_session_id():
    with pytest.raises(ValidationError):
        StepSessionSyncRequest(session_id="not-a-uuid", steps=100)


def test_step_session_sync_rejects_uuid_v1():
    # UUID v1（時間戳版本），版本欄位不是 4，SHALL 被拒絕。
    with pytest.raises(ValidationError):
        StepSessionSyncRequest(
            session_id="2ed6657d-e927-11e6-94ba-8b2ba76b2efe", steps=100
        )


def test_step_session_sync_rejects_negative_steps():
    with pytest.raises(ValidationError):
        StepSessionSyncRequest(
            session_id="8f14e45f-ceea-4c9c-8f77-4f3c3e2b2b1a", steps=-1
        )


def test_step_session_sync_accepts_zero_steps():
    request = StepSessionSyncRequest(
        session_id="8f14e45f-ceea-4c9c-8f77-4f3c3e2b2b1a", steps=0
    )
    assert request.steps == 0


# ── 儲存與節流模型的基本建構 ────────────────────────────────────────────


def test_health_measurement_round_trips_mongo_id_alias():
    doc = {
        "_id": "M1",
        "user_id": "U1",
        "kind": "blood_pressure",
        "measured_at": datetime.now(timezone.utc),
        "recorded_by": "U1",
        "systolic": 120,
        "diastolic": 80,
        "level": "within_range",
    }
    measurement = HealthMeasurement(**doc)
    assert measurement.id == "M1"


def test_step_session_requires_started_at_and_last_synced_at():
    session = StepSession(
        user_id="U1",
        session_id="8f14e45f-ceea-4c9c-8f77-4f3c3e2b2b1a",
        date="2026-09-01",
        steps=300,
        started_at=datetime.now(timezone.utc),
        last_synced_at=datetime.now(timezone.utc),
    )
    assert session.steps == 300


def test_step_count_is_the_only_response_shaped_model_for_steps():
    count = StepCount(user_id="U1", date="2026-09-01", steps=800)
    assert set(type(count).model_fields) == {"user_id", "date", "steps"}


def test_health_alert_claim_has_no_response_model_but_is_constructible():
    claim = HealthAlertClaim(
        user_id="U1",
        alert_key="bp_high",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    assert claim.alert_key == "bp_high"
