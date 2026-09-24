"""
症狀科別建議的驗收檢核清單。

對應 openspec/changes/symptom-department-guidance/acceptance.md 的 H 節：每個測試
名稱以檢核編號開頭，對應清單的一列。

期望值從 spec 與原始對照表推導，在實作之前寫定。實作期間 SHALL NOT 為了讓測試
通過而修改期望值；認為期望值有誤時，先回報再由人決定。

實作前（2026-09-11）先在舊程式上跑過一次：清單標「紅」的項目必須失敗，而且失敗
原因要與清單一致；標「綠」的是守既有行為的迴歸項，實作前後都必須通過。
"""

import dataclasses
import json
import logging
import re

import pytest
from linebot.v3.messaging import FlexContainer

from app.services.family.patient_context import PatientContext
from app.services.medical.department_matcher import CANONICAL_DEPARTMENTS
from app.services.medical.symptom_classification.symptom_department_service import (
    RESULT_FALLBACK,
    RESULT_SUGGESTION,
    SymptomDepartmentService,
    SymptomTriageResult,
)
from app.services.medical.symptom_classification.symptom_table import (
    DEFAULT_TABLE_PATH,
    DepartmentCandidate,
    SourceReference,
    SymptomEntry,
    SymptomTableError,
    load_source_references,
    load_symptom_table,
)
from resources.flex_messages.medical_messages.symptom_department_flex_message import (
    build_symptom_department_flex,
)

# spec「對照表載入時強制轉為部定專科並驗證來源」的欄位表。
ALLOWED_SYMPTOM_FIELDS = {"term", "sources", "subgroup", "kind", "note", "downgraded_from"}

# design 決策 6 的保底科別與順序。刻意寫字面值，不從程式常數推導——從常數推導
# 的話，常數改錯了測試照樣會過。
EXPECTED_FALLBACK = ["家醫科", "內科", "不分科"]

ONLY_PEDIATRIC_REASON = "這個症狀在對照表中只列了兒科"

# 流鼻水、流鼻血曾在此列，MMH_TP 併入後耳鼻喉科有了來源醫院佐證，不再屬於撤回的補列。
WITHDRAWN_TERMS = ("痰多", "帶狀皰疹（皮蛇）")

# acceptance C1：維護用語與寫死的家數都不得出現在卡片上。
FORBIDDEN_ON_CARD = (
    "註：",
    "補列",
    "人工改寫",
    "原註記",
    "SHALL",
    "downgrade_rule",
    "cos(",
    "爬蟲",
    "三家",
    "都這樣分類",
)

ANNOTATION_PATTERN = re.compile(
    r"（(?:僅 1 家醫院的對照表收錄此症狀，建議先去電確認"
    r"|收錄此症狀的 \d+ 家醫院中，有 \d+ 家列在此科(?:，建議先去電確認)?)）"
)


def expected_annotation(n: int, m: int) -> str:
    """spec「候選標註與參考來源」的三種句型，逐字照抄。"""
    if n == 1:
        return "（僅 1 家醫院的對照表收錄此症狀，建議先去電確認）"
    if m == 1:
        return f"（收錄此症狀的 {n} 家醫院中，有 1 家列在此科，建議先去電確認）"
    return f"（收錄此症狀的 {n} 家醫院中，有 {m} 家列在此科）"


# ---------------------------------------------------------------- 共用工具


@pytest.fixture(scope="module")
def table():
    return load_symptom_table()


class FixedResolver:
    """不論輸入為何都回傳指定條目。本檔驗的是查表之後的流程，不驗口語比對。"""

    def __init__(self, term: str | None) -> None:
        self._term = term

    async def resolve(self, text: str) -> str | None:
        return self._term


async def _suggest(
    table,
    term,
    age,
    text="要看哪一科",
    *,
    gender="unknown",
    requested_department="",
):
    service = SymptomDepartmentService(table=table, normalizer=FixedResolver(term))
    context = PatientContext(
        operator_id="U_OPERATOR",
        patient_kind="self",
        patient_id="U_OPERATOR",
        age=age,
        gender=gender,
    )
    return await service.suggest(
        text,
        patient_context=context,
        requested_department=requested_department,
    )


@pytest.mark.asyncio
async def test_patient_context_stays_bound_to_the_triage_result(table):
    context = PatientContext(
        operator_id="U_OPERATOR",
        patient_kind="member",
        patient_id="U_MEMBER",
        display_label="王美玲",
        relationship="spouse",
    )
    service = SymptomDepartmentService(
        table=table,
        normalizer=FixedResolver("頭痛"),
    )

    result = await service.suggest("頭痛", patient_context=context)

    assert result.user_input == "頭痛"
    assert result.patient_context is context


