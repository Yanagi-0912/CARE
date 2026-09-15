import json

import pytest

from app.services.medical.department_matcher import CANONICAL_DEPARTMENTS
from app.services.medical.symptom_classification.symptom_table import (
    SymptomTableError,
    load_symptom_table,
)

# 服務流程（查表、兒科過濾、候選數上限、保底、卡片標註與參考來源）由
# test_symptom_acceptance.py 的 H 節檢核清單驗證，這裡只留清單沒涵蓋的項目。


@pytest.fixture(scope="module")
def table():
    return load_symptom_table()


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
        ("腰酸背痛", ["骨科", "家醫科", "復健科"]),
        ("紅斑性狼瘡", ["內科", "皮膚科"]),
        # 坐骨神經痛、性病的順序原由 rank 決定，改由 test_symptom_acceptance.py 的 T12 驗證。
    ],
)
def test_reviewed_candidate_order(table, term, expected):
    assert [c.canonical for c in table.lookup(term).candidates] == expected
