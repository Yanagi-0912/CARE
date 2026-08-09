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
                "frequency_code": "TID",
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