_REFERENCES = tuple(
    SourceReference(code=code, name=f"{code} 醫院", url=f"https://example.com/{code}")
    for code in ("TPVGH_YL", "NCKUH_TN", "NTUH_YL", "TPVGH_HC", "CTH_XD", "AFGH_KH", "AFGH_TY", "CMUH_HC", "MMH_TP", "TZUCHI_HL", "A", "B", "D", "E")
)


def _card(result, references=_REFERENCES, font_size="large") -> dict:
    return build_symptom_department_flex(
        result, references=references, font_size=font_size
    )["contents"]


def _texts(node) -> list[str]:
    if isinstance(node, dict):
        found = [node["text"]] if node.get("type") == "text" else []
        for value in node.values():
            found.extend(_texts(value))
        return found
    if isinstance(node, list):
        return [text for item in node for text in _texts(item)]
    return []


def _reasons(card) -> list[str]:
    return [text for text in _texts(card) if text.startswith("理由：")]


def _cited_codes(card) -> list[str]:
    """參考來源區塊逐條列出的醫院代碼，依卡片上的順序。"""
    codes: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            action = node.get("action")
            if isinstance(action, dict) and action.get("type") == "uri":
                codes.append(action["uri"].rsplit("/", 1)[-1])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(card)
    return codes


# ---------------------------------------------------------------- 載入檢查


def _valid_table() -> dict:
    """每一項檢查都合格的最小對照表，白名單內的欄位全部出現。

    載入失敗的測試一律從這張表只改一處，確保失敗原因就是那一處。
    """
    return {
        "status": "verified",
        "sources": {
            "TPVGH_YL": {"name": "甲醫院", "url": "https://example.com/TPVGH_YL"},
            "NCKUH_TN": {"name": "乙醫院", "url": "https://example.com/NCKUH_TN"},
        },
        "departments": [
            {
                "canonical": "內科",
                "db_facility_count": 100,
                "symptoms": [
                    {
                        "term": "咳嗽",
                        "kind": "symptom",
                        "subgroup": "胸腔內科",
                        "sources": ["TPVGH_YL", "NCKUH_TN"],
                        "note": "維護紀錄",
                        "downgraded_from": "胸腔科",
                    }
                ],
            },
            {
                "canonical": "兒科",
                "db_facility_count": 50,
                "symptoms": [
                    {"term": "咳嗽", "kind": "symptom", "subgroup": None, "sources": ["TPVGH_YL"]}
                ],
            },
        ],
    }


def _load(tmp_path, data):
    path = tmp_path / "table.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return load_symptom_table(path)


def _first_symptom(data) -> dict:
    return data["departments"][0]["symptoms"][0]


def test_T00_minimal_valid_table_loads(tmp_path):
    loaded = _load(tmp_path, _valid_table())
    assert [c.canonical for c in loaded.lookup("咳嗽").candidates] == ["內科", "兒科"]


def test_T01_empty_sources_rejected(tmp_path):
    data = _valid_table()
    _first_symptom(data)["sources"] = []
    with pytest.raises(SymptomTableError, match="sources 為空"):
        _load(tmp_path, data)


def test_T02_unregistered_source_code_rejected(tmp_path):
    data = _valid_table()
    _first_symptom(data)["sources"] = ["TPVGH_YL", "X"]
    with pytest.raises(SymptomTableError, match="未登記的來源代碼"):
        _load(tmp_path, data)


@pytest.mark.parametrize(
    ("field", "value"), [("rank", 1), ("origin", "project"), ("priority", 1)]
)
def test_T03_field_outside_whitelist_rejected(tmp_path, field, value):
    data = _valid_table()
    _first_symptom(data)[field] = value
    with pytest.raises(SymptomTableError, match="不允許的欄位"):
        _load(tmp_path, data)


def test_T04_duplicate_term_within_a_department_rejected(tmp_path):
    data = _valid_table()
    data["departments"][0]["symptoms"].append(
        {"term": "咳嗽", "kind": "symptom", "subgroup": None, "sources": ["NCKUH_TN"]}
    )
    with pytest.raises(SymptomTableError, match="重複的症狀與科別"):
        _load(tmp_path, data)


