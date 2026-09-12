import pytest

from app.services.medical_news.article_grader import (
    ARTICLE_SCHEMA,
    ArticleJudgement,
    GeminiKbArticleGrader,
    parse_article_judgement,
)


# ── 輸出解析 ────────────────────────────────────────────────────────


def test_parses_valid_payload():
    judgement = parse_article_judgement(
        {"is_useful_for_elderly": True, "reason": "講的是長者防跌"}
    )

    assert judgement == ArticleJudgement(
        is_useful_for_elderly=True, reason="講的是長者防跌"
    )


def test_missing_fields_raise():
    """不合法的輸出與「判定為不適合」是兩件事，必須分得開。

    回一個預設值會讓呼叫端把「判定沒有發生」記成一次正常的淘汰，於是 grader
    整個壞掉時看起來只像那天沒有合適的文章。
    """
    with pytest.raises(ValueError):
        parse_article_judgement({"reason": "少了判定欄位"})


def test_non_dict_payload_raises():
    with pytest.raises(ValueError):
        parse_article_judgement("true")


def test_string_verdict_is_rejected_rather_than_coerced():
    """`bool("false")` 是 True——這是最糟的失敗方向，會安靜地放行全部。"""
    with pytest.raises(ValueError):
        parse_article_judgement(
            {"is_useful_for_elderly": "false", "reason": "字串不是布林"}
        )


def test_missing_reason_text_is_tolerated_but_verdict_is_not():
    judgement = parse_article_judgement(
        {"is_useful_for_elderly": False, "reason": ""}
    )

    assert judgement.is_useful_for_elderly is False
    assert judgement.reason == ""


# ── 呼叫路徑 ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_judge_article_uses_injected_invoker():
    captured = {}

    async def fake_invoke(prompt):
        captured["prompt"] = prompt
        return {"is_useful_for_elderly": True, "reason": "ok"}

    grader = GeminiKbArticleGrader(invoke_judge=fake_invoke)

    judgement = await grader.judge_article("長者防跌", "掌握三要訣。")

    assert judgement.is_useful_for_elderly is True
    assert "長者防跌" in captured["prompt"]
    assert "掌握三要訣。" in captured["prompt"]


@pytest.mark.asyncio
async def test_excerpt_is_truncated_before_reaching_the_model():
    captured = {}

    async def fake_invoke(prompt):
        captured["prompt"] = prompt
        return {"is_useful_for_elderly": True, "reason": "ok"}

    grader = GeminiKbArticleGrader(invoke_judge=fake_invoke, max_chars=10)

    await grader.judge_article("標題", "一" * 500)

    assert "一" * 10 in captured["prompt"]
    assert "一" * 11 not in captured["prompt"]


@pytest.mark.asyncio
async def test_exceptions_are_not_swallowed():
    """呼叫端必須有能力 fail closed，所以本模組不得吞例外。"""

    async def boom(prompt):
        raise RuntimeError("quota exhausted")

    grader = GeminiKbArticleGrader(invoke_judge=boom)

    with pytest.raises(RuntimeError):
        await grader.judge_article("標題", "摘錄")


@pytest.mark.asyncio
async def test_requires_a_gemini_service_or_invoker():
    grader = GeminiKbArticleGrader()

    with pytest.raises(RuntimeError):
        await grader.judge_article("標題", "摘錄")


def test_schema_requires_both_fields():
    assert set(ARTICLE_SCHEMA["required"]) == {"is_useful_for_elderly", "reason"}
