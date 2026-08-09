"""藥袋影像的結構化辨識。

不接傳統 OCR：台灣的藥袋沒有統一版型，每家醫院、診所、社區藥局的排版、字體、
欄位順序都不同，「OCR 取字 + 規則解析欄位」在跨機構時會大量失效。改以視覺模型
在 schema 約束下直接輸出結構。

失敗一律分成三種可區分的原因。三者對使用者的下一步指示完全不同——重拍、
換一張、稍後再試——合併成同一則訊息只會讓使用者重複做無效的重拍。
"""

import asyncio
import logging
from typing import Any, Optional, Protocol

from app.models.medication import MedicationFrequencyCode
from app.models.prescription import (
    FREQUENCY_TO_SLOTS,
    RecognitionResult,
    RecognizedDrug,
    ScanFailureReason,
)
from app.services.gemini.shared.errors import (
    GeminiError,
    GeminiParseError,
    GeminiSchemaError,
)

logger = logging.getLogger(__name__)


class PrescriptionScanError(Exception):
    """藥袋辨識失敗。`reason` 決定要給使用者哪一種指示。"""

    reason: ScanFailureReason = "unreadable"


class PrescriptionUnreadableError(PrescriptionScanError):
    """影像判讀失敗——建議重拍。"""

    reason: ScanFailureReason = "unreadable"


class PrescriptionNotRecognizedError(PrescriptionScanError):
    """判定不是藥袋——重拍同一張沒有幫助，要換一張。"""

    reason: ScanFailureReason = "not_prescription"


class PrescriptionServiceUnavailableError(PrescriptionScanError):
    """外部服務失敗或逾時——建議稍後再試，重拍不會有幫助。"""

    reason: ScanFailureReason = "service_unavailable"


class StructuredVisionInvoker(Protocol):
    async def invoke_structured_output_with_image(
        self,
        *,
        prompt: str,
        image_bytes: bytes,
        mime_type: str,
        json_schema: dict[str, Any],
    ) -> Any:
        ...


_DRUG_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "generic_name": {"type": "string"},
        "unit_content": {"type": "string"},
        "total_quantity": {"type": "integer"},
        "usage_raw": {"type": "string"},
        "frequency_code": {
            "type": "string",
            "enum": list(FREQUENCY_TO_SLOTS.keys()),
        },
        "dose_per_time": {"type": "string"},
        "timing": {
            "type": "string",
            "enum": ["before_meal", "after_meal", "bedtime", "empty_stomach"],
        },
        "duration_days": {"type": "integer"},
        "indication": {"type": "string"},
    },
    "required": ["name"],
}

RECOGNITION_SCHEMA = {
    "type": "object",
    "properties": {
        "institution": {"type": "string"},
        # 收成陣列而非單值：單張影像若鋪了好幾個藥袋，多出來的姓名與日期
        # 就是唯一能察覺的訊號。收成單值會讓這個訊號在模型端就被丟掉。
        "patient_names": {"type": "array", "items": {"type": "string"}},
        "dispensed_dates": {"type": "array", "items": {"type": "string"}},
        "drugs": {"type": "array", "items": _DRUG_SCHEMA},
    },
    "required": ["drugs"],
}

RECOGNITION_PROMPT = """你正在讀一張台灣的藥袋（醫療機構或藥局調劑後交付給病人的藥品包裝）。

請只抽取藥袋上實際印出的資訊，並遵守：

1. 任何欄位若藥袋上沒有印，就留空，不要推測、不要計算、不要從常識補齊。
2. usage_raw 必須原樣保留藥袋上印的用法字串（例如「TID PC」「早晚飯後各一顆」），
   不要改寫成別的說法。
3. frequency_code 只能從下列選一個：
   QD（一日一次）、BID（一日二次）、TID（一日三次）、QID（一日四次）、
   HS（睡前）、PRN（需要時服用）、OTHER。
   只要無法明確歸類到前六種，一律填 OTHER，不要勉強歸類。
4. patient_names 與 dispensed_dates 請列出影像中看到的所有值。
   如果畫面裡不只一個藥袋，就會有多個值——請如實列出，不要只留一個。
5. 一袋一藥是常態，但同一張影像可能包含多個藥袋，請把看到的藥品全部列出。

只輸出符合 schema 的結果。"""