def test_T04_duplicate_term_across_blocks_of_the_same_department_rejected(tmp_path):
    """兩個科別區塊解析為同一部定專科時最容易踩到——正是併入新醫院的情境。"""
    data = _valid_table()
    data["departments"].append(
        {
            "canonical": "內科",
            "db_facility_count": 100,
            "symptoms": [
                {"term": "咳嗽", "kind": "symptom", "subgroup": None, "sources": ["NCKUH_TN"]}
            ],
        }
    )
    with pytest.raises(SymptomTableError, match="重複的症狀與科別"):
        _load(tmp_path, data)


def test_T05_duplicate_source_code_rejected(tmp_path):
    data = _valid_table()
    _first_symptom(data)["sources"] = ["TPVGH_YL", "TPVGH_YL"]
    with pytest.raises(SymptomTableError, match="來源代碼重複"):
        _load(tmp_path, data)


@pytest.mark.parametrize("term", ["", "   "])
def test_T06_blank_term_rejected(tmp_path, term):
    data = _valid_table()
    _first_symptom(data)["term"] = term
    with pytest.raises(SymptomTableError, match="term 為空"):
        _load(tmp_path, data)


_MISSING = object()


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(_MISSING, id="missing"),
        pytest.param(None, id="null"),
        pytest.param(0, id="zero"),
        pytest.param(-3, id="negative"),
        pytest.param("100", id="string"),
        pytest.param(1.5, id="float"),
        pytest.param(True, id="bool"),
    ],
)
def test_T07_facility_count_must_be_a_positive_integer(tmp_path, value):
    data = _valid_table()
    block = data["departments"][0]
    if value is _MISSING:
        del block["db_facility_count"]
    else:
        block["db_facility_count"] = value
    with pytest.raises(SymptomTableError, match="db_facility_count"):
        _load(tmp_path, data)


def test_T08_unverified_table_loads_with_a_warning(tmp_path, caplog):
    data = _valid_table()
    data["status"] = "unverified"
    with caplog.at_level(logging.WARNING):
        _load(tmp_path, data)
    assert "非 verified" in caplog.text


def test_T09_data_structures_carry_no_manual_fields():
    assert {f.name for f in dataclasses.fields(DepartmentCandidate)} == {
        "canonical",
        "subgroup",
        "facility_count",
        "sources",
    }
    assert {f.name for f in dataclasses.fields(SymptomEntry)} == {"term", "candidates"}


# ---------------------------------------------------------------- 正式表與排序


def test_T10_real_table_holds_only_source_backed_entries(table):
    raw = json.loads(DEFAULT_TABLE_PATH.read_text(encoding="utf-8"))
    for block in raw["departments"]:
        for symptom in block["symptoms"]:
            extra = set(symptom) - ALLOWED_SYMPTOM_FIELDS
            assert not extra, (block["canonical"], symptom["term"], extra)
    for term in table.terms:
        for candidate in table.lookup(term).candidates:
            assert candidate.sources, (term, candidate.canonical)
    for term in WITHDRAWN_TERMS:
        assert table.lookup(term) is None, term


def test_T11_candidates_sorted_by_source_count_then_facility_count(table):
    for term in table.terms:
        keys = [
            (-len(c.sources), -c.facility_count) for c in table.lookup(term).candidates
        ]
        assert keys == sorted(keys), term


@pytest.mark.parametrize(
    ("term", "expected"),
    [
        ("坐骨神經痛", ["神經外科", "復健科", "骨科", "神經科", "麻醉科"]),
        ("性病", ["皮膚科", "泌尿科", "內科", "家醫科", "婦產科"]),
    ],
)
def test_T12_order_follows_sources_not_manual_rank(table, term, expected):
    assert [c.canonical for c in table.lookup(term).candidates] == expected


# ---------------------------------------------------------------- 保底與年齡


@pytest.mark.asyncio
async def test_T14_fallback_departments(table):
    result = await _suggest(table, None, 40)
    assert result.kind == RESULT_FALLBACK
    assert [c.canonical for c in result.candidates] == EXPECTED_FALLBACK
    assert set(EXPECTED_FALLBACK) <= CANONICAL_DEPARTMENTS


@pytest.mark.asyncio
@pytest.mark.parametrize("term", ["生長發育遲緩"])
async def test_T15_adult_gets_fallback_when_only_pediatrics_lists_it(table, term):
    result = await _suggest(table, term, 40)
    assert result.kind == RESULT_FALLBACK
    assert result.matched_term == term
    assert result.fallback_reason == ONLY_PEDIATRIC_REASON
    assert [c.canonical for c in result.candidates] == EXPECTED_FALLBACK


@pytest.mark.asyncio
async def test_T16_child_asking_about_vomiting_gets_pediatrics_only(table):
    result = await _suggest(table, "生長發育遲緩", 8)
    assert result.kind == RESULT_SUGGESTION
    assert [c.canonical for c in result.candidates] == ["兒科"]


