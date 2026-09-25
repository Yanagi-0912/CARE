import json

import pytest

from app.core.request_context import reset_line_user_id, set_line_user_id
from app.services.family.patient_context import (
    PatientCandidate,
    PatientContext,
    ValueSource,
)
from app.services.medical.symptom_classification.symptom_department_service import (
    PEDIATRIC_REASON_AGE,
    PEDIATRIC_REASON_MENTIONED_CHILD,
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
        self.contexts: list[PatientContext] = []
        self.requested_departments: list[str] = []

    async def suggest(self, text, *, patient_context, requested_department=""):
        self.calls.append(text)
        self.contexts.append(patient_context)
        self.requested_departments.append(requested_department)
        return self._result


class StubPatientContextService:
    def __init__(self, context: PatientContext | None = None):
        self.context = context or PatientContext(
            operator_id="U_OPERATOR",
            patient_kind="self",
            patient_id="U_OPERATOR",
        )
        self.calls: list[tuple[str, dict]] = []

    async def resolve(self, operator_id, **kwargs):
        self.calls.append((operator_id, kwargs))
        return self.context


def _configure(service, patient_context_service=None):
    configure_symptom_tool(
        service,
        patient_context_service or StubPatientContextService(),
    )


@pytest.fixture(autouse=True)
def reset_tool():
    """每個測試獨立設定注入狀態，結束後還原，避免影響其他測試模組。"""
    original_service = symptom_tools._symptom_department_service
    original_context_service = symptom_tools._patient_context_service
    token = set_line_user_id("U_OPERATOR")
    try:
        yield
    finally:
        reset_line_user_id(token)
        configure_symptom_tool(original_service, original_context_service)


def _suggestion(*names):
    return SymptomTriageResult(
        kind=RESULT_SUGGESTION,
        user_input="肚子痛",
        matched_term="腹痛",
        candidates=tuple(
            DepartmentCandidate(
                canonical=name, subgroup=None, facility_count=100, sources=("V", "N")
            )
            for name in names
        ),
    )


def _case(symptom: str, **kwargs) -> dict:
    return {"cases": [{"symptom": symptom, **kwargs}]}


# ---------------------------------------------------------------- 註冊


@pytest.mark.parametrize("include_rag_tool", [True, False])
def test_tool_is_always_registered(include_rag_tool):
    """
    問掛號科別不是查知識庫，與 guardrail 是否放行 RAG 無關，
    因此不隨 include_rag_tool 開關，與其他醫療工具一致。
    """
    names = {tool.name for tool in get_all_tools(include_rag_tool=include_rag_tool)}
    assert "suggest_department_for_symptom" in names


# ---------------------------------------------------------------- 工具輸出


@pytest.mark.asyncio
async def test_returns_flex_envelope():
    _configure(StubService(_suggestion("內科", "兒科")))
    payload = json.loads(
        await suggest_department_for_symptom.ainvoke(_case("肚子好痛"))
    )
    assert payload["type"] == "flex"
    assert payload["contents"]["type"] == "bubble"


@pytest.mark.asyncio
async def test_passes_symptom_through_untouched():
    """工具不得自行改寫使用者的說法，正規化是服務層的事。"""
    stub = StubService(_suggestion("內科"))
    _configure(stub)
    await suggest_department_for_symptom.ainvoke(_case("肚子好痛"))
    assert stub.calls == ["肚子好痛"]


@pytest.mark.asyncio
async def test_builds_patient_context_from_structured_tool_arguments():
    context = PatientContext(
        operator_id="U_OPERATOR",
        patient_kind="member",
        patient_id="U_CHILD",
        display_label="王小明",
        relationship="child",
        age=5,
        age_source=ValueSource.MESSAGE,
        gender="male",
        gender_source=ValueSource.MESSAGE,
    )
    contexts = StubPatientContextService(context)
    departments = StubService(_suggestion("兒科"))
    _configure(departments, contexts)

    await suggest_department_for_symptom.ainvoke(
        _case("一直嘔吐", relationship="child", age=5, gender="male")
    )

    assert contexts.calls == [
        (
            "U_OPERATOR",
            {
                "person": "",
                "relationship": "child",
                "message_age": 5,
                "message_gender": "male",
            },
        )
    ]
    assert departments.calls == ["一直嘔吐"]
    assert departments.contexts == [context]
    assert departments.requested_departments == [""]


@pytest.mark.asyncio
async def test_explicit_department_is_forwarded_separately_from_symptom_text():
    departments = StubService(_suggestion("婦產科"))
    _configure(departments)

    await suggest_department_for_symptom.ainvoke(
        _case("肚子痛", requested_department="婦產科")
    )

    assert departments.calls == ["肚子痛"]
    assert departments.requested_departments == ["婦產科"]


@pytest.mark.asyncio
async def test_multiple_patient_cases_ask_which_person_first_without_any_lookup():
    contexts = StubPatientContextService()
    departments = StubService(_suggestion("內科"))
    _configure(departments, contexts)

    reply = await suggest_department_for_symptom.ainvoke(
        {
            "cases": [
                {"symptom": "肚子痛", "relationship": "spouse"},
                {"symptom": "頭痛"},
            ]
        }
    )

    assert reply == (
        "這則訊息提到多位需要看診的人。"
        "請先告訴我想先處理哪一位，以及他的症狀。"
    )
    assert contexts.calls == []
    assert departments.calls == []


@pytest.mark.asyncio
async def test_empty_patient_cases_ask_for_person_and_symptom_without_lookup():
    contexts = StubPatientContextService()
    departments = StubService(_suggestion("內科"))
    _configure(departments, contexts)

    reply = await suggest_department_for_symptom.ainvoke({"cases": []})

    assert reply == "請告訴我是哪一位需要看診，以及他的症狀。"
    assert contexts.calls == []
    assert departments.calls == []


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("en", "more than one person"),
        ("id", "lebih dari satu orang"),
        ("vi", "nhiều người"),
        ("th", "มากกว่าหนึ่งคน"),
        ("ja", "複数います"),
    ],
)
@pytest.mark.asyncio
async def test_multiple_patient_reply_uses_request_language(language, expected):
    from app.core.user_language import reset_request_language, set_request_language

    contexts = StubPatientContextService()
    departments = StubService(_suggestion("內科"))
    _configure(departments, contexts)
    token = set_request_language(language)
    try:
        reply = await suggest_department_for_symptom.ainvoke(
            {"cases": [{"symptom": "x"}, {"symptom": "y"}]}
        )
    finally:
        reset_request_language(token)

    assert expected in reply
    assert contexts.calls == []
    assert departments.calls == []