class PrescriptionOcrService:
    def __init__(
        self,
        gemini_service: StructuredVisionInvoker,
        timeout_seconds: int = 60,
    ) -> None:
        self._gemini_service = gemini_service
        self._timeout_seconds = timeout_seconds

    async def recognize(self, image_bytes: bytes, mime_type: str) -> RecognitionResult:
        payload = await self._invoke(image_bytes, mime_type)
        result = self._parse(payload)

        if not result.drugs:
            raise PrescriptionNotRecognizedError("影像中未辨識出任何藥品")
        return result

    async def _invoke(self, image_bytes: bytes, mime_type: str) -> Any:
        try:
            return await asyncio.wait_for(
                self._gemini_service.invoke_structured_output_with_image(
                    prompt=RECOGNITION_PROMPT,
                    image_bytes=image_bytes,
                    mime_type=mime_type,
                    json_schema=RECOGNITION_SCHEMA,
                ),
                timeout=self._timeout_seconds,
            )
        except (GeminiSchemaError, GeminiParseError) as exc:
            # 模型有回應但格式不對：這是「這張圖讀不出東西」，不是服務故障。
            logger.warning("藥袋辨識回應格式異常：%s", exc)
            raise PrescriptionUnreadableError("辨識結果格式異常") from exc
        except (asyncio.TimeoutError, GeminiError) as exc:
            logger.warning("藥袋辨識服務無法完成：%s", exc)
            raise PrescriptionServiceUnavailableError("辨識服務暫時無法使用") from exc

    def _parse(self, payload: Any) -> RecognitionResult:
        if not isinstance(payload, dict):
            raise PrescriptionUnreadableError("辨識結果不是物件")

        raw_drugs = payload.get("drugs")
        if not isinstance(raw_drugs, list):
            raise PrescriptionUnreadableError("辨識結果的藥品欄位不是陣列")

        patient_names = _string_list(payload.get("patient_names"))
        dispensed_dates = _string_list(payload.get("dispensed_dates"))

        drugs = [
            drug for drug in (self._parse_drug(item) for item in raw_drugs) if drug
        ]

        return RecognitionResult(
            institution=_optional_string(payload.get("institution")),
            patient_name=patient_names[0] if patient_names else None,
            dispensed_date=dispensed_dates[0] if dispensed_dates else None,
            drugs=drugs,
            multiple_bags_suspected=len(patient_names) > 1 or len(dispensed_dates) > 1,
        )

    def _parse_drug(self, item: Any) -> Optional[RecognizedDrug]:
        if not isinstance(item, dict):
            return None
        name = _optional_string(item.get("name"))
        if not name:
            # 沒有藥名的項目對後續一點用都沒有：既不能比對藥證庫，
            # 也不能讓使用者核對。丟掉比留一個空殼好。
            return None

        return RecognizedDrug(
            name=name,
            generic_name=_optional_string(item.get("generic_name")),
            unit_content=_optional_string(item.get("unit_content")),
            total_quantity=_optional_int(item.get("total_quantity")),
            usage_raw=_optional_string(item.get("usage_raw")),
            frequency_code=_coerce_frequency(item.get("frequency_code")),
            dose_per_time=_optional_string(item.get("dose_per_time")),
            timing=_coerce_timing(item.get("timing")),
            duration_days=_optional_int(item.get("duration_days")),
            indication=_optional_string(item.get("indication")),
            # 名稱可信度由藥證庫決定，辨識階段一律 low。
            name_confidence="low",
        )


def _optional_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    seen: list[str] = []
    for item in value:
        text = _optional_string(item)
        if text and text not in seen:
            seen.append(text)
    return seen


def _coerce_frequency(value: Any) -> MedicationFrequencyCode:
    """列舉外的值落到 OTHER，而不是讓整份辨識失敗。

    模型偶爾會吐出 schema 沒列的頻次（例如 Q8H）。那一筆的頻次不可用，
    但同一張藥袋的其他資訊仍然有價值，且 OTHER 本來就會要求使用者指定時段。
    """
    text = _optional_string(value)
    if text and text.upper() in FREQUENCY_TO_SLOTS:
        return text.upper()  # type: ignore[return-value]
    return "OTHER"


def _coerce_timing(value: Any) -> Optional[str]:
    text = _optional_string(value)
    if text in {"before_meal", "after_meal", "bedtime", "empty_stomach"}:
        return text
    return None
