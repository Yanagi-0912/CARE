import asyncio

import pytest

from app.services.gemini.shared.errors import GeminiNetworkError, GeminiSchemaError
from app.services.medication.prescription_ocr_service import (
    PrescriptionNotRecognizedError,
    PrescriptionOcrService,
    PrescriptionServiceUnavailableError,
    PrescriptionUnreadableError,
)


class FakeGemini:
    """只實作 PrescriptionOcrService 用到的那一個方法。"""

    def __init__(self, result=None, error: Exception | None = None):
        self._result = result
        self._error = error
        self.calls: list[dict] = []

    async def invoke_structured_output_with_image(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._result


def _payload(**overrides):
    payload = {
        "institution": "臺大醫院",
        "patient_names": ["王大明"],
        "dispensed_dates": ["2026-08-09"],
        "drugs": [
            {
                "name": "脈優錠5毫克",
                "usage_raw": "TID PC",
                "frequency_raw": "TID",
                "frequency_code": "TID",
                "total_quantity_raw": "共21錠",
                "total_quantity": 21,
                "indication": "高血壓",
            }
        ],
    }
    payload.update(overrides)
    return payload


def _service(gemini) -> PrescriptionOcrService:
    return PrescriptionOcrService(gemini_service=gemini, timeout_seconds=5)


@pytest.mark.asyncio
async def test_maps_model_output_to_recognition_result():
    result = await _service(FakeGemini(_payload())).recognize(b"image", "image/jpeg")

    assert result.institution == "臺大醫院"
    assert result.patient_name == "王大明"
    assert result.dispensed_date == "2026-08-09"
    assert len(result.drugs) == 1
    assert result.drugs[0].name == "脈優錠5毫克"
    assert result.drugs[0].frequency_code == "TID"
    assert result.drugs[0].total_quantity == 21


@pytest.mark.asyncio
async def test_usage_raw_is_preserved_verbatim():
    """核對畫面要對照藥袋原文，不得被正規化後的頻次代碼取代。"""
    result = await _service(FakeGemini(_payload())).recognize(b"image", "image/jpeg")

    assert result.drugs[0].usage_raw == "TID PC"


@pytest.mark.asyncio
async def test_name_confidence_starts_low_before_catalog_check():
    """辨識階段不判定藥名可信；那是藥證庫的工作。"""
    result = await _service(FakeGemini(_payload())).recognize(b"image", "image/jpeg")

    assert result.drugs[0].name_confidence == "low"


@pytest.mark.asyncio
async def test_unknown_frequency_falls_back_to_other():
    """模型吐出不在列舉內的頻次時落到 OTHER，而不是讓整份辨識失敗。"""
    payload = _payload(
        drugs=[{"name": "某藥", "usage_raw": "每8小時", "frequency_code": "Q8H"}]
    )

    result = await _service(FakeGemini(payload)).recognize(b"image", "image/jpeg")

    assert result.drugs[0].frequency_code == "OTHER"
    assert result.drugs[0].usage_raw == "每8小時"


@pytest.mark.asyncio
async def test_drug_without_name_is_dropped():
    payload = _payload(drugs=[{"name": "", "usage_raw": "TID"}, {"name": "有名字"}])

    result = await _service(FakeGemini(payload)).recognize(b"image", "image/jpeg")

    assert [drug.name for drug in result.drugs] == ["有名字"]


@pytest.mark.asyncio
async def test_no_drugs_is_not_a_prescription():
    with pytest.raises(PrescriptionNotRecognizedError) as excinfo:
        await _service(FakeGemini(_payload(drugs=[]))).recognize(b"image", "image/jpeg")

    assert excinfo.value.reason == "not_prescription"


@pytest.mark.asyncio
async def test_every_drug_missing_a_name_is_not_a_prescription():
    payload = _payload(drugs=[{"name": ""}, {"usage_raw": "TID"}])

    with pytest.raises(PrescriptionNotRecognizedError):
        await _service(FakeGemini(payload)).recognize(b"image", "image/jpeg")


@pytest.mark.asyncio
async def test_multiple_patient_names_flags_multiple_bags():
    payload = _payload(patient_names=["王大明", "王小美"])

    result = await _service(FakeGemini(payload)).recognize(b"image", "image/jpeg")

    assert result.multiple_bags_suspected is True
    assert result.patient_name == "王大明"
    assert len(result.drugs) == 1


@pytest.mark.asyncio
async def test_multiple_dispensed_dates_flags_multiple_bags():
    payload = _payload(dispensed_dates=["2026-08-09", "2026-07-01"])

    result = await _service(FakeGemini(payload)).recognize(b"image", "image/jpeg")

    assert result.multiple_bags_suspected is True


@pytest.mark.asyncio
async def test_single_bag_is_not_flagged():
    result = await _service(FakeGemini(_payload())).recognize(b"image", "image/jpeg")

    assert result.multiple_bags_suspected is False


@pytest.mark.asyncio
async def test_malformed_model_output_is_unreadable():
    with pytest.raises(PrescriptionUnreadableError) as excinfo:
        await _service(FakeGemini({"drugs": "not a list"})).recognize(
            b"image", "image/jpeg"
        )

    assert excinfo.value.reason == "unreadable"


@pytest.mark.asyncio
async def test_schema_error_is_unreadable():
    gemini = FakeGemini(error=GeminiSchemaError("bad schema"))

    with pytest.raises(PrescriptionUnreadableError):
        await _service(gemini).recognize(b"image", "image/jpeg")


@pytest.mark.asyncio
async def test_network_error_is_service_unavailable():
    """外部服務掛掉時要說「稍後再試」，不能叫使用者重拍——重拍不會有幫助。"""
    gemini = FakeGemini(error=GeminiNetworkError("boom"))

    with pytest.raises(PrescriptionServiceUnavailableError) as excinfo:
        await _service(gemini).recognize(b"image", "image/jpeg")

    assert excinfo.value.reason == "service_unavailable"


@pytest.mark.asyncio
async def test_timeout_is_service_unavailable():
    gemini = FakeGemini(error=asyncio.TimeoutError())

    with pytest.raises(PrescriptionServiceUnavailableError):
        await _service(gemini).recognize(b"image", "image/jpeg")


@pytest.mark.asyncio
async def test_image_is_passed_through_to_the_model():
    gemini = FakeGemini(_payload())

    await _service(gemini).recognize(b"raw-bytes", "image/png")

    assert gemini.calls[0]["image_bytes"] == b"raw-bytes"
    assert gemini.calls[0]["mime_type"] == "image/png"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw", "code", "expected"),
    [
        ("QD 每日一次", "OTHER", "QD"),
        ("BID（每日二次）", None, "BID"),
        ("TID PC", "OTHER", "TID"),
        ("每日服用四次", "QID", "QID"),
        ("每天兩次", "OTHER", "BID"),
        ("需要時服用", "OTHER", "PRN"),
        ("HS 睡前", "HS", "HS"),
        ("ＱＤ　每日１次", "QD", "QD"),
        (None, "QD", "OTHER"),
        ("", "QD", "OTHER"),
        ("每次1錠", "QD", "OTHER"),
        ("每日一次", "BID", "OTHER"),
        ("QD 每日二次", "QD", "OTHER"),
        ("QD / BID", "OTHER", "OTHER"),
        ("每8小時", "Q8H", "OTHER"),
        ("每週一三五各一次", "QD", "OTHER"),
        ("以前QD，改BID", "BID", "OTHER"),
        ("非每日一次", "QD", "OTHER"),
        ("QD?", "QD", "OTHER"),
        ("每日三次", "Q8H", "OTHER"),
    ],
)
async def test_frequency_requires_unambiguous_consistent_printed_evidence(raw, code, expected):
    payload = _payload(drugs=[{
        "name": "某藥", "frequency_raw": raw, "frequency_code": code,
        "usage_raw": "每次1錠", "indication": "每日一次 早餐後",
    }])

    drug = (await _service(FakeGemini(payload)).recognize(b"image", "image/jpeg")).drugs[0]

    assert drug.frequency_code == expected
    assert drug.usage_raw == "每次1錠"


