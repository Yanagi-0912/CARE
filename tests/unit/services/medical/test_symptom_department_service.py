import json

import pytest

from app.services.medical.department_matcher import CANONICAL_DEPARTMENTS
from app.services.medical.symptom_classification.normalizer import (
    SymptomNormalizer,
)
from app.services.medical.symptom_classification.symptom_department_service import (
    FALLBACK_DEPARTMENTS,
    RESULT_FALLBACK,
    RESULT_SUGGESTION,
    SymptomDepartmentService,
)
from app.services.medical.symptom_classification.symptom_table import (
    MAX_CANDIDATES,
    SymptomTableError,
    load_symptom_table,
)
from resources.flex_messages.medical_messages.symptom_department_flex_message import (
    build_symptom_department_flex,
)


@pytest.fixture(scope="module")
def table():
    return load_symptom_table()


# 比對層的替身。
#
# 本檔驗的是服務流程——查表、兒科過濾、候選數上限、保底——不是「口語怎麼對到
# 條目」。初版直接靠真實的別名表達成命中，於是規則層一拆，這裡十五個測試全倒，
# 而服務邏輯一行都沒動：測試綁在比對實作上，不是綁在它要驗的性質上。
#
# 換成替身之後，比對層改成向量、或再換成別的做法，都不會再波及這裡。
# 比對層自己的正確性由它自己的測試負責。
_STUB_TERMS: dict[str, str | None] = {
    "我肚子好痛要掛哪一科": "腹痛",
    "我肚子痛，要看哪一科": "腹痛",
    "我肚子痛到站不起來要掛哪一科": "腹痛",
    "我兒子肚子痛要看哪科": "腹痛",
    "拉肚子看哪科": "腹瀉",
    "我牙齒痛要掛什麼科": "牙痛",
    "眼睛乾要掛哪一科": "乾眼症",
    "失眠該看什麼科": "失眠",
    "小孩發燒要掛哪一科": "發燒",
    "寶寶一直咳要掛哪科": "咳嗽",
    "尿床要看哪一科": "尿床",
    "頭痛要掛哪一科": "頭痛",
    # 比對不到的輸入一律回 None，服務層應走保底
    "天空是藍色的要掛哪一科": None,
    "我阿公昏迷要掛哪一科": None,
}


class StubResolver:
    """照 SymptomResolver 的介面回傳預先指定的條目，不做任何比對。"""

    def __init__(self, mapping: dict[str, str | None] | None = None) -> None:
        self._mapping = _STUB_TERMS if mapping is None else mapping

    async def resolve(self, text: str) -> str | None:
        assert text in self._mapping, f"測試未替 {text!r} 指定比對結果"
        return self._mapping[text]


@pytest.fixture
def service(table):
    return SymptomDepartmentService(table=table, normalizer=StubResolver())


# ---------------------------------------------------------------- 對照表載入


def test_every_canonical_is_queryable(table):
    """
    對照表的科別必須全部是資料庫查得到的值，否則會產生「系統說查過了但附近
    沒有」——那比「系統看不懂」更難察覺也更誤導。
    """
    for term in table.terms:
        for candidate in table.lookup(term).candidates:
            assert candidate.canonical in CANONICAL_DEPARTMENTS