@pytest.mark.asyncio
async def test_ambiguous_patient_asks_for_full_name_without_querying_symptom_table():
    context = PatientContext(
        operator_id="U_OPERATOR",
        patient_kind="ambiguous",
        display_label="parent",
        relationship="parent",
        candidates=(
            PatientCandidate("U_FATHER", "王大明", "parent"),
            PatientCandidate("U_MOTHER", "林美玲", "parent"),
        ),
    )
    contexts = StubPatientContextService(context)
    departments = StubService(_suggestion("內科"))
    _configure(departments, contexts)

    reply = await suggest_department_for_symptom.ainvoke(
        _case("頭痛", relationship="parent")
    )

    assert reply == (
        "找到多位符合的家人：王大明、林美玲。"
        "請說完整姓名後，再問一次要看哪一科。"
    )
    assert departments.calls == []


@pytest.mark.asyncio
async def test_conflicting_name_and_relationship_asks_for_correction_without_lookup():
    context = PatientContext(
        operator_id="U_OPERATOR",
        patient_kind="conflict",
        display_label="王美玲",
        relationship="parent",
        candidates=(PatientCandidate("U_SPOUSE", "王美玲", "spouse"),),
    )
    departments = StubService(_suggestion("內科"))
    _configure(departments, StubPatientContextService(context))

    reply = await suggest_department_for_symptom.ainvoke(
        _case("頭痛", person="王美玲", relationship="parent")
    )

    assert reply == (
        "「王美玲」與指定的稱謂不一致。"
        "請確認姓名或稱謂後，再問一次要看哪一科。"
    )
    assert departments.calls == []


