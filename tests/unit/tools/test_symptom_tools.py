import json

import pytest

from app.services.medical.symptom_classification.symptom_department_service import (
    RESULT_FALLBACK,
    RESULT_SUGGESTION,
    SymptomTriageResult,
)
from app.services.medical.symptom_classification.symptom_table import DepartmentCandidate
from app.tools import symptom_tools
from app.tools.registry import get_all_tools
from app.tools.symptom_tools import (
    configure_symptom_tool,
    suggest_department_for_symptom,
)


class StubService:
    def __init__(self, result):
        self._result = result
        self.calls: list[str] = []

    async def suggest(self, text):
        self.calls.append(text)
        return self._result


@pytest.fixture(autouse=True)
def reset_tool():
    """每個測試獨立設定注入狀態，結束後還原，避免影響其他測試模組。"""
    original = symptom_tools._symptom_department_service
    yield
    configure_symptom_tool(original)


def _suggestion(*names):
    return SymptomTriageResult(
        kind=RESULT_SUGGESTION,
        user_input="肚子痛",
        matched_term="腹痛",
        candidates=tuple(
            DepartmentCandidate(
                canonical=name, subgroup=None, facility_count=100, source_count=2
            )
            for name in names
        ),
    )


# ---------------------------------------------------------------- 旗標與註冊


@pytest.mark.parametrize("include_rag_tool", [True, False])
def test_tool_is_always_registered(include_rag_tool):
    """
    問掛號科別不是查知識庫，與 guardrail 是否放行 RAG 無關，
    因此不隨 include_rag_tool 開關，與其他醫療工具一致。
    """
    names = {tool.name for tool in get_all_tools(include_rag_tool=include_rag_tool)}
    assert "suggest_department_for_symptom" in names


def test_other_medical_tools_unaffected():
    names = {tool.name for tool in get_all_tools(include_rag_tool=True)}
    for expected in (
        "find_nearby_hospitals",
        "find_nearby_facilities_by_department",
        "lookup_medical_facility",
        "request_location_quick_reply",
        "get_rag_answer",
    ):
        assert expected in names


# ---------------------------------------------------------------- 工具輸出


@pytest.mark.asyncio
async def test_returns_flex_envelope():
    configure_symptom_tool(StubService(_suggestion("內科", "兒科")))
    payload = json.loads(
        await suggest_department_for_symptom.ainvoke({"symptom": "肚子好痛"})
    )
    assert payload["type"] == "flex"
    assert payload["contents"]["type"] == "bubble"


@pytest.mark.asyncio
async def test_passes_symptom_through_untouched():
    """工具不得自行改寫使用者的說法，正規化是服務層的事。"""
    stub = StubService(_suggestion("內科"))
    configure_symptom_tool(stub)
    await suggest_department_for_symptom.ainvoke({"symptom": "肚子好痛"})
    assert stub.calls == ["肚子好痛"]


@pytest.mark.asyncio
async def test_uninitialized_service_returns_message_not_exception():
    configure_symptom_tool(None)
    reply = await suggest_department_for_symptom.ainvoke({"symptom": "肚子痛"})
    assert "未初始化" in reply


# ---------------------------------------------------------------- 純文字 fallback


@pytest.mark.parametrize(
    "result",
    [
        SymptomTriageResult(
            kind=RESULT_SUGGESTION,
            user_input="x",
            matched_term="腹痛",
            candidates=(DepartmentCandidate("內科", "胃腸肝膽", 100, 3),),
        ),
        SymptomTriageResult(
            kind=RESULT_FALLBACK,
            user_input="x",
            fallback_reason="無法對應到已知的症狀條目",
            candidates=(
                DepartmentCandidate("家醫科", None, 0, 0),
                DepartmentCandidate("內科", None, 0, 0),
            ),
        ),
    ],
)
def test_plain_reply_has_no_markdown(result):
    """line-reply-rules：LINE 回覆一律純文字，不得輸出 Markdown。"""
    text = symptom_tools._format_plain_reply(result)
    assert text
    for token in ("**", "##", "```", "](", "* "):
        assert token not in text


def test_plain_reply_never_contains_emergency_content():
    """
    急迫度已經拆到 agent 之前，判定為緊急的訊息根本不會走到這個工具。
    這裡守住反向性質：本工具的輸出不得再夾帶急診指示或專線號碼，否則就是
    第二套沒人維護的急症判斷偷偷長回來。
    """
    for result in (
        SymptomTriageResult(
            kind=RESULT_SUGGESTION,
            user_input="x",
            matched_term="腹痛",
            candidates=(DepartmentCandidate("內科", None, 100, 3),),
        ),
        SymptomTriageResult(
            kind=RESULT_FALLBACK,
            user_input="x",
            fallback_reason="無法對應到已知的症狀條目",
            candidates=(DepartmentCandidate("家醫科", None, 0, 0),),
        ),
    ):
        text = symptom_tools._format_plain_reply(result)
        for token in ("119", "110", "1925", "急診", "tel:"):
            assert token not in text
