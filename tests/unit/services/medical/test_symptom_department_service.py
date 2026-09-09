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
    """沒有人工優先序時，三家都這樣分類的科別要排在只有一家的前面。

    有 rank 的條目不在此列：來源共識描述的是三份表怎麼分類，不是臨床動線，
    指定了 rank 就代表有人判斷過該先去哪一科（見 DepartmentCandidate.rank）。
    """
    for term in table.terms:
        counts = [
            c.source_count for c in table.lookup(term).candidates if c.rank is None
        ]
        assert counts == sorted(counts, reverse=True), term


def test_manual_rank_outranks_cross_source_agreement(table):
    """
    坐骨神經痛：骨科只有一家來源，神經外科與復健科各有來源，但非外傷性的
    坐骨神經痛第一線就是骨科與復健科，先被導向手術科別是錯的動線。
    """
    candidates = table.lookup("坐骨神經痛").candidates
    assert candidates[0].canonical == "骨科"
    assert candidates[0].source_count < candidates[1].source_count


def test_ranked_candidates_come_before_unranked_ones(table):
    """只指定部分候選時，有指定的照 rank 排前，其餘維持共識順序。"""
    for term in table.terms:
        ranked = [c.rank is not None for c in table.lookup(term).candidates]
        assert ranked == sorted(ranked, reverse=True), term
        ranks = [c.rank for c in table.lookup(term).candidates if c.rank is not None]
        assert ranks == sorted(ranks), term


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

    # 候選數刻意由 MAX_CANDIDATES 推導：寫死科別清單時，上限一調（3 → 5）這條
    # 測試就會從「驗保底」變成「驗剛好沒超過」而靜默失去意義。
    over_the_cap = ("內科", "外科", "婦產科", "泌尿科", "皮膚科", "骨科", "眼科")[
        : MAX_CANDIDATES + 1
    ]
    entry = SymptomEntry(
        term="多科症狀",
        kind="symptom",
        candidates=tuple(
            DepartmentCandidate(
                canonical=name, subgroup=None, facility_count=1, sources=("V",)
            )
            for name in over_the_cap
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
async def test_adult_never_sees_pediatric_even_as_last_resort(service, age_context):
    """
    初版在濾光時回傳兒科，理由是「那代表這症狀本來就只有兒科看」。實測推翻了
    這個前提：「嘔吐」當時也只有兒科，但那是表缺成人科別，不是臨床事實。
    程式分不出兩者，所以一律走保底——見 test_adult_gets_fallback_when_only_pediatric_remains。
    """
    with age_context(40):
        result = await service.suggest("尿床要看哪一科")
    assert "兒科" not in [c.canonical for c in result.candidates]


@pytest.mark.asyncio
async def test_filter_does_not_touch_non_pediatric_candidates(service, age_context):
    with age_context(40):
        result = await service.suggest("頭痛要掛哪一科")
    canonicals = [c.canonical for c in result.candidates]
    assert "兒科" not in canonicals
    assert canonicals == ["神經科", "家醫科"]


# ---------------------------------------------------------------- 兒科濾光的處置


@pytest.mark.asyncio
async def test_vomiting_now_has_an_adult_department(table):
    """
    實測回報：「我感覺噁心、嘔吐，要看哪一科」回傳僅兒科。成因是三份來源的
    粒度不一致——榮總玉里把嘔吐收在「小兒科／15歲以下兒童」那一整列裡，
    成大與台大未單列，於是表上只有兒科。已補列於內科。
    """
    canonicals = [c.canonical for c in table.lookup("嘔吐").candidates]
    assert "內科" in canonicals


@pytest.mark.asyncio
async def test_adult_gets_fallback_when_only_pediatric_remains(table, age_context):
    """
    濾光有兩種可能而程式分不出來：這個症狀真的只有兒科看（尿床），或表缺了
    成人科別（嘔吐曾是如此）。初版在這裡回傳兒科，於是後者會給成人一個明確
    錯誤的答案。改成走保底——「誠實的不確定」優於「錯的答案」。
    """
    service = SymptomDepartmentService(
        table=table, normalizer=StubResolver({"x": "尿床"})
    )
    with age_context(40):
        result = await service.suggest("x")

    assert result.kind == RESULT_FALLBACK
    assert result.matched_term == "尿床"
    assert "兒科" not in [c.canonical for c in result.candidates]
    assert [c.canonical for c in result.candidates] == list(FALLBACK_DEPARTMENTS)


@pytest.mark.asyncio
async def test_child_still_gets_the_pediatric_only_symptom(table, age_context):
    service = SymptomDepartmentService(
        table=table, normalizer=StubResolver({"x": "尿床"})
    )
    with age_context(8):
        result = await service.suggest("x")

    assert result.kind == RESULT_SUGGESTION
    assert [c.canonical for c in result.candidates] == ["兒科"]


# ---------------------------------------------------------------- 症狀層級出處


@pytest.mark.asyncio
async def test_term_sources_survive_the_pediatric_filter(table, age_context):
    """
    成人問「嘔吐」拿到的內科是本專案補列的（sources 為空），但榮總玉里的表確實
    收錄了嘔吐——列在小兒科。那個來源代碼只掛在被濾掉的兒科候選上，服務層若不
    先收下來，卡片會變成一張完全沒有出處的卡，而出處其實存在、點進去也找得到
    使用者問的症狀。
    """
    service = SymptomDepartmentService(
        table=table, normalizer=StubResolver({"x": "嘔吐"})
    )
    with age_context(40):
        result = await service.suggest("x")

    assert [c.canonical for c in result.candidates] == ["內科"]
    assert result.candidates[0].sources == ()
    assert "V" in result.term_sources


@pytest.mark.asyncio
async def test_term_sources_are_deduplicated(table, age_context):
    """同一家醫院把症狀列在兩個科別時只算一次，否則卡片會列出重複的出處。"""
    service = SymptomDepartmentService(
        table=table, normalizer=StubResolver({"x": "腹瀉"})
    )
    with age_context(40):
        result = await service.suggest("x")

    assert result.term_sources == ("V", "N")


@pytest.mark.asyncio
async def test_fallback_carries_no_term_sources(service, age_context):
    """保底建議不是來自任何一家醫院的對照表，帶著出處出去就是替它背書。"""
    with age_context(40):
        result = await service.suggest("天空是藍色的要掛哪一科")

    assert result.kind == RESULT_FALLBACK
    assert result.term_sources == ()


# ---------------------------------------------------------------- 次專科標籤用語


def test_every_subgroup_is_a_registrable_clinic_name(table):
    """
    subgroup 會直接印在卡片的科別標籤上，因此必須是掛號窗口報得出來的診名。

    初版存的是分類用語（「感染」「胸腔」「初診分流」），標籤上就出現一個掛不到
    號的詞——使用者拿著它到櫃檯說不出口，等於給了錯的指示。
    """
    for term in table.terms:
        for candidate in table.lookup(term).candidates:
            for subgroup in candidate.subgroups:
                assert subgroup.endswith("科"), (
                    f"{term} / {candidate.canonical} 的次專科 {subgroup!r} 不是診名"
                )


@pytest.mark.asyncio
async def test_common_cold_points_at_where_people_actually_go(table, age_context):
    """
    感冒在三家對照表分屬感染科、家庭醫學科、胸腔內科，沒有共識。與其挑一家的
    分法掛標籤，不如給民眾真的會去的窗口——家醫科與耳鼻喉科。
    """
    service = SymptomDepartmentService(
        table=table, normalizer=StubResolver({"x": "感冒"})
    )
    with age_context(40):
        result = await service.suggest("x")

    canonicals = [c.canonical for c in result.candidates]
    assert "家醫科" in canonicals and "耳鼻喉科" in canonicals
    assert all(c.subgroups == () for c in result.candidates)


@pytest.mark.asyncio
async def test_hyperlipidemia_offers_the_cardiology_route(table, age_context):
    """高血脂合併心血管風險時走心臟內科是常規，只給新陳代謝偏窄。"""
    service = SymptomDepartmentService(
        table=table, normalizer=StubResolver({"x": "高血脂"})
    )
    with age_context(40):
        result = await service.suggest("x")

    assert result.candidates[0].subgroups == ("新陳代謝內分泌科", "心臟內科")
    assert "家醫科" in [c.canonical for c in result.candidates]


# ------------------------------------------------ 人工複查補上的候選科別（B 節）
#
# 來源：openspec/changes/symptom-department-guidance/subgroup_review.md
# 這些症狀原本只掛在一個科別，因為三份來源裡把它列在別科的那一家在整併時被
# 對到同一個 canonical，另一家的分法就沒進表。缺的候選會讓使用者被導向錯的
# 窗口（隱睪症只給外科、成人問就找不到泌尿科），因此逐筆補回並在此鎖住。


@pytest.mark.parametrize(
    ("term", "expected"),
    [
        ("隱睪症", ["外科", "泌尿科"]),
        ("包皮過長", ["泌尿科", "外科"]),
        ("尿道下裂", ["泌尿科", "外科"]),
        ("關節炎", ["內科", "骨科"]),
        ("坐骨神經痛", ["骨科", "神經外科", "復健科"]),
        ("腰酸背痛", ["骨科", "家醫科", "復健科"]),
        ("性病", ["皮膚科", "內科", "泌尿科"]),
        ("紅斑性狼瘡", ["內科", "皮膚科"]),
        ("感冒", ["家醫科", "耳鼻喉科", "內科"]),
    ],
)
def test_reviewed_candidate_order(table, term, expected):
    assert [c.canonical for c in table.lookup(term).candidates] == expected


def test_asthma_offers_family_medicine_to_adults(table):
    """氣喘的兒科候選會被成人過濾掉，家醫科補上後成人才有第二個方向。"""
    canonicals = [c.canonical for c in table.lookup("氣喘").candidates]
    assert canonicals == ["內科", "兒科", "家醫科"]


def test_reviewed_additions_stay_within_the_candidate_cap(table):
    """
    補候選會逼近 MAX_CANDIDATES，超過就整筆退成保底卡——那等於為了補一個科別
    而讓原本答得出來的症狀答不出來。
    """
    for term in ("坐骨神經痛", "腰酸背痛", "性病", "感冒", "氣喘"):
        assert not table.lookup(term).is_too_broad