@pytest.mark.asyncio
async def test_unlinked_patient_still_gets_general_advice_from_message_values():
    context = PatientContext(
        operator_id="U_OPERATOR",
        patient_kind="not_found",
        display_label="隔壁阿伯",
        age=70,
        age_source=ValueSource.MESSAGE,
        gender="male",
        gender_source=ValueSource.MESSAGE,
    )
    departments = StubService(_suggestion("神經內科"))
    _configure(departments, StubPatientContextService(context))

    payload = json.loads(
        await suggest_department_for_symptom.ainvoke(
            _case("頭痛", person="隔壁阿伯", age=70, gender="male")
        )
    )

    assert payload["type"] == "flex"
    assert departments.calls == ["頭痛"]
    assert departments.contexts == [context]


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("en", "More than one family member matches: A, B."),
        ("id", "Ada beberapa anggota keluarga yang cocok: A, B."),
        ("vi", "Có nhiều người thân phù hợp: A, B."),
        ("th", "พบสมาชิกครอบครัวที่ตรงกันหลายคน: A, B"),
        ("ja", "該当するご家族が複数います：A、B。"),
    ],
)
@pytest.mark.asyncio
async def test_ambiguous_patient_reply_uses_request_language(language, expected):
    from app.core.user_language import reset_request_language, set_request_language

    context = PatientContext(
        operator_id="U_OPERATOR",
        patient_kind="ambiguous",
        candidates=(
            PatientCandidate("U_A", "A", "parent"),
            PatientCandidate("U_B", "B", "parent"),
        ),
    )
    departments = StubService(_suggestion("內科"))
    _configure(departments, StubPatientContextService(context))
    token = set_request_language(language)
    try:
        reply = await suggest_department_for_symptom.ainvoke(_case("頭痛"))
    finally:
        reset_request_language(token)

    assert expected in reply
    assert departments.calls == []


def test_tool_schema_exposes_only_structured_patient_clues():
    schema = suggest_department_for_symptom.args_schema.model_json_schema()

    assert set(schema["properties"]) == {"cases"}
    assert schema["required"] == ["cases"]
    case_schema = schema["$defs"]["SymptomPatientCase"]
    assert set(case_schema["properties"]) == {
        "symptom",
        "person",
        "relationship",
        "age",
        "gender",
        "requested_department",
    }
    assert case_schema["required"] == ["symptom"]
    relationship_schema = case_schema["properties"]["relationship"]
    relationship_enum = next(
        option["enum"]
        for option in relationship_schema["anyOf"]
        if "enum" in option
    )
    assert set(relationship_enum) == {
        "parent",
        "child",
        "spouse",
        "sibling",
        "grandparent",
        "grandchild",
    }
    assert "" not in relationship_enum


def test_gemini_schema_uses_an_optional_non_empty_relationship_enum():
    """Gemini 會先驗證整包工具；巢狀 enum 含空字串時連寒暄都會回 400。"""
    from langchain_google_genai._function_utils import (
        convert_to_genai_function_declarations,
    )

    declaration = convert_to_genai_function_declarations(
        [suggest_department_for_symptom]
    )[0].function_declarations[0]
    case_schema = declaration.parameters.properties["cases"].items
    relationship_schema = case_schema.properties["relationship"]

    assert relationship_schema.enum == [
        "parent",
        "child",
        "spouse",
        "sibling",
        "grandparent",
        "grandchild",
    ]
    assert "relationship" not in case_schema.required


@pytest.mark.asyncio
async def test_request_language_context_localizes_the_flex_card():
    from app.core.user_language import reset_request_language, set_request_language

    _configure(StubService(_suggestion("內科")))
    token = set_request_language("en")
    try:
        payload = json.loads(
            await suggest_department_for_symptom.ainvoke(_case("肚子好痛"))
        )
    finally:
        reset_request_language(token)

    assert payload["altText"] == "Suggested care departments"
    assert payload["contents"]["header"]["contents"][1]["contents"][0]["text"] == (
        "Internal Medicine"
    )


@pytest.mark.asyncio
async def test_uninitialized_service_returns_message_not_exception():
    _configure(None)
    reply = await suggest_department_for_symptom.ainvoke(_case("肚子痛"))
    assert "未初始化" in reply


