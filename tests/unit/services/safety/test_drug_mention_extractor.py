import asyncio
import json
import logging

import pytest

from app.services.gemini.shared.errors import GeminiParseError, GeminiUnknownError
from app.services.safety.drug_mention_extractor import (
    MENTION_EXTRACTION_SCHEMA,
    DrugMentionExtractor,
)

BAG_OCR_TEXT = (
    "台大醫院 藥品調劑袋\n病患姓名：王大明\n調劑日期：2026-08-16\n"
    "調劑者：李藥師\n普拿疼錠500毫克 每日三次"
)


class FakeGeminiService:
    """建構子注入的替身。記錄呼叫參數，回傳預先安排好的 payload。"""

    def __init__(self, payload=None, error=None, delay=0.0):
        self._payload = payload
        self._error = error
        self._delay = delay
        self.calls = []

    async def invoke_structured_output(self, *, prompt, json_schema):
        self.calls.append({"prompt": prompt, "json_schema": json_schema})
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return self._payload


async def test_extract_returns_mentions_from_payload():
    gemini = FakeGeminiService(
        payload={
            "mentions": [
                {
                    "raw_name": "合利他命強効錠 EX PLUS",
                    "source_text": "朋友從日本帶回來的",
                    "channel": "overseas_personal",
                    "dispensed_package_markers": [],
                }
            ]
        }
    )
    extractor = DrugMentionExtractor(gemini_service=gemini, timeout_seconds=20)

    mentions = await extractor.extract("朋友從日本帶回來的合利他命強効錠 EX PLUS")

    assert len(mentions) == 1
    assert mentions[0].raw_name == "合利他命強効錠 EX PLUS"
    assert mentions[0].source_text == "朋友從日本帶回來的"
    assert mentions[0].channel == "overseas_personal"
    # 藥證庫比對是抽取之後的事，抽取器不得自行填。
    assert mentions[0].catalog_hit is False
    assert mentions[0].license_number is None


async def test_extract_passes_input_text_to_the_model():
    gemini = FakeGeminiService(payload={"mentions": []})
    extractor = DrugMentionExtractor(gemini_service=gemini)

    await extractor.extract("這個藥可以吃嗎")

    assert "這個藥可以吃嗎" in gemini.calls[0]["prompt"]
    assert gemini.calls[0]["json_schema"] is MENTION_EXTRACTION_SCHEMA


async def test_extract_collects_dispensed_package_markers_from_bag_ocr():
    """輸入可能是圖片的 OCR 全文；法定必載欄位的訊號要抽得出來。"""
    gemini = FakeGeminiService(
        payload={
            "mentions": [
                {
                    "raw_name": "普拿疼錠500毫克",
                    "channel": "medical_institution",
                    "dispensed_package_markers": [
                        "patient_name",
                        "institution",
                        "dispenser",
                        "dispensed_date",
                    ],
                }
            ]
        }
    )
    extractor = DrugMentionExtractor(gemini_service=gemini)

    mentions = await extractor.extract(BAG_OCR_TEXT)

    assert mentions[0].dispensed_package_markers == [
        "patient_name",
        "institution",
        "dispenser",
        "dispensed_date",
    ]


async def test_extract_drops_markers_outside_the_known_set():
    """模型自創的訊號名稱會讓「齊備」的判斷失去意義，直接丟掉。"""
    gemini = FakeGeminiService(
        payload={
            "mentions": [
                {
                    "raw_name": "某藥",
                    "dispensed_package_markers": ["patient_name", "barcode"],
                }
            ]
        }
    )
    extractor = DrugMentionExtractor(gemini_service=gemini)

    mentions = await extractor.extract(BAG_OCR_TEXT)

    assert mentions[0].dispensed_package_markers == ["patient_name"]


async def test_extract_falls_back_to_unknown_channel():
    """列舉外的通路只讓那一個欄位落回 unknown，不讓整次抽取失敗。"""
    gemini = FakeGeminiService(
        payload={"mentions": [{"raw_name": "某藥", "channel": "black_market"}]}
    )
    extractor = DrugMentionExtractor(gemini_service=gemini)

    mentions = await extractor.extract("某藥")

    assert mentions[0].channel == "unknown"


async def test_extract_discards_items_without_a_name():
    """沒有名稱的項目既不能比對藥證庫也不能判定，留著只會變成空殼。"""
    gemini = FakeGeminiService(
        payload={
            "mentions": [
                {"raw_name": "  ", "channel": "unknown"},
                {"raw_name": "普拿疼", "channel": "unknown"},
            ]
        }
    )
    extractor = DrugMentionExtractor(gemini_service=gemini)

    mentions = await extractor.extract("普拿疼")

    assert [m.raw_name for m in mentions] == ["普拿疼"]


async def test_extract_returns_empty_on_timeout():
    """本能力是背景旁路，逾時一律靜默結束，SHALL NOT 往外拋。"""
    gemini = FakeGeminiService(payload={"mentions": []}, delay=0.05)
    extractor = DrugMentionExtractor(gemini_service=gemini, timeout_seconds=0.01)

    assert await extractor.extract("某藥") == []


@pytest.mark.parametrize(
    "error",
    [GeminiParseError("壞掉的 JSON"), GeminiUnknownError("未知"), RuntimeError("爆了")],
)
async def test_extract_returns_empty_on_any_error(error):
    extractor = DrugMentionExtractor(gemini_service=FakeGeminiService(error=error))

    assert await extractor.extract("某藥") == []


@pytest.mark.parametrize("payload", [None, "字串", {"mentions": "不是陣列"}, {}])
async def test_extract_returns_empty_on_malformed_payload(payload):
    extractor = DrugMentionExtractor(gemini_service=FakeGeminiService(payload=payload))

    assert await extractor.extract("某藥") == []


async def test_extract_skips_the_model_for_blank_input():
    gemini = FakeGeminiService(payload={"mentions": []})
    extractor = DrugMentionExtractor(gemini_service=gemini)

    assert await extractor.extract("   ") == []
    assert gemini.calls == []


async def test_extract_log_does_not_leak_input_text(caplog):
    """log 出現在集中式收容裡，帶上原文等於把病情與姓名一併寫進去。"""
    extractor = DrugMentionExtractor(
        gemini_service=FakeGeminiService(error=GeminiUnknownError("boom"))
    )

    with caplog.at_level(logging.WARNING):
        await extractor.extract(BAG_OCR_TEXT)

    logged = " ".join(record.getMessage() for record in caplog.records)
    assert logged != ""
    assert "王大明" not in logged
    assert "台大醫院" not in logged


def test_extraction_schema_carries_no_risk_conclusion():
    """抽取只輸出事實。schema 一旦有風險欄位，判定就會悄悄搬回模型裡。"""
    serialized = json.dumps(MENTION_EXTRACTION_SCHEMA, ensure_ascii=False)

    assert "risk" not in serialized
    assert "safety" not in serialized
    assert "danger" not in serialized
