from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models.prescription import (
    FREQUENCY_TO_SLOTS,
    PrescriptionDraft,
    RecognitionResult,
    RecognizedDrug,
)


def test_recognized_drug_requires_only_name():
    """藥袋常有欄位缺漏，除藥名外都必須允許為空。"""
    drug = RecognizedDrug(name="脈優錠5毫克")

    assert drug.name == "脈優錠5毫克"
    assert drug.generic_name is None
    assert drug.unit_content is None
    assert drug.total_quantity is None
    assert drug.usage_raw is None
    assert drug.dose_per_time is None
    assert drug.timing is None
    assert drug.duration_days is None
    assert drug.indication is None
    assert drug.license_number is None


def test_recognized_drug_defaults_to_unclassified_frequency():
    """頻次無法歸類時落到 OTHER，不臆測。"""
    drug = RecognizedDrug(name="某藥")

    assert drug.frequency_code == "OTHER"


def test_recognized_drug_name_confidence_defaults_to_low():
    """未經藥證庫校驗的藥名一律是低信心，不得因模型自述而預設為高。"""
    drug = RecognizedDrug(name="某藥")

    assert drug.name_confidence == "low"


def test_recognized_drug_rejects_unknown_frequency_code():
    with pytest.raises(ValidationError):
        RecognizedDrug(name="某藥", frequency_code="Q8H")


def test_recognized_drug_preserves_usage_raw_verbatim():
    """核對畫面要對照藥袋原文，正規化結果不得覆寫它。"""
    drug = RecognizedDrug(name="某藥", usage_raw="TID PC", frequency_code="TID")

    assert drug.usage_raw == "TID PC"


def test_recognition_result_defaults():
    result = RecognitionResult()

    assert result.institution is None
    assert result.patient_name is None
    assert result.dispensed_date is None
    assert result.drugs == []
    assert result.multiple_bags_suspected is False


def test_frequency_to_slots_mapping():
    assert FREQUENCY_TO_SLOTS["QD"] == ("morning",)
    assert FREQUENCY_TO_SLOTS["BID"] == ("morning", "evening")
    assert FREQUENCY_TO_SLOTS["TID"] == ("morning", "noon", "evening")
    assert FREQUENCY_TO_SLOTS["QID"] == ("morning", "noon", "evening", "bedtime")
    assert FREQUENCY_TO_SLOTS["HS"] == ("bedtime",)


def test_prn_and_other_have_no_automatic_slots():
    """PRN 不得自動建立定時提醒；OTHER 需使用者指定時段。"""
    assert FREQUENCY_TO_SLOTS["PRN"] == ()
    assert FREQUENCY_TO_SLOTS["OTHER"] == ()


def test_frequency_to_slots_values_are_immutable():
    """映射是共用的；值必須不可變，呼叫端才無法就地修改而污染其他查詢。"""
    for slots in FREQUENCY_TO_SLOTS.values():
        assert isinstance(slots, tuple)


def test_prescription_draft_starts_uncommitted():
    draft = PrescriptionDraft(
        draft_id="D1",
        creator_user_id="U_FAMILY",
        recognition=RecognitionResult(drugs=[RecognizedDrug(name="某藥")]),
        confidence_level="medium",
        expires_at=datetime.now(timezone.utc),
    )

    assert draft.committed_at is None
    assert draft.committed_medication_ids == []


def test_prescription_draft_rejects_unknown_confidence_level():
    with pytest.raises(ValidationError):
        PrescriptionDraft(
            draft_id="D1",
            creator_user_id="U_FAMILY",
            recognition=RecognitionResult(),
            confidence_level="pretty_sure",
            expires_at=datetime.now(timezone.utc),
        )
