"""藥袋影像的結構化辨識。

不接傳統 OCR：台灣的藥袋沒有統一版型，每家醫院、診所、社區藥局的排版、字體、
欄位順序都不同，「OCR 取字 + 規則解析欄位」在跨機構時會大量失效。改以視覺模型
在 schema 約束下直接輸出結構。

失敗一律分成三種可區分的原因。三者對使用者的下一步指示完全不同——重拍、
換一張、稍後再試——合併成同一則訊息只會讓使用者重複做無效的重拍。
"""

import asyncio
import logging
import math
import re
import unicodedata
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
        "name": {
            "type": "string",
            "description": "原樣抄錄同一列的藥品名稱，含印出的中文及英文商品名，不翻譯、不猜藥名。",
        },
        "generic_name": {
            "type": ["string", "null"],
            "description": "只填實際印出的學名／成分名；英文商品名保留在 name，未印學名填 null。",
        },
        "unit_content": {
            "type": ["string", "null"],
            "description": "每錠／每顆的含量，例如 5 mg；不是每次用量，未印或看不清填 null。",
        },
        "dose_per_time": {
            "type": ["string", "null"],
            "description": "每次服用的數量及單位，例如半錠、1 錠；未印或看不清填 null。",
        },
        "usage_raw": {
            "type": ["string", "null"],
            "description": "原樣抄錄同一藥品的用法／頻次／服用時間文字，不改寫；未印或看不清填 null。",
        },
        "frequency_raw": {
            "type": ["string", "null"],
            "description": "先原樣抄錄頻次欄位，例如 QD 每日一次、BID、需要時；不填每次幾錠或適應症。未印或看不清填 null。",
        },
        "frequency_code": {
            "type": "string",
            "enum": list(FREQUENCY_TO_SLOTS.keys()),
            "description": "依 frequency_raw 的明確文字歸類；QD 每日一次、BID 每日二次、TID 每日三次、QID 每日四次、HS 睡前、PRN 需要時，其餘 OTHER。",
        },
        "timing": {
            "type": ["string", "null"],
            "enum": ["before_meal", "after_meal", "bedtime", "empty_stomach", None],
            "description": "只依實際印出的飯前、飯後、睡前、空腹歸類，未印填 null。",
        },
        "total_quantity_raw": {
            "type": ["string", "null"],
            "description": "原樣抄錄總數量及欄位標籤，例如 共21錠；每次1錠不是總數量。未印填 null。",
        },
        "total_quantity": {
            "type": ["integer", "null"],
            "minimum": 1,
            "description": "total_quantity_raw 明確印出的總數量，不能用頻次、每次用量或療程計算；沒有依據填 null。",
        },
        "duration_days_raw": {
            "type": ["string", "null"],
            "description": "原樣抄錄療程標籤與天數，例如 療程5天、7日份；每日一次不是1天。未印填 null。",
        },
        "duration_days": {
            "type": ["integer", "null"],
            "minimum": 1,
            "description": "duration_days_raw 明確印出的療程天數，不預設1天，不從數量、頻次、日期推算；沒有依據填 null。",
        },
        "indication": {
            "type": ["string", "null"],
            "description": "只抄錄實際印出的適應症／用途；頻次、用量與飯前飯後不可放在這裡，未印填 null。",
        },
    },
}
# 每個欄位明確回傳值或 null，讓「沒有印」不必用虛構數值填補型別。
_DRUG_SCHEMA["required"] = list(_DRUG_SCHEMA["properties"])