@pytest.mark.asyncio
async def test_missing_patient_context_service_returns_message_not_exception():
    configure_symptom_tool(StubService(_suggestion("內科")), None)

    reply = await suggest_department_for_symptom.ainvoke(_case("肚子痛"))

    assert "未初始化" in reply


@pytest.mark.asyncio
async def test_missing_operator_id_does_not_query_patient_or_department():
    contexts = StubPatientContextService()
    departments = StubService(_suggestion("內科"))
    _configure(departments, contexts)
    token = set_line_user_id("")
    try:
        reply = await suggest_department_for_symptom.ainvoke(_case("肚子痛"))
    finally:
        reset_line_user_id(token)

    assert "未初始化" in reply
    assert contexts.calls == []
    assert departments.calls == []


# ---------------------------------------------------------------- 純文字 fallback


@pytest.mark.parametrize(
    "result",
    [
        SymptomTriageResult(
            kind=RESULT_SUGGESTION,
            user_input="x",
            matched_term="腹痛",
            candidates=(DepartmentCandidate("內科", "胃腸肝膽", 100, ("V", "N", "Y")),),
        ),
        SymptomTriageResult(
            kind=RESULT_FALLBACK,
            user_input="x",
            fallback_reason="無法對應到已知的症狀條目",
            candidates=(
                DepartmentCandidate("家醫科", None, 0, ()),
                DepartmentCandidate("內科", None, 0, ()),
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
            candidates=(DepartmentCandidate("內科", None, 100, ("V", "N", "Y")),),
        ),
        SymptomTriageResult(
            kind=RESULT_FALLBACK,
            user_input="x",
            fallback_reason="無法對應到已知的症狀條目",
            candidates=(DepartmentCandidate("家醫科", None, 0, ()),),
        ),
    ):
        text = symptom_tools._format_plain_reply(result)
        for token in ("119", "110", "1925", "急診", "tel:"):
            assert token not in text


def _fallback(*names, pediatric_reason=None):
    return SymptomTriageResult(
        kind=RESULT_FALLBACK,
        user_input="x",
        fallback_reason="無法對應到已知的症狀條目",
        candidates=tuple(DepartmentCandidate(name, None, 0, ()) for name in names),
        pediatric_reason=pediatric_reason,
    )


@pytest.mark.parametrize(
    ("pediatric_reason", "note"),
    [
        (PEDIATRIC_REASON_MENTIONED_CHILD, "因為是幫孩子詢問，另外列出兒科。"),
        (PEDIATRIC_REASON_AGE, "因為看診者未滿 15 歲，另外列出兒科。"),
    ],
)
def test_plain_reply_explains_pediatrics_in_child_fallback(pediatric_reason, note):
    """卡片建不起來時的純文字回覆，同樣要說明為什麼多了兒科。"""
    text = symptom_tools._format_plain_reply(
        _fallback("兒科", "家醫科", "內科", "不分科", pediatric_reason=pediatric_reason)
    )
    assert note in text
    assert "1. 兒科" in text and "4. 不分科" in text


def test_plain_reply_for_adult_fallback_has_no_pediatric_note():
    text = symptom_tools._format_plain_reply(_fallback("家醫科", "內科", "不分科"))
    assert "另外列出兒科" not in text


@pytest.mark.parametrize(
    ("language", "department", "subgroup"),
    [
        ("en", "Internal Medicine", "Gastroenterology and Hepatology"),
        ("id", "Penyakit Dalam", "Gastroenterologi dan Hepatologi"),
        ("vi", "Nội khoa", "Tiêu hóa và gan mật"),
        ("th", "อายุรกรรม", "ระบบทางเดินอาหารและตับ"),
        ("ja", "内科", "消化器・肝臓内科"),
    ],
)
def test_plain_reply_is_localized(language, department, subgroup):
    result = SymptomTriageResult(
        kind=RESULT_SUGGESTION,
        user_input="x",
        matched_term="腹痛",
        candidates=(
            DepartmentCandidate("內科", "胃腸肝膽科", 100, ("V", "N")),
        ),
    )

    text = symptom_tools._format_plain_reply(result, language)

    assert department in text
    assert subgroup in text
    assert "腹痛" not in text
