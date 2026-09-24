"""科別工具的人物解析整合驗收（tasks 10.12）。"""

from datetime import datetime, timezone
import json

import pytest
from fastapi import HTTPException

from app.core.request_context import reset_line_user_id, set_line_user_id
from app.models.family_tree import FamilyMember, FamilyTree
from app.services.family.patient_context_service import PatientContextService
from app.services.medical.symptom_classification.symptom_department_service import (
    SymptomDepartmentService,
)
from app.services.medical.symptom_classification.symptom_table import (
    DepartmentCandidate,
    SymptomEntry,
    SymptomTable,
)
from app.tools import symptom_tools
from app.tools.symptom_tools import (
    configure_symptom_tool,
    suggest_department_for_symptom,
)


OPERATOR = "U_OPERATOR"
SPOUSE = "U_SPOUSE"
CHILD = "U_CHILD"
PARENT = "U_PARENT"


def _candidate(name: str) -> DepartmentCandidate:
    return DepartmentCandidate(
        canonical=name,
        subgroup=None,
        facility_count=1,
        sources=(),
    )


TABLE = SymptomTable(
    {
        "腹痛": SymptomEntry(
            term="腹痛",
            candidates=tuple(
                _candidate(name) for name in ("內科", "婦產科", "外科", "家醫科")
            ),
        ),
        "頭痛": SymptomEntry(
            term="頭痛",
            candidates=(_candidate("神經內科"), _candidate("家醫科")),
        ),
        "嘔吐": SymptomEntry(
            term="嘔吐",
            candidates=(_candidate("兒科"),),
        ),
    }
)


class RecordingResolver:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def resolve(self, text: str) -> str | None:
        self.calls.append(text)
        if "肚子痛" in text:
            return "腹痛"
        if "頭痛" in text:
            return "頭痛"
        if "嘔吐" in text:
            return "嘔吐"
        return None


class FakeTrees:
    def __init__(self, members: tuple[FamilyMember, ...]) -> None:
        now = datetime.now(timezone.utc)
        self.tree = FamilyTree(
            user_id=OPERATOR,
            family_members=list(members),
            created_at=now,
            updated_at=now,
        )
        self.calls: list[str] = []

    async def get_by_user_id(self, user_id: str) -> FamilyTree:
        self.calls.append(user_id)
        return self.tree


class FakeAuthorization:
    def __init__(self, denied_ids: set[str] | None = None) -> None:
        self.denied_ids = denied_ids or set()
        self.calls: list[tuple] = []

    async def authorize(self, *args, **kwargs) -> str:
        self.calls.append((args, kwargs))
        if args[1] in self.denied_ids:
            raise HTTPException(status_code=403, detail="forbidden")
        return "GUARDIAN"


class FakeProfiles:
    def __init__(self) -> None:
        self.values = {
            OPERATOR: {"age": 40, "gender": "male"},
            SPOUSE: {"age": 35, "gender": "female"},
            CHILD: {},
            PARENT: {"age": 68, "gender": "female"},
        }
        self.calls: list[str] = []

    async def get_user_profile(self, user_id: str) -> dict | None:
        self.calls.append(user_id)
        return self.values.get(user_id)


def _members() -> tuple[FamilyMember, ...]:
    return (
        FamilyMember(
            user_id=SPOUSE,
            display_name="王美玲",
            relationship_type="spouse",
        ),
        FamilyMember(
            user_id=CHILD,
            display_name="王小明",
            relationship_type="child",
        ),
        FamilyMember(
            user_id=PARENT,
            display_name="林秀英",
            relationship_type="parent",
        ),
    )


@pytest.fixture
def pipeline():
    original_department = symptom_tools._symptom_department_service
    original_context = symptom_tools._patient_context_service
    trees = FakeTrees(_members())
    authorization = FakeAuthorization()
    profiles = FakeProfiles()
    resolver = RecordingResolver()
    contexts = PatientContextService(
        family_tree_repository=trees,
        authorization_service=authorization,
        user_profile_service=profiles,
    )
    departments = SymptomDepartmentService(table=TABLE, normalizer=resolver)
    configure_symptom_tool(departments, contexts)
    token = set_line_user_id(OPERATOR)
    try:
        yield trees, authorization, profiles, resolver
    finally:
        reset_line_user_id(token)
        configure_symptom_tool(original_department, original_context)


async def _invoke(*cases: dict) -> str:
    return await suggest_department_for_symptom.ainvoke({"cases": list(cases)})


def _texts(value) -> list[str]:
    if isinstance(value, dict):
        found = [value["text"]] if value.get("type") == "text" else []
        for child in value.values():
            found.extend(_texts(child))
        return found
    if isinstance(value, list):
        return [text for child in value for text in _texts(child)]
    return []