@pytest.mark.asyncio
async def test_T17_fourteen_is_a_child(table):
    result = await _suggest(table, "生長發育遲緩", 14)
    assert result.kind == RESULT_SUGGESTION
    assert [c.canonical for c in result.candidates] == ["兒科"]


@pytest.mark.asyncio
async def test_T17_fifteen_is_an_adult(table):
    """兒科區塊的 age_note：滿 15 歲者改對應成人科別。"""
    result = await _suggest(table, "生長發育遲緩", 15)
    assert result.kind == RESULT_FALLBACK


@pytest.mark.asyncio
@pytest.mark.parametrize("age", [None, -1, 131, "12", True, 12.5])
async def test_T18_unknown_or_invalid_age_is_treated_as_adult(table, age):
    result = await _suggest(table, "生長發育遲緩", age)
    assert result.kind == RESULT_FALLBACK


@pytest.mark.asyncio
async def test_T18_parent_mentioning_a_child_keeps_pediatrics(table):
    result = await _suggest(table, "生長發育遲緩", None, text="我家寶寶發育比較慢要看哪一科")
    assert result.kind == RESULT_SUGGESTION
    assert [c.canonical for c in result.candidates] == ["兒科"]


@pytest.mark.asyncio
async def test_T18_speaker_age_does_not_override_adult_patient_context(table):
    result = await _suggest(table, "生長發育遲緩", 40)

    assert result.kind == RESULT_FALLBACK
    assert [c.canonical for c in result.candidates] == EXPECTED_FALLBACK


@pytest.mark.asyncio
async def test_T18_patient_age_is_used_even_when_speaker_age_is_adult(table):
    result = await _suggest(table, "生長發育遲緩", 5)

    assert result.kind == RESULT_SUGGESTION
    assert [c.canonical for c in result.candidates] == ["兒科"]


@pytest.mark.asyncio
async def test_T18_child_relationship_does_not_imply_pediatric_age(table):
    service = SymptomDepartmentService(
        table=table,
        normalizer=FixedResolver("生長發育遲緩"),
    )
    context = PatientContext(
        operator_id="U_OPERATOR",
        patient_kind="member",
        patient_id="U_CHILD",
        display_label="王大明",
        relationship="child",
    )

    result = await service.suggest(
        "我兒子發育比較慢要看哪一科",
        patient_context=context,
    )

    assert result.kind == RESULT_FALLBACK
    assert [c.canonical for c in result.candidates] == EXPECTED_FALLBACK
    assert result.pediatric_reason is None


# ---------------------------------------------------------------- 看診者性別適用性（task 10.9）


@pytest.mark.asyncio
async def test_task_10_9_male_patient_general_abdominal_pain_hides_obstetrics(table):
    result = await _suggest(table, "腹痛", 40, text="我肚子痛要看哪科", gender="male")

    assert result.kind == RESULT_SUGGESTION
    assert [candidate.canonical for candidate in result.candidates] == [
        "內科",
        "外科",
        "家醫科",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("gender", ["female", "unknown"])
async def test_task_10_9_female_or_unknown_gender_keeps_obstetrics(table, gender):
    result = await _suggest(table, "腹痛", 40, gender=gender)

    assert result.kind == RESULT_SUGGESTION
    assert [candidate.canonical for candidate in result.candidates] == [
        "內科",
        "婦產科",
        "外科",
        "家醫科",
    ]


@pytest.mark.asyncio
async def test_task_10_9_spouse_does_not_inherit_male_speakers_gender(table):
    service = SymptomDepartmentService(
        table=table,
        normalizer=FixedResolver("腹痛"),
    )
    spouse = PatientContext(
        operator_id="U_MALE_OPERATOR",
        patient_kind="member",
        patient_id="U_SPOUSE",
        display_label="王美玲",
        relationship="spouse",
        gender="unknown",
    )

    result = await service.suggest(
        "我老婆肚子痛要看哪科",
        patient_context=spouse,
    )

    assert "婦產科" in [candidate.canonical for candidate in result.candidates]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "我懷孕了而且肚子痛要看哪科",
        "我月經期間肚子痛要看哪科",
        "I am pregnant and have abdominal pain. Which department?",
    ],
)
async def test_task_10_9_explicit_reproductive_context_overrides_male_profile(
    table, text
):
    result = await _suggest(table, "腹痛", 40, text=text, gender="male")

    assert "婦產科" in [candidate.canonical for candidate in result.candidates]


