"""從一段文字抽出「提到了哪些藥品」。

輸入可能是使用者自己打的字，也可能是圖片經既有管線 OCR 後的全文——兩者對本
服務沒有差別，它只讀字串，SHALL NOT 下載影像或對影像呼叫模型。

抽取階段只輸出事實：文字裡出現了什麼名稱、什麼描述、有沒有提到取得管道。
schema 刻意不含任何風險欄位，判定留給 `risk_rules.assess()` 的純函式；把「要不
要驚動全家」交給輸出會漂移的模型，等於讓門檻無法被窮舉測試釘住。
"""

import asyncio
import logging
from typing import Any, Optional, Protocol

from app.models.safety import AcquisitionChannel, DrugMention
from app.services.safety.risk_rules import DISPENSED_PACKAGE_MARKERS

logger = logging.getLogger(__name__)

_CHANNELS: tuple[str, ...] = (
    "medical_institution",
    "licensed_pharmacy",
    "overseas_personal",
    "online_marketplace",
    "acquaintance",
    "tv_shopping",
    "unknown",
)


class StructuredTextInvoker(Protocol):
    async def invoke_structured_output(
        self,
        *,
        prompt: str,
        json_schema: dict[str, Any],
    ) -> Any:
        ...


MENTION_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "mentions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "raw_name": {"type": "string"},
                    "source_text": {"type": "string"},
                    "channel": {"type": "string", "enum": list(_CHANNELS)},
                    "dispensed_package_markers": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": list(DISPENSED_PACKAGE_MARKERS),
                        },
                    },
                },
                "required": ["raw_name"],
            },
        }
    },
    "required": ["mentions"],
}

EXTRACTION_PROMPT = """你正在讀一段來自聊天室的文字。它可能是使用者自己打的字，也可能是一張照片上的全部文字（例如藥品包裝或藥袋）。

請只記錄文字中實際出現的藥品，並遵守：

1. raw_name 原樣保留文字中出現的藥品名稱，不要翻譯、不要改寫、不要補上劑型或劑量。
2. source_text 保留文字中描述這個藥品來源或用途的原句；沒有就留空。
3. channel 只在文字明講取得管道時才填，並從下列選一個：
   medical_institution（醫療機構或診所給的）、licensed_pharmacy（藥局購買）、
   overseas_personal（境外攜帶或代購）、online_marketplace（網路賣場）、
   acquaintance（親友給的或介紹的）、tv_shopping（電視購物）。
   文字沒有明講就填 unknown，不要從常識推測。
4. dispensed_package_markers 只在文字中確實出現對應欄位時才列出：
   patient_name（病患姓名）、institution（調劑機構名稱）、dispenser（調劑者或藥師姓名）、
   dispensed_date（調劑日期）。
5. 只記錄文字中出現的內容。不要判斷這個藥安不安全、合不合法，也不要給任何建議。
6. 文字中沒有提到任何藥品時，回傳空陣列。

只輸出符合 schema 的結果。

--- 以下是要讀的文字 ---
{text}"""


class DrugMentionExtractor:
    def __init__(
        self,
        gemini_service: StructuredTextInvoker,
        timeout_seconds: float = 20,
    ) -> None:
        self._gemini_service = gemini_service
        self._timeout_seconds = timeout_seconds

    async def extract(self, text: str) -> list[DrugMention]:
        """抽取失敗一律回傳空清單。

        使用者並沒有在等這個結果，任何例外往外拋只會變成主流程要處理的雜訊；
        空清單的後果是「這次不判定」，方向是安全的。
        """
        if not text or not text.strip():
            return []

        try:
            payload = await asyncio.wait_for(
                self._gemini_service.invoke_structured_output(
                    prompt=EXTRACTION_PROMPT.format(text=text),
                    json_schema=MENTION_EXTRACTION_SCHEMA,
                ),
                timeout=self._timeout_seconds,
            )
        except asyncio.TimeoutError:
            # log 一律不帶輸入文字：它可能是病情描述，也可能是含姓名與就診
            # 機構的藥袋全文。
            logger.warning("用藥風險抽取逾時（%s 秒），本次不判定", self._timeout_seconds)
            return []
        except Exception as exc:  # noqa: BLE001 - 背景旁路，任何例外都不得逸散
            logger.warning("用藥風險抽取失敗，本次不判定：%s", type(exc).__name__)
            return []

        return self._parse(payload)

    def _parse(self, payload: Any) -> list[DrugMention]:
        if not isinstance(payload, dict):
            logger.warning("用藥風險抽取回應不是物件，本次不判定")
            return []

        raw_mentions = payload.get("mentions")
        if not isinstance(raw_mentions, list):
            logger.warning("用藥風險抽取回應缺少 mentions 陣列，本次不判定")
            return []

        parsed = (self._parse_mention(item) for item in raw_mentions)
        return [mention for mention in parsed if mention is not None]

    def _parse_mention(self, item: Any) -> Optional[DrugMention]:
        if not isinstance(item, dict):
            return None

        raw_name = _optional_string(item.get("raw_name"))
        if not raw_name:
            # 沒有名稱就無從比對藥證庫，也無從判定。丟掉比留空殼好。
            return None

        return DrugMention(
            raw_name=raw_name,
            source_text=_optional_string(item.get("source_text")),
            channel=_coerce_channel(item.get("channel")),
            dispensed_package_markers=_known_markers(
                item.get("dispensed_package_markers")
            ),
        )


def _optional_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_channel(value: Any) -> AcquisitionChannel:
    """列舉外的值落回 unknown，而不是讓整次抽取失敗。

    模型偶爾會自創通路名稱。那一筆的通路不可用，但藥名與其他訊號仍有價值，
    且 unknown 本來就是「沒說」的預設。
    """
    text = _optional_string(value)
    if text in _CHANNELS:
        return text  # type: ignore[return-value]
    return "unknown"


def _known_markers(value: Any) -> list[str]:
    """只留下已定義的法定必載欄位訊號。

    模型自創的名稱留著只會讓「四項齊備」的判斷失去意義——多一個 barcode
    不會讓它更像合法調劑包裝。
    """
    if not isinstance(value, list):
        return []

    markers: list[str] = []
    for item in value:
        text = _optional_string(item)
        if text in DISPENSED_PACKAGE_MARKERS and text not in markers:
            markers.append(text)
    return markers
