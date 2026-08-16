from datetime import datetime, timedelta, timezone
from typing import get_args

import pytest
from pydantic import ValidationError

from app.models.safety import (
    AcquisitionChannel,
    DrugMention,
    RiskLevel,
    SafetyAlertRecord,
)


def test_risk_level_has_exactly_three_grades():
    """三級各自對應一套揭露規則（不打擾／只回本人／通報家人）。

    多一級就會出現沒有人定義過收件人的等級。
    """
    assert set(get_args(RiskLevel)) == {"none", "low", "high"}


def test_acquisition_channel_covers_every_recognised_route():
    """通路是判定的一半，列舉少一項就等於把該情境靜默併入 unknown。"""
    assert set(get_args(AcquisitionChannel)) == {
        "medical_institution",
        "licensed_pharmacy",
        "overseas_personal",
        "online_marketplace",
        "acquaintance",
        "tv_shopping",
        "unknown",
    }


def test_drug_mention_requires_only_raw_name():
    """聊天室的句子與藥袋 OCR 常常只有藥名，其餘欄位缺漏是常態。"""
    mention = DrugMention(raw_name="合利他命強効錠 EX PLUS")

    assert mention.raw_name == "合利他命強効錠 EX PLUS"
    assert mention.source_text is None
    assert mention.license_number is None
    assert mention.dispensed_package_markers == []


def test_drug_mention_rejects_missing_raw_name():
    """沒有名稱的項目無從比對藥證庫，模型層就要擋掉。"""
    with pytest.raises(ValidationError):
        DrugMention()


def test_drug_mention_channel_defaults_to_unknown():
    """抽取階段不補未出現的資訊：沒提通路就是 unknown，不是預設為醫療機構。"""
    assert DrugMention(raw_name="某藥").channel == "unknown"


def test_drug_mention_catalog_hit_defaults_to_false():
    """catalog_hit 由抽取之後的藥證庫比對回填，未比對前一律視為未命中。"""
    assert DrugMention(raw_name="某藥").catalog_hit is False


def test_drug_mention_rejects_channel_outside_enum():
    """列舉外的值必須在模型層現形，才不會有無人處理的通路悄悄流進判定。"""
    with pytest.raises(ValidationError):
        DrugMention(raw_name="某藥", channel="black_market")


def test_drug_mention_carries_no_risk_conclusion():
    """抽取與判斷分離：schema 內不得有任何風險或安全性欄位。"""
    conclusion_fields = [
        name
        for name in DrugMention.model_fields
        if "risk" in name or "safety" in name or "danger" in name
    ]

    assert conclusion_fields == []


def test_drug_mention_markers_are_not_shared_between_instances():
    """預設空陣列若共用同一個 list，一次抽取的訊號會污染下一次。"""
    first = DrugMention(raw_name="A")
    second = DrugMention(raw_name="B")

    first.dispensed_package_markers.append("patient_name")

    assert second.dispensed_package_markers == []


def test_safety_alert_record_carries_dedupe_key_and_expiry():
    """節流靠 (user_id, drug_key) 唯一鍵與 expires_at 的 TTL，兩者缺一不可。"""
    notified_at = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
    record = SafetyAlertRecord(
        user_id="U1234",
        drug_key="合利他命強効錠explus",
        risk_level="high",
        notified_at=notified_at,
        expires_at=notified_at + timedelta(hours=24),
    )

    assert record.user_id == "U1234"
    assert record.drug_key == "合利他命強効錠explus"
    assert record.risk_level == "high"
    assert record.notified_at == notified_at
    assert record.expires_at == notified_at + timedelta(hours=24)


def test_safety_alert_record_rejects_risk_level_outside_enum():
    notified_at = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)

    with pytest.raises(ValidationError):
        SafetyAlertRecord(
            user_id="U1234",
            drug_key="某藥",
            risk_level="critical",
            notified_at=notified_at,
            expires_at=notified_at + timedelta(hours=24),
        )


def test_safety_alert_record_defaults_notified_at_to_aware_now():
    """紀錄時間若落成 naive datetime，與 TTL 索引比對的時區就會漂掉。"""
    record = SafetyAlertRecord(
        user_id="U1234",
        drug_key="某藥",
        risk_level="low",
        expires_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
    )

    assert record.notified_at.tzinfo is not None