@pytest.mark.asyncio
async def test_no_evidence_does_not_invent_a_quantity_or_one_day_course():
    payload = _payload(drugs=[{
        "name": "某藥", "dose_per_time": "1錠", "frequency_code": "QD",
        "frequency_raw": "QD 每日一次", "usage_raw": "每日一次，每次1錠",
        "total_quantity": 1, "duration_days": 1,
        "total_quantity_raw": None, "duration_days_raw": None,
    }])

    drug = (await _service(FakeGemini(payload)).recognize(b"image", "image/jpeg")).drugs[0]

    assert drug.frequency_code == "QD"
    assert drug.dose_per_time == "1錠"
    assert drug.total_quantity is None
    assert drug.duration_days is None


@pytest.mark.asyncio
async def test_frequency_transcription_remains_available_for_review_when_usage_is_missing():
    payload = _payload(drugs=[{
        "name": "某藥", "usage_raw": None, "frequency_raw": "BID 每日二次",
        "frequency_code": "OTHER",
    }])

    drug = (await _service(FakeGemini(payload)).recognize(b"image", "image/jpeg")).drugs[0]

    assert drug.frequency_code == "BID"
    assert drug.usage_raw == "BID 每日二次"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value", "raw", "expected"),
    [
        ("total_quantity", 21, "共21錠", 21),
        ("total_quantity", "３０", "總數量：３０ 顆", 30),
        ("total_quantity", 30, "QTY: 30 tablets", 30),
        ("total_quantity", 1, "每次1錠", None),
        ("total_quantity", 1, "1錠", None),
        ("total_quantity", 21, "共12錠", None),
        ("duration_days", 5, "療程5天", 5),
        ("duration_days", 7, "7日份", 7),
        ("duration_days", "５", "給藥日數：５日", 5),
        ("duration_days", 5.0, "5 days", 5),
        ("duration_days", 1, "每日1次", None),
        ("duration_days", 5, "療程3天", None),
        ("duration_days", 0, "療程0天", None),
        ("duration_days", -1, "療程1天", None),
        ("duration_days", 5.9, "療程5天", None),
        ("duration_days", True, "療程1天", None),
        ("duration_days", float("inf"), "療程5天", None),
        ("duration_days", "5.0", "療程5天", None),
    ],
)
async def test_quantity_and_duration_need_matching_printed_context(field, value, raw, expected):
    payload = _payload(drugs=[{
        "name": "某藥", field: value, f"{field}_raw": raw,
    }])

    drug = (await _service(FakeGemini(payload)).recognize(b"image", "image/jpeg")).drugs[0]

    assert getattr(drug, field) == expected


@pytest.mark.asyncio
async def test_does_not_calculate_course_from_quantity_and_frequency():
    payload = _payload(drugs=[{
        "name": "某藥", "dose_per_time": "1錠", "total_quantity": 21,
        "total_quantity_raw": "共21錠", "frequency_code": "TID",
        "frequency_raw": "TID", "duration_days": 7,
    }])

    drug = (await _service(FakeGemini(payload)).recognize(b"image", "image/jpeg")).drugs[0]

    assert drug.total_quantity == 21
    assert drug.frequency_code == "TID"
    assert drug.duration_days is None