@pytest.mark.asyncio
async def test_task_10_9_explicit_obstetrics_request_overrides_male_profile(table):
    result = await _suggest(
        table,
        "腹痛",
        40,
        text="我肚子痛可以看婦產科嗎",
        gender="male",
        requested_department="婦產科",
    )

    assert "婦產科" in [candidate.canonical for candidate in result.candidates]


@pytest.mark.asyncio
async def test_task_10_9_childbirth_context_adds_obstetrics_to_fallback(table):
    result = await _suggest(
        table,
        None,
        35,
        text="我老婆要生小孩了要看哪科",
        gender="unknown",
    )

    assert result.kind == RESULT_FALLBACK
    assert [candidate.canonical for candidate in result.candidates] == [
        "婦產科",
        *EXPECTED_FALLBACK,
    ]


@pytest.mark.asyncio
async def test_task_10_9_explicit_obstetrics_request_adds_it_to_unknown_fallback(table):
    result = await _suggest(
        table,
        None,
        40,
        text="我可以看婦產科嗎",
        gender="male",
        requested_department="Obstetrics & Gynecology",
    )

    assert result.kind == RESULT_FALLBACK
    assert [candidate.canonical for candidate in result.candidates] == [
        "婦產科",
        *EXPECTED_FALLBACK,
    ]


@pytest.mark.asyncio
async def test_task_10_9_reproductive_matched_term_overrides_male_profile(table):
    result = await _suggest(table, "月經失調", 40, gender="male")

    assert [candidate.canonical for candidate in result.candidates] == ["婦產科"]


@pytest.mark.asyncio
async def test_task_10_9_male_general_obstetrics_only_match_uses_neutral_fallback(table):
    result = await _suggest(table, "下腹痛", 40, gender="male")

    assert result.kind == RESULT_FALLBACK
    assert [candidate.canonical for candidate in result.candidates] == EXPECTED_FALLBACK
    assert result.fallback_reason == "依看診者資料，沒有適合預設顯示的特定科別"


# ---------------------------------------------------------------- 候選數上限

_FIVE = ["內科", "外科", "婦產科", "泌尿科", "皮膚科"]


def _table_with_candidates(tmp_path, departments):
    """每一科各列同一個症狀、同一家來源；院所數遞減，順序因此固定。"""
    data = {
        "status": "verified",
        "sources": {"TPVGH_YL": {"name": "甲醫院", "url": "https://example.com/TPVGH_YL"}},
        "departments": [
            {
                "canonical": name,
                "db_facility_count": 1000 - index,
                "symptoms": [
                    {
                        "term": "多科症狀",
                        "kind": "symptom",
                        "subgroup": None,
                        "sources": ["TPVGH_YL"],
                    }
                ],
            }
            for index, name in enumerate(departments)
        ],
    }
    return _load(tmp_path, data)


@pytest.mark.asyncio
async def test_T19_five_candidates_are_all_suggested(tmp_path):
    result = await _suggest(_table_with_candidates(tmp_path, _FIVE), "多科症狀", 40)
    assert result.kind == RESULT_SUGGESTION
    assert [c.canonical for c in result.candidates] == _FIVE


@pytest.mark.asyncio
async def test_T19_headache_no_longer_falls_back(table):
    """精神科的泛稱頭痛不收後，成人過濾兒科即可得到四個有效候選。"""
    result = await _suggest(table, "頭痛", 40)
    assert result.kind == RESULT_SUGGESTION
    assert [c.canonical for c in result.candidates] == [
        "神經科",
        "內科",
        "中醫一般科",
        "家醫科",
    ]


@pytest.mark.asyncio
async def test_T19_six_candidates_fall_back(tmp_path):
    loaded = _table_with_candidates(tmp_path, [*_FIVE, "骨科"])
    result = await _suggest(loaded, "多科症狀", 40)
    assert result.kind == RESULT_FALLBACK


@pytest.mark.asyncio
async def test_T19_cap_counts_pediatrics_before_filtering(tmp_path):
    """六個候選含兒科：成人只會看到五個，但上限以過濾前計算，仍走保底。"""
    loaded = _table_with_candidates(tmp_path, [*_FIVE, "兒科"])
    result = await _suggest(loaded, "多科症狀", 40)
    assert result.kind == RESULT_FALLBACK


# ---------------------------------------------------------------- 卡片標註


def _single(sources, term_sources):
    return SymptomTriageResult(
        kind=RESULT_SUGGESTION,
        user_input="要看哪一科",
        matched_term="咳嗽",
        candidates=(DepartmentCandidate("內科", None, 100, sources),),
        term_sources=term_sources,
    )


