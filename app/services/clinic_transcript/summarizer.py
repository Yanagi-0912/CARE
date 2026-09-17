"""把看診逐字稿整理成家人看得懂的摘要。

### 這份摘要刻意不說「醫師說」

上游的 `clinic_transcribe.py` 切得出段落，但不知道哪一段是醫師講的（官方寫
「3 位以上的講者歸屬是實驗性質」，而診間預設就有三到五個人）。既然不知道，
摘要就不能寫「醫師說要停掉降血壓的藥」——那句話一旦錯了，家人會照著做，
而這是整個功能唯一會真正害到人的失敗方式。

所以欄位名稱一律用「這次看診提到」的語氣，`PROMPT` 也明著禁止指派說話者。
長輩自己抱怨的「我這個藥吃了想吐」和醫師的判斷，在摘要裡長得一樣，
家人要分辨就點開原文看前後文——那是人做得比機器好的事。

### 為什麼有一欄專門放「聽不清楚的地方」

診間有口罩、有距離、有隔壁的聲音，辨識一定會有聽不出來的段落。沒有這一欄，
模型只能在「跳過」和「硬掰一句」之間選，而硬掰出來的醫囑看起來跟真的一樣。
給它一個誠實的去處，漏掉的東西才會浮上來讓家人知道要去問。

### 為什麼用藥那一欄要附原文

其他欄位寫錯，家人最多是誤會；用藥寫錯，長輩可能真的停藥或加量。附上原文
讓摘要可被當場否證，是這一欄唯一的防線（下游 `drug_hints.py` 再拿長輩自己的
用藥清單比對一次）。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# 逐字稿送進模型前的長度上限。門診三十分鐘的逐字稿大約一萬多字，這個值留了餘裕；
# 純粹是防呆，不是根據任何量測訂的。超過就截斷並在摘要標明，不要整份丟掉。
MAX_TRANSCRIPT_CHARS = 40_000

# 沒量過。抓得比一般結構化呼叫（`DrugMentionExtractor` 用 20 秒）寬，因為輸入長很多。
SUMMARY_TIMEOUT_SECONDS = 90.0


class StructuredTextInvoker(Protocol):
    async def invoke_structured_output(
        self, *, prompt: str, json_schema: dict[str, Any]
    ) -> Any: ...


SUMMARY_SCHEMA: dict[str, Any] = {
    "title": "ClinicVisitSummary",
    "type": "object",
    "properties": {
        "main_points": {
            "type": "array",
            "items": {"type": "string"},
            "description": "這次看診談到的重點，每則一句話",
        },
        "medication_changes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "用藥上的變動，一句話"},
                    "quote": {"type": "string", "description": "逐字稿裡對應的原文，照抄"},
                },
                "required": ["description", "quote"],
            },
            "description": "用藥的變動；沒談到就給空陣列",
        },
        "next_visit": {
            "type": "string",
            "description": "下次回診的時間或條件；沒談到就給空字串",
        },
        "reminders": {
            "type": "array",
            "items": {"type": "string"},
            "description": "要注意的事，例如飲食、活動、什麼情況要回診",
        },
        "unclear": {
            "type": "array",
            "items": {"type": "string"},
            "description": "聽不清楚或不確定的地方，建議家人回去問",
        },
    },
    "required": ["main_points", "medication_changes", "next_visit", "reminders", "unclear"],
}


PROMPT = """下面是一段看診的錄音逐字稿。段落之間換行的地方代表換人講話，但**沒有人知道哪一段是誰講的**。

請整理成給家人看的摘要，遵守下面的規則：

1. 絕對不要寫「醫師說」「病人說」「護理師說」或任何指出說話者的字眼。你沒有這個資訊，寫了就是編的。改用「這次看診提到」「有談到」這種說法。
2. 只寫逐字稿裡真的出現的內容。不要補常識、不要推論、不要把你自己的醫學知識加進去。
3. 逐字稿是語音辨識的結果，一定有聽錯的字。看不懂、前後矛盾、或明顯是聽錯的地方，不要猜它原本是什麼，寫進 unclear 那一欄，讓家人回去問。
4. medication_changes 每一則都要附上逐字稿裡對應的原文，照抄不要改寫。找不到明確原文就不要寫這一則。
5. 藥名和劑量照逐字稿寫，不要更正成你認為對的藥名或劑量。
6. 沒有談到的欄位就給空陣列或空字串，不要為了填滿而寫東西。