RECOGNITION_SCHEMA = {
    "type": "object",
    "properties": {
        "institution": {"type": ["string", "null"]},
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

1. 任何欄位若藥袋上沒有印或看不清楚，回傳 null，不要推測、不要計算、不要從藥名或常識補齊。
   frequency_code 的未知值固定 OTHER，陣列沒有資料時回傳 []。
2. usage_raw 必須原樣保留藥袋上印的用法字串（例如「TID PC」「早晚飯後各一顆」），
   不要改寫成別的說法。
3. frequency_code 只能從下列選一個：
   QD（一日一次）、BID（一日二次）、TID（一日三次）、QID（一日四次）、
   HS（睡前）、PRN（需要時服用）、OTHER。
   只要無法明確歸類到前六種，一律填 OTHER，不要勉強歸類。
4. patient_names 與 dispensed_dates 請列出影像中看到的所有值。
   如果畫面裡不只一個藥袋，就會有多個值——請如實列出，不要只留一個。
5. 一袋一藥是常態，但同一張影像可能包含多個藥袋，請把看到的藥品全部列出。
   同一張標籤也可能列多種藥，一列一個 drugs 項目，欄位不得跨列混用。
6. 先抄錄 frequency_raw，再依它填 frequency_code：例如「QD 每日一次」是 QD，
   「BID 每日二次」是 BID，「PRN 需要時」是 PRN。不要把頻次塞進 indication。
   每次1錠只代表 dose_per_time，不能據此推定 QD、總數量1或療程1天。
7. total_quantity 與 duration_days 都必須有同列的原文依據（對應 *_raw）。
   只印藥名、含量、每次用量、頻次與服用時間的表格，總數量與療程一律 null。
   即使能由其他欄位算出天數，也不可計算。看不清藥名的列不可猜測藥名。

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

        frequency_raw = (
            item.get("frequency_raw") if "frequency_raw" in item else item.get("usage_raw")
        )
        return RecognizedDrug(
            name=name,
            generic_name=_optional_string(item.get("generic_name")),
            unit_content=_optional_string(item.get("unit_content")),
            total_quantity=_evidenced_positive_int(
                item.get("total_quantity"), item.get("total_quantity_raw"), "quantity"
            ),
            usage_raw=_optional_string(item.get("usage_raw")) or _optional_string(frequency_raw),
            frequency_code=_coerce_frequency(
                item.get("frequency_code"), frequency_raw,
            ),
            dose_per_time=_optional_string(item.get("dose_per_time")),
            timing=_coerce_timing(item.get("timing")),
            duration_days=_evidenced_positive_int(
                item.get("duration_days"), item.get("duration_days_raw"), "duration"
            ),
            indication=_optional_string(item.get("indication")),
            # 名稱可信度由藥證庫決定，辨識階段一律 low。
            name_confidence="low",
        )


def _optional_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _positive_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        return int(value) if math.isfinite(value) and value.is_integer() and value > 0 else None
    if isinstance(value, str):
        text = unicodedata.normalize("NFKC", value).strip()
        if re.fullmatch(r"[0-9]+", text):
            try:
                number = int(text)
            except ValueError:
                return None
            return number if number > 0 else None
    return None


_QUANTITY_TEXT = re.compile(
    r"(?:總(?:數量|量|數|計)|數量|共|合計|QTY|QUANTITY|TOTALQUANTITY|TOTAL)[:：]?"
    r"([0-9]+)(?:錠|顆|粒|包|袋|瓶|支|片|個|膠囊|毫升|ML|TABLETS?|CAPSULES?|TABS?|CAPS?)?"
)
_DURATION_TEXT = re.compile(
    r"(?:(?:療程|(?:用藥|給藥)?(?:天數|日數)|共|DURATION)[:：]?([0-9]+)(?:天|日|DAYS?)(?:份)?"
    r"|([0-9]+)(?:天(?:份)?|日份|DAYS?))"
)


def _evidenced_positive_int(value: Any, raw: Any, kind: str) -> Optional[int]:
    """只接受有欄位語意、且數字一致的原文；不從每日／每次字樣推算。"""
    number = _positive_int(value)
    evidence = _optional_string(raw)
    if number is None or not evidence:
        return None
    text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", evidence)).upper()
    match = (_QUANTITY_TEXT if kind == "quantity" else _DURATION_TEXT).fullmatch(text)
    if match is None:
        return None
    printed = next(group for group in match.groups() if group is not None)
    try:
        return number if int(printed) == number else None
    except ValueError:
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


_DAILY_PREFIX = r"(?:每日|每天|一日|一天)(?:服用)?"
_FREQUENCY_WORDS = {
    "QD": _DAILY_PREFIX + r"(?:1|一)次(?:服用)?",
    "BID": _DAILY_PREFIX + r"(?:2|二|兩)次(?:服用)?",
    "TID": _DAILY_PREFIX + r"(?:3|三)次(?:服用)?",
    "QID": _DAILY_PREFIX + r"(?:4|四)次(?:服用)?",
    "HS": r"睡前(?:服用)?",
    "PRN": r"(?:需要|必要)時(?:服用)?",
}


def _coerce_frequency(value: Any, raw: Any) -> MedicationFrequencyCode:
    """原文明確才正規化；Q8H、衝突、否定或不完整字串一律保留 OTHER。"""
    text = _optional_string(value)
    code = unicodedata.normalize("NFKC", text).upper() if text else "OTHER"
    evidence = _optional_string(raw)
    if code not in FREQUENCY_TO_SLOTS or not evidence:
        return "OTHER"
    # 只去掉分隔標點，問號、否定詞與額外頻次仍會使 fullmatch 失敗。
    normalized = unicodedata.normalize("NFKC", evidence).upper()
    normalized = re.sub(r"[\s,，、;；:：/()（）\[\]]+", "", normalized)
    matches = [
        key for key, words in _FREQUENCY_WORDS.items()
        if re.fullmatch(rf"(?:{key}(?:{words})?|{words}(?:{key})?)(?:AC|PC)?", normalized)
    ]
    if len(matches) != 1 or code not in {"OTHER", matches[0]}:
        return "OTHER"
    return matches[0]  # type: ignore[return-value]


def _coerce_timing(value: Any) -> Optional[str]:
    text = _optional_string(value)
    if text in {"before_meal", "after_meal", "bedtime", "empty_stomach"}:
        return text
    return None