def _only_reason(card) -> str:
    reasons = _reasons(card)
    assert len(reasons) == 1
    assert len(ANNOTATION_PATTERN.findall(reasons[0])) == 1, reasons[0]
    return reasons[0]


def test_T20_annotation_when_a_single_hospital_lists_the_symptom():
    reason = _only_reason(_card(_single(("NTUH_YL",), ("NTUH_YL",))))
    assert reason.endswith("（僅 1 家醫院的對照表收錄此症狀，建議先去電確認）")


def test_T21_annotation_when_one_of_several_hospitals_lists_this_department():
    reason = _only_reason(_card(_single(("TPVGH_YL",), ("TPVGH_YL", "NCKUH_TN", "NTUH_YL"))))
    assert reason.endswith(
        "（收錄此症狀的 3 家醫院中，有 1 家列在此科，建議先去電確認）"
    )


def test_T22_annotation_when_every_hospital_agrees_claims_no_unanimity():
    reason = _only_reason(_card(_single(("TPVGH_YL", "NCKUH_TN", "NTUH_YL"), ("TPVGH_YL", "NCKUH_TN", "NTUH_YL"))))
    assert reason.endswith("（收錄此症狀的 3 家醫院中，有 3 家列在此科）")
    assert "都" not in reason


def test_T23_annotation_when_some_hospitals_list_this_department():
    reason = _only_reason(_card(_single(("NCKUH_TN", "NTUH_YL"), ("TPVGH_YL", "NCKUH_TN", "NTUH_YL"))))
    assert reason.endswith("（收錄此症狀的 3 家醫院中，有 2 家列在此科）")


def test_T24_annotation_scales_beyond_three_hospitals():
    card = _card(_single(("A", "B", "C"), ("A", "B", "C", "D", "E")))
    assert _only_reason(card).endswith("（收錄此症狀的 5 家醫院中，有 3 家列在此科）")
    assert "三家" not in json.dumps(card, ensure_ascii=False)


async def _every_card(table, age):
    """全表每個條目以指定年齡產生的 (條目, 結果, 卡片)。"""
    references = load_source_references()
    cards = []
    for term in table.terms:
        result = await _suggest(table, term, age)
        cards.append((term, result, _card(result, references=references)))
    return cards


# T25 拆成兩個測試：混在一起時，先失敗的那一項會遮住另一項，突變驗證（K3）
# 就無法證明「還原 note 拼接」一定會被抓到。


@pytest.mark.asyncio
@pytest.mark.parametrize("age", [40, 8])
async def test_T25a_no_maintenance_text_on_any_card(table, age):
    for term, _, card in await _every_card(table, age):
        payload = json.dumps(card, ensure_ascii=False)
        for forbidden in FORBIDDEN_ON_CARD:
            assert forbidden not in payload, (term, age, forbidden)


@pytest.mark.asyncio
@pytest.mark.parametrize("age", [40, 8])
async def test_T25b_every_candidate_ends_with_exactly_one_annotation(table, age):
    for term, result, card in await _every_card(table, age):
        if result.kind != RESULT_SUGGESTION:
            continue
        reasons = _reasons(card)
        assert len(reasons) == len(result.candidates), term
        for reason in reasons:
            matches = list(ANNOTATION_PATTERN.finditer(reason))
            assert len(matches) == 1, (term, reason)
            assert matches[0].end() == len(reason), (term, reason)


@pytest.mark.asyncio
async def test_T26_fallback_card_has_no_annotation_and_no_sources(table):
    card = _card(await _suggest(table, None, 40))
    assert not ANNOTATION_PATTERN.search(json.dumps(card, ensure_ascii=False))
    assert _cited_codes(card) == []


@pytest.mark.parametrize("font_size", ["normal", "large", "xlarge"])
def test_T27_largest_card_passes_line_validation(font_size):
    result = SymptomTriageResult(
        kind=RESULT_SUGGESTION,
        user_input="要看哪一科",
        matched_term="長期難以緩解的疼痛",
        candidates=tuple(
            DepartmentCandidate(name, ("新陳代謝內分泌科", "心臟內科"), 100, ("A",))
            for name in ("職業醫學科", "放射腫瘤科", "中醫一般科", "耳鼻喉科", "整形外科")
        ),
        term_sources=("A", "B", "C", "D", "E"),
    )
    card = _card(result, font_size=font_size)
    FlexContainer.from_json(json.dumps(card, ensure_ascii=False))


# ---------------------------------------------------------------- D 節行為案例