逐字稿：
{transcript}"""


@dataclass(frozen=True)
class MedicationChange:
    description: str
    quote: str


@dataclass(frozen=True)
class ClinicVisitSummary:
    main_points: tuple[str, ...] = ()
    medication_changes: tuple[MedicationChange, ...] = ()
    next_visit: str = ""
    reminders: tuple[str, ...] = ()
    unclear: tuple[str, ...] = field(default=())
    # 逐字稿被截斷時為 True，讓畫面提醒家人這份摘要沒有涵蓋全部。
    truncated: bool = False

    @property
    def is_empty(self) -> bool:
        return not (
            self.main_points or self.medication_changes or self.next_visit or self.reminders
        )


def _strings(payload: Any, key: str) -> tuple[str, ...]:
    raw = payload.get(key) if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return ()
    return tuple(item.strip() for item in raw if isinstance(item, str) and item.strip())


def _medication_changes(payload: Any) -> tuple[MedicationChange, ...]:
    raw = payload.get("medication_changes") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return ()
    changes: list[MedicationChange] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        description = str(item.get("description") or "").strip()
        quote = str(item.get("quote") or "").strip()
        # 規則 4：沒有原文就不成立。少一則，好過給家人一則無法核對的用藥指示。
        if description and quote:
            changes.append(MedicationChange(description=description, quote=quote))
    return tuple(changes)


class ClinicVisitSummarizer:
    def __init__(
        self,
        gemini_service: StructuredTextInvoker,
        timeout_seconds: float = SUMMARY_TIMEOUT_SECONDS,
    ) -> None:
        self._gemini = gemini_service
        self._timeout_seconds = timeout_seconds

    async def summarize(self, transcript: str) -> ClinicVisitSummary:
        """摘要失敗回傳空摘要，不拋錯。

        逐字稿本身已經存起來了，摘要只是加值。為了摘要失敗就讓整次看診紀錄不見，
        方向是錯的——家人至少還能自己讀原文。
        """
        text = (transcript or "").strip()
        if not text:
            return ClinicVisitSummary()

        truncated = len(text) > MAX_TRANSCRIPT_CHARS
        if truncated:
            text = text[:MAX_TRANSCRIPT_CHARS]

        try:
            payload = await asyncio.wait_for(
                self._gemini.invoke_structured_output(
                    prompt=PROMPT.format(transcript=text),
                    json_schema=SUMMARY_SCHEMA,
                ),
                timeout=self._timeout_seconds,
            )
        except asyncio.TimeoutError:
            # log 不帶逐字稿內容：那是整段診間對話，敏感度比一般訊息高。
            logger.warning("看診摘要逾時（%s 秒）", self._timeout_seconds)
            return ClinicVisitSummary(truncated=truncated)
        except Exception as exc:  # noqa: BLE001 - 加值功能，任何例外都不得讓紀錄消失
            logger.warning("看診摘要失敗：%s", type(exc).__name__)
            return ClinicVisitSummary(truncated=truncated)

        next_visit = payload.get("next_visit") if isinstance(payload, dict) else ""
        summary = ClinicVisitSummary(
            main_points=_strings(payload, "main_points"),
            medication_changes=_medication_changes(payload),
            next_visit=str(next_visit or "").strip(),
            reminders=_strings(payload, "reminders"),
            unclear=_strings(payload, "unclear"),
            truncated=truncated,
        )
        logger.info(
            "stage=clinic_summary points=%d med_changes=%d unclear=%d truncated=%s",
            len(summary.main_points),
            len(summary.medication_changes),
            len(summary.unclear),
            truncated,
        )
        return summary
