import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage

from app.services.rag.question_decomposer import (
    MAX_SUB_QUESTIONS,
    GeminiQuestionDecomposer,
    SubQuestion,
    looks_compound,
    parse_decomposition,
)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("魚油跟魚肝油有什麼差別？吃降血脂藥要注意什麼？", True),
        ("Is it safe? What about coffee?", True),
        ("全形？半形?", True),
        # 只有一個問號：題組裡的單題與「單一問題多條件」控制題都長這樣
        ("同時有糖尿病與腎功能不佳的長輩，能不能直接按照網路上的高蛋白飲食建議吃？", False),
        # 以分號連接兩問（題組 T08）目前刻意不收
        ("我爸說自己吃得很少但都沒瘦；可是他年紀大了，我擔心肌肉流失，該注意什麼？", False),
        ("", False),
        (None, False),
    ],
)
def test_looks_compound_counts_question_marks(text, expected):
    assert looks_compound(text) is expected


def _raw(strategy, subs):
    return json.dumps({"strategy": strategy, "sub_questions": subs}, ensure_ascii=False)


def test_parse_compound_returns_both_phrasings():
    raw = _raw(
        "compound",
        [
            {"question": "魚油與魚肝油有什麼差別？", "retrieval_query": "魚油與魚肝油的差別",
             "kept_context": [], "dropped_context": ["最近買了魚肝油"]},
            {"question": "正在吃降血脂藥的人補充魚油要注意什麼？",
             "retrieval_query": "降血脂藥與魚油併用的注意事項"},
        ],
    )
    assert parse_decomposition(raw) == [
        SubQuestion("魚油與魚肝油有什麼差別？", "魚油與魚肝油的差別"),
        SubQuestion("正在吃降血脂藥的人補充魚油要注意什麼？", "降血脂藥與魚油併用的注意事項"),
    ]


def test_parse_single_returns_empty():
    assert parse_decomposition(_raw("single", [])) == []


def test_parse_compound_with_one_valid_sub_returns_empty():
    raw = _raw("compound", [{"question": "只有一題？", "retrieval_query": "只有一題"}, {"question": "  "}])
    assert parse_decomposition(raw) == []


def test_parse_missing_retrieval_query_falls_back_to_question():
    raw = _raw("compound", [{"question": "甲？"}, {"question": "乙？", "retrieval_query": ""}])
    assert parse_decomposition(raw) == [SubQuestion("甲？", "甲？"), SubQuestion("乙？", "乙？")]


def test_parse_caps_sub_questions():
    subs = [{"question": f"第{i}題？", "retrieval_query": f"第{i}題"} for i in range(5)]
    assert len(parse_decomposition(_raw("compound", subs))) == MAX_SUB_QUESTIONS


def test_parse_accepts_fenced_json():
    raw = "```json\n" + _raw("compound", [{"question": "甲？"}, {"question": "乙？"}]) + "\n```"
    assert [s.question for s in parse_decomposition(raw)] == ["甲？", "乙？"]


def test_parse_malformed_output_raises():
    with pytest.raises(Exception):
        parse_decomposition("這不是 JSON")


async def test_gemini_decomposer_sends_original_message_in_prompt():
    gemini = MagicMock()
    gemini.chat_model.ainvoke = AsyncMock(
        return_value=AIMessage(content=_raw("compound", [{"question": "甲？"}, {"question": "乙？"}]))
    )
    decomposer = GeminiQuestionDecomposer(gemini)

    subs = await decomposer.decompose("阿嬤好幾天沒便意是便祕嗎？平常要怎麼調整？")

    assert [s.question for s in subs] == ["甲？", "乙？"]
    prompt = gemini.chat_model.ainvoke.await_args.args[0][0].content
    assert "使用者訊息：阿嬤好幾天沒便意是便祕嗎？平常要怎麼調整？" in prompt