# (編號, 條目, 年齡, N, [(科別, 次專科, M)], 參考來源)
# 預期值由原始 JSON 直接推導（撤回補列與 rank 後依來源家數、院所數排序），
# 不經過服務程式。
_SUGGESTION_CASES = [
    ("D1", "咳嗽", 40, 6, [("內科", ("胸腔內科",), 4), ("中醫一般科", (), 1), ("家醫科", (), 1), ("耳鼻喉科", (), 1)], ["NCKUH_TN", "NTUH_YL", "CTH_XD", "MMH_TP", "TZUCHI_HL"]),
    (
        "D2",
        "咳嗽",
        8,
        6,
        [("內科", ("胸腔內科",), 4), ("中醫一般科", (), 1), ("家醫科", (), 1), ("兒科", (), 1), ("耳鼻喉科", (), 1)],
        ["TPVGH_YL", "NCKUH_TN", "NTUH_YL", "CTH_XD", "MMH_TP", "TZUCHI_HL"],
    ),
    ("D4", "嘔吐", 8, 4, [("內科", ("胃腸肝膽科",), 3), ("家醫科", (), 1), ("兒科", (), 1)], ["TPVGH_YL", "CTH_XD", "AFGH_TY", "MMH_TP"]),
    (
        "D6",
        "坐骨神經痛",
        40,
        9,
        [("神經外科", (), 7), ("復健科", (), 4), ("骨科", (), 3), ("神經科", ("神經內科",), 2), ("麻醉科", ("疼痛科",), 2)],
        ["TPVGH_YL", "NCKUH_TN", "NTUH_YL", "TPVGH_HC", "AFGH_KH", "AFGH_TY", "CMUH_HC", "MMH_TP", "TZUCHI_HL"],
    ),
    (
        "D7",
        "性病",
        40,
        5,
        [("皮膚科", (), 3), ("泌尿科", (), 3), ("內科", ("感染科",), 2), ("家醫科", (), 1), ("婦產科", (), 1)],
        ["NCKUH_TN", "NTUH_YL", "AFGH_KH", "MMH_TP", "TZUCHI_HL"],
    ),
    (
        "D8",
        "感冒",
        40,
        9,
        [("內科", (), 5), ("耳鼻喉科", (), 3), ("家醫科", (), 2), ("中醫一般科", (), 1)],
        ["NCKUH_TN", "NTUH_YL", "TPVGH_HC", "CTH_XD", "AFGH_KH", "AFGH_TY", "CMUH_HC", "MMH_TP", "TZUCHI_HL"],
    ),
    (
        "D9",
        "氣喘",
        40,
        9,
        [("內科", ("胸腔內科",), 6), ("中醫一般科", (), 2)],
        ["NCKUH_TN", "NTUH_YL", "TPVGH_HC", "CTH_XD", "AFGH_KH", "AFGH_TY", "CMUH_HC", "TZUCHI_HL"],
    ),
    ("D10", "高血脂", 40, 3, [("內科", ("新陳代謝及內分泌科", "心臟內科", "腎臟內科"), 3), ("家醫科", (), 1)], ["NCKUH_TN", "NTUH_YL", "CMUH_HC"]),
    (
        "D11",
        "酒癮",
        40,
        8,
        [("精神科", (), 8)],
        ["TPVGH_YL", "NCKUH_TN", "NTUH_YL", "TPVGH_HC", "AFGH_KH", "AFGH_TY", "CMUH_HC", "TZUCHI_HL"],
    ),
    ("D12", "身心障礙者牙科照護", 40, 1, [("牙科", ("特殊需求者牙科",), 1)], ["NTUH_YL"]),
    (
        "D16",
        "腹瀉",
        40,
        7,
        [("內科", ("胃腸肝膽科",), 6), ("外科", ("大腸直腸外科",), 2), ("中醫一般科", (), 1), ("家醫科", (), 1)],
        ["TPVGH_YL", "NCKUH_TN", "CTH_XD", "AFGH_TY", "CMUH_HC", "MMH_TP", "TZUCHI_HL"],
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case_id", "term", "age", "n", "expected", "cited"),
    _SUGGESTION_CASES,
    ids=[case[0] for case in _SUGGESTION_CASES],
)
async def test_T28_acceptance_suggestion_cases(
    table, case_id, term, age, n, expected, cited
):
    result = await _suggest(table, term, age)
    assert result.kind == RESULT_SUGGESTION
    assert [(c.canonical, c.subgroups) for c in result.candidates] == [
        (name, subgroups) for name, subgroups, _ in expected
    ]

    card = _card(result)
    reasons = _reasons(card)
    assert len(reasons) == len(expected)
    for reason, (_, _, m) in zip(reasons, expected):
        assert reason.endswith(expected_annotation(n, m)), reason
    assert _cited_codes(card) == cited
    assert "參考來源" in _texts(card)


@pytest.mark.asyncio
async def test_T28_acceptance_D14_bedwetting_adult_gets_urology(table):
    """尿床原本只有兒科收錄、成人走保底；TZUCHI_HL 的泌尿科也列了尿床，成人改拿泌尿科。"""
    result = await _suggest(table, "尿床", 40)
    assert result.kind == RESULT_SUGGESTION
    assert [c.canonical for c in result.candidates] == ["泌尿科"]
    assert _cited_codes(_card(result)) == ["TZUCHI_HL"]


# ---------------------------------------------------------------- 孩童的保底

# 孩童的保底：兒科在前，其後為 T14 的三科。刻意寫字面值，理由同 EXPECTED_FALLBACK。
EXPECTED_PEDIATRIC_FALLBACK = ["兒科", "家醫科", "內科", "不分科"]

CHILD_NOTE = "因為是幫孩子詢問，另外列出兒科。"
UNDER_AGE_NOTE = "因為看診者未滿 15 歲，另外列出兒科。"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("age", "text", "reason"),
    [
        (8, "要看哪一科", "age"),
        (14, "要看哪一科", "age"),
        (None, "我家寶寶全身不舒服要看哪一科", "mentioned_child"),
        (40, "寶寶全身不舒服要看哪一科", "mentioned_child"),
    ],
)
async def test_T29_child_fallback_lists_pediatrics_first(table, age, text, reason):
    result = await _suggest(table, None, age, text=text)
    assert result.kind == RESULT_FALLBACK
    assert [c.canonical for c in result.candidates] == EXPECTED_PEDIATRIC_FALLBACK
    assert result.pediatric_reason == reason