def _card_text(reply: str) -> str:
    return "\n".join(_texts(json.loads(reply)))


@pytest.mark.asyncio
async def test_self_abdominal_pain_uses_operator_profile(pipeline):
    _trees, authorization, profiles, _resolver = pipeline

    text = _card_text(await _invoke({"symptom": "我肚子痛"}))

    assert "內科" in text
    assert "婦產科" not in text
    assert authorization.calls == []
    assert profiles.calls == [OPERATOR]


@pytest.mark.asyncio
async def test_spouse_abdominal_pain_uses_authorized_spouse_profile(pipeline):
    _trees, authorization, profiles, _resolver = pipeline

    text = _card_text(
        await _invoke({"symptom": "肚子痛", "relationship": "spouse"})
    )

    assert "婦產科" in text
    assert authorization.calls[0][0][1] == SPOUSE
    assert profiles.calls == [SPOUSE]


@pytest.mark.asyncio
async def test_spouse_childbirth_keeps_obstetrics_without_a_table_match(pipeline):
    _trees, _authorization, profiles, _resolver = pipeline

    text = _card_text(
        await _invoke(
            {"symptom": "要生小孩了", "relationship": "spouse"}
        )
    )

    assert "婦產科" in text
    assert profiles.calls == [SPOUSE]


@pytest.mark.asyncio
async def test_child_relationship_does_not_invent_an_age_or_pediatrics(pipeline):
    _trees, _authorization, profiles, _resolver = pipeline

    text = _card_text(
        await _invoke({"symptom": "嘔吐", "relationship": "child"})
    )

    assert "1. 兒科" not in text
    assert "家醫科" in text
    assert profiles.calls == [CHILD]


@pytest.mark.asyncio
async def test_multiple_people_with_different_ages_stop_before_all_lookups(pipeline):
    trees, authorization, profiles, resolver = pipeline

    reply = await _invoke(
        {"symptom": "嘔吐", "relationship": "child", "age": 5},
        {"symptom": "頭痛", "person": "林秀英", "age": 68},
    )

    assert reply.startswith("這則訊息提到多位需要看診的人")
    assert trees.calls == []
    assert authorization.calls == []
    assert profiles.calls == []
    assert resolver.calls == []


@pytest.mark.asyncio
async def test_conflicting_name_and_relationship_stops_before_symptom_lookup(pipeline):
    _trees, authorization, profiles, resolver = pipeline

    reply = await _invoke(
        {"symptom": "頭痛", "person": "林秀英", "relationship": "child"}
    )

    assert "林秀英" in reply
    assert "稱謂不一致" in reply
    assert authorization.calls == []
    assert profiles.calls == []
    assert resolver.calls == []


@pytest.mark.asyncio
async def test_unlinked_person_uses_only_current_message_values(pipeline):
    _trees, authorization, profiles, _resolver = pipeline

    text = _card_text(
        await _invoke({"symptom": "頭痛", "person": "隔壁阿伯", "age": 70})
    )

    assert "神經內科" in text
    assert authorization.calls == []
    assert profiles.calls == []


@pytest.mark.asyncio
async def test_denied_spouse_profile_is_not_read(pipeline):
    trees, _authorization, profiles, resolver = pipeline
    denied = FakeAuthorization({SPOUSE})
    contexts = PatientContextService(
        family_tree_repository=trees,
        authorization_service=denied,
        user_profile_service=profiles,
    )
    configure_symptom_tool(
        SymptomDepartmentService(table=TABLE, normalizer=resolver),
        contexts,
    )

    text = _card_text(
        await _invoke({"symptom": "肚子痛", "relationship": "spouse"})
    )

    assert "婦產科" in text
    assert denied.calls[0][0][1] == SPOUSE
    assert profiles.calls == []


@pytest.mark.asyncio
async def test_structured_pronoun_continuation_stays_on_the_spouse(pipeline):
    _trees, authorization, profiles, _resolver = pipeline

    await _invoke({"symptom": "肚子痛", "relationship": "spouse"})
    text = _card_text(
        await _invoke({"symptom": "她還有頭痛", "relationship": "spouse"})
    )

    assert "神經內科" in text
    assert [call[0][1] for call in authorization.calls] == [SPOUSE, SPOUSE]
    assert profiles.calls == [SPOUSE, SPOUSE]


@pytest.mark.asyncio
async def test_explicit_first_person_resets_after_a_spouse_question(pipeline):
    trees, authorization, profiles, _resolver = pipeline

    await _invoke({"symptom": "肚子痛", "relationship": "spouse"})
    text = _card_text(await _invoke({"symptom": "換我頭痛了"}))

    assert "神經內科" in text
    assert [call[0][1] for call in authorization.calls] == [SPOUSE]
    assert profiles.calls == [SPOUSE, OPERATOR]
    assert trees.calls == [OPERATOR]