def test_load_fails_fast_on_unresolvable_department(tmp_path):
    """表壞掉要在啟動時就炸開，不是等到線上有人問了才回一個查不到的科別。"""
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps(
            {
                "status": "verified",
                "departments": [
                    {
                        "canonical": "15歲以下兒童",
                        "db_facility_count": 0,
                        "symptoms": [{"term": "腹瀉", "kind": "symptom"}],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(SymptomTableError, match="無法解析"):
        load_symptom_table(bad)


def test_load_reports_declared_status(table):
    """
    表是否經人工審定，必須是程式查得到的事實，不能只存在於註解裡——它決定
    這張表能不能用於線上回覆（design 決策 11、tasks 2.5）。

    2026-09-02 由 unverified 改為 verified。這個測試守的是「載入器如實反映
    檔案的宣告」，不是把某個特定值寫死；旗標翻動時應連同 usage_rules 一起改，
    兩邊不一致才是問題。
    """
    import json

    from app.services.medical.symptom_classification.symptom_table import (
        DEFAULT_TABLE_PATH,
    )

    declared = json.loads(DEFAULT_TABLE_PATH.read_text(encoding="utf-8"))["status"]
    assert table.verified is (declared == "verified")
    assert table.verified is True


def test_candidates_sorted_by_cross_source_agreement(table):
    """三家都這樣分類的科別，要排在只有一家的前面。"""
    for term in table.terms:
        counts = [c.source_count for c in table.lookup(term).candidates]
        assert counts == sorted(counts, reverse=True)


# ---------------------------------------------------------------- 正規化


@pytest.mark.asyncio
async def test_normalizer_schema_has_no_department_field(table):
    """
    正規化層 SHALL NOT 產生科別。schema 裡根本沒有那個欄位，因此
    「模型會不會亂推科別」在架構上就不存在，不必靠 prompt 約束。
    """
    normalizer = SymptomNormalizer(table_terms=table.terms)
    schema = normalizer._build_schema()
    assert set(schema["properties"]) == {"symptom"}
    enum_values = set(schema["properties"]["symptom"]["enum"])
    assert not (enum_values & CANONICAL_DEPARTMENTS)


@pytest.mark.asyncio
async def test_normalizer_rejects_value_outside_enum(table):
    """enum 的強制力取決於模型與 SDK，放行清單外的值會讓後續查表靜默落空。"""

    async def invoke(_prompt):
        return {"symptom": "內科"}

    normalizer = SymptomNormalizer(table_terms=table.terms, invoke=invoke)
    assert await normalizer.resolve("某種沒人聽過的怪症狀") is None


@pytest.mark.asyncio
async def test_normalizer_falls_back_to_none_on_error(table):
    async def invoke(_prompt):
        raise RuntimeError("LLM 掛了")

    normalizer = SymptomNormalizer(table_terms=table.terms, invoke=invoke)
    assert await normalizer.resolve("某種沒人聽過的怪症狀") is None


# ---------------------------------------------------------------- 服務流程


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected_term", "expected_first"),
    [
        ("我肚子好痛要掛哪一科", "腹痛", "內科"),
        ("拉肚子看哪科", "腹瀉", "內科"),
        ("我牙齒痛要掛什麼科", "牙痛", "牙科"),
        ("眼睛乾要掛哪一科", "乾眼症", "眼科"),
        ("失眠該看什麼科", "失眠", "精神科"),
    ],
)
async def test_suggestion_happy_path(service, text, expected_term, expected_first):
    result = await service.suggest(text)
    assert result.kind == RESULT_SUGGESTION
    assert result.matched_term == expected_term
    assert result.primary_department == expected_first
    assert 1 <= len(result.candidates) <= MAX_CANDIDATES


@pytest.mark.asyncio
async def test_unknown_symptom_falls_back(service):
    result = await service.suggest("天空是藍色的要掛哪一科")
    assert result.kind == RESULT_FALLBACK
    assert [c.canonical for c in result.candidates] == list(FALLBACK_DEPARTMENTS)


@pytest.mark.asyncio
async def test_too_many_candidates_falls_back_instead_of_guessing(table):
    """
    候選過多代表這個症狀本來就跨科，硬挑三個等於把不確定性藏起來。
    SHALL NOT 退化為「取表中最接近的幾條」。
    """

    class BroadNormalizer:
        async def resolve(self, text):
            return "多科症狀"

    from app.services.medical.symptom_classification.symptom_table import (
        DepartmentCandidate,
        SymptomEntry,
        SymptomTable,
    )

    entry = SymptomEntry(
        term="多科症狀",
        kind="symptom",
        candidates=tuple(
            DepartmentCandidate(
                canonical=name, subgroup=None, facility_count=1, source_count=1
            )
            for name in ("內科", "外科", "婦產科", "泌尿科")
        ),
    )
    service = SymptomDepartmentService(
        table=SymptomTable({"多科症狀": entry}, verified=True),
        normalizer=BroadNormalizer(),
    )

    result = await service.suggest("那個症狀要掛哪一科")
    assert result.kind == RESULT_FALLBACK
    assert result.matched_term == "多科症狀"
    assert [c.canonical for c in result.candidates] == list(FALLBACK_DEPARTMENTS)


@pytest.mark.asyncio
async def test_fallback_departments_are_queryable():
    for name in FALLBACK_DEPARTMENTS:
        assert name in CANONICAL_DEPARTMENTS


# ---------------------------------------------------------------- Flex 呈現


@pytest.mark.asyncio
async def test_suggestion_flex_carries_disclaimer(service):
    result = await service.suggest("我肚子好痛要掛哪一科")
    payload = json.dumps(build_symptom_department_flex(result), ensure_ascii=False)
    assert "不是醫療診斷" in payload
    assert "儘速就醫" in payload
    assert "參考來源" in payload


# ---------------------------------------------------------------- 與 agent 的互動


def test_symptom_suggestion_suppresses_forced_rag():
    """
    科別建議卡已經是完整回覆，再強制跑一次 RAG 只會多花十幾秒；更嚴重的是
    get_rag_answer 的來源後置處理會把來源段落接在 Flex JSON 後面，讓它不再是
    合法 JSON，LINE 端退化成把整包 JSON 當純文字送出（實測 bug）。
    """
    from langchain_core.messages import ToolMessage

    from app.services.agent.utils.nodes import _already_ran_symptom_suggestion

    messages = [
        ToolMessage(
            content='{"type": "flex"}',
            name="suggest_department_for_symptom",
            tool_call_id="1",
        )
    ]
    assert _already_ran_symptom_suggestion(messages) is True
    assert _already_ran_symptom_suggestion([]) is False


def test_plain_symptom_question_still_forces_rag():
    """只有症狀、沒問科別時，本工具不會被呼叫，force_rag 的守衛不應誤擋。"""
    from langchain_core.messages import ToolMessage

    from app.services.agent.utils.nodes import _already_ran_symptom_suggestion

    messages = [
        ToolMessage(content="衛教內容", name="get_rag_answer", tool_call_id="1")
    ]
    assert _already_ran_symptom_suggestion(messages) is False


def test_flex_payload_survives_line_flex_parsing():
    """
    Flex 必須通過 LINE SDK 的 FlexContainer 驗證，否則 reply 端會靜默退化成
    純文字，使用者會看到一整包 JSON。
    """
    import asyncio

    from linebot.v3.messaging import FlexContainer

    loaded = load_symptom_table()
    svc = SymptomDepartmentService(table=loaded, normalizer=StubResolver())

    for question in ("我肚子痛，要看哪一科", "天空是藍色的要掛哪一科"):
        result = asyncio.run(svc.suggest(question))
        payload = build_symptom_department_flex(result)
        FlexContainer.from_dict(payload["contents"])


@pytest.mark.asyncio
async def test_service_never_returns_hotlines_or_emergency_kind(service):
    """
    急迫度已經拆到 urgency.py，擋在整個 agent 之前。本服務只回科別方向，
    不得再長出第二套急症判斷——那正是前一版「沒問科別就不檢查」的成因。
    """
    for text in ("我肚子痛到站不起來要掛哪一科", "我阿公昏迷要掛哪一科"):
        result = await service.suggest(text)
        assert result.kind in (RESULT_SUGGESTION, RESULT_FALLBACK)
        assert not hasattr(result, "hotlines")
        assert not hasattr(result, "action")


# ---------------------------------------------------------------- 兒科過濾


@pytest.fixture
def age_context():
    """把 request-scoped 年齡設定包成 context manager，測完一定還原。"""
    from contextlib import contextmanager

    from app.core.user_age import reset_request_age, set_request_age

    @contextmanager
    def _set(age):
        token = set_request_age(age)
        try:
            yield
        finally:
            reset_request_age(token)

    return _set


@pytest.mark.asyncio
@pytest.mark.parametrize("age", [None, 18, 35, 70])
async def test_adult_question_drops_pediatric(service, age_context, age):
    """
    對照表有 11 條症狀同時掛兒科與成人科別。成人問「我肚子好痛要掛哪一科」
    拿到「內科、兒科」時，兒科那一項對他沒有意義卻佔掉一個候選名額。
    年齡未知（None）時同樣濾掉——未知不等於是小孩。
    """
    with age_context(age):
        result = await service.suggest("我肚子好痛要掛哪一科")
    assert [c.canonical for c in result.candidates] == ["內科"]


@pytest.mark.asyncio
async def test_child_account_keeps_pediatric(service, age_context):
    with age_context(8):
        result = await service.suggest("我肚子好痛要掛哪一科")
    assert "兒科" in [c.canonical for c in result.candidates]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    ["小孩發燒要掛哪一科", "我兒子肚子痛要看哪科", "寶寶一直咳要掛哪科"],
)
async def test_parent_asking_for_a_child_keeps_pediatric(service, age_context, text):
    """
    家長帳號的年齡欄位是家長自己的，光看年齡會把兒科濾掉。訊息裡的孩童指涉
    是年齡蓋不到的那一半。
    """
    with age_context(40):
        result = await service.suggest(text)
    assert "兒科" in [c.canonical for c in result.candidates]


@pytest.mark.asyncio
async def test_pediatric_only_symptom_survives_the_filter(service, age_context):
    """
    兒科是唯一候選時一律保留——那代表這個症狀本來就只有兒科看。濾光會讓
    使用者拿到空卡或無謂的保底，比給一個不完全適用的科別更糟。
    """
    with age_context(40):
        result = await service.suggest("尿床要看哪一科")
    assert [c.canonical for c in result.candidates] == ["兒科"]


@pytest.mark.asyncio
async def test_filter_does_not_touch_non_pediatric_candidates(service, age_context):
    with age_context(40):
        result = await service.suggest("頭痛要掛哪一科")
    canonicals = [c.canonical for c in result.candidates]
    assert "兒科" not in canonicals
    assert canonicals == ["神經科", "家醫科"]