@pytest.mark.asyncio
@pytest.mark.parametrize("age", [15, 40, None, -1, "12", True])
async def test_T30_non_child_fallback_is_unchanged(table, age):
    result = await _suggest(table, None, age)
    assert result.kind == RESULT_FALLBACK
    assert [c.canonical for c in result.candidates] == EXPECTED_FALLBACK
    assert result.pediatric_reason is None


@pytest.mark.asyncio
async def test_T31_child_with_too_many_candidates_gets_pediatrics(tmp_path):
    loaded = _table_with_candidates(tmp_path, [*_FIVE, "骨科"])
    result = await _suggest(loaded, "多科症狀", 8)
    assert result.kind == RESULT_FALLBACK
    assert [c.canonical for c in result.candidates] == EXPECTED_PEDIATRIC_FALLBACK


@pytest.mark.asyncio
async def test_T32_child_asking_about_another_child_is_worded_for_that_child(table):
    """12 歲使用者問寶寶：要看病的是被提到的孩子，不是使用者本人。"""
    result = await _suggest(table, None, 12, text="我家寶寶全身不舒服要看哪一科")
    assert result.pediatric_reason == "mentioned_child"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("age", "text", "expected_note"),
    [
        (None, "我家寶寶全身不舒服要看哪一科", CHILD_NOTE),
        (8, "要看哪一科", UNDER_AGE_NOTE),
        (40, "要看哪一科", None),
    ],
)
async def test_T33_fallback_card_explains_why_pediatrics_is_listed(
    table, age, text, expected_note
):
    texts = _texts(_card(await _suggest(table, None, age, text=text)))
    for note in (CHILD_NOTE, UNDER_AGE_NOTE):
        shown = any(note in text for text in texts)
        assert shown is (note == expected_note), note


@pytest.mark.parametrize(
    ("term", "expected"),
    [
        ("背痛", ["家醫科", "復健科", "骨科", "神經外科"]),
        ("腰痛", ["家醫科", "復健科", "骨科", "神經外科"]),
    ],
)
def test_T34_context_free_back_pain_does_not_force_specialty(table, term, expected):
    assert [candidate.canonical for candidate in table.lookup(term).candidates] == expected


def test_T35_gum_bleeding_does_not_force_hematology(table):
    assert table.lookup("牙齦出血") is None
    assert [
        candidate.canonical for candidate in table.lookup("刷牙出血").candidates
    ] == ["牙科"]


def test_T18_there_is_no_speaker_age_side_channel():
    """發話者年齡的 ContextVar 已刪除（10.20）：看診者年齡只能經 PatientContext 傳入。"""
    import app.core.user_age as user_age

    for name in ("get_request_age", "set_request_age", "reset_request_age"):
        assert not hasattr(user_age, name), name
