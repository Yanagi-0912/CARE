import pytest

from app.services.medical_news.grader import (
    NEWS_SCHEMA,
    GeminiNewsGrader,
    NewsJudgement,
)


def _grader(payload):
    async def _invoke(prompt: str):
        _invoke.prompt = prompt
        return payload

    grader = GeminiNewsGrader(invoke_judge=_invoke)
    grader._probe = _invoke
    return grader


@pytest.mark.asyncio
async def test_judge_parses_structured_payload():
    grader = _grader(
        {
            "is_about_this_drug": True,
            "concern_kind": "recall",
            "summary": "食藥署公告某批號回收。",
        }
    )

    result = await grader.judge("普拿疼", "回收公告", "內文")

    assert result == NewsJudgement(True, "recall", "食藥署公告某批號回收。")


@pytest.mark.asyncio
async def test_judge_accepts_none_concern_kind():
    """`none` 是合法的判定結果（代表「有講到這個藥但不構成消息」），
    只是呼叫端不該據以推播。它與「輸出不合法」必須分得開。"""
    grader = _grader(
        {"is_about_this_drug": True, "concern_kind": "none", "summary": ""}
    )

    result = await grader.judge("普拿疼", "標題", "內文")

    assert result.concern_kind == "none"


@pytest.mark.asyncio
async def test_judge_raises_on_unknown_concern_kind():
    grader = _grader(
        {"is_about_this_drug": True, "concern_kind": "回收", "summary": "x"}
    )

    with pytest.raises(ValueError):
        await grader.judge("普拿疼", "標題", "內文")


@pytest.mark.asyncio
async def test_judge_raises_on_missing_field():
    grader = _grader({"is_about_this_drug": True, "summary": "x"})

    with pytest.raises(ValueError):
        await grader.judge("普拿疼", "標題", "內文")


@pytest.mark.asyncio
async def test_judge_raises_on_non_dict_payload():
    grader = _grader("recall")

    with pytest.raises(ValueError):
        await grader.judge("普拿疼", "標題", "內文")


@pytest.mark.asyncio
async def test_judge_does_not_swallow_exceptions():
    """判定失敗必須讓呼叫端知道，才能 fail closed。

    在這裡吞掉例外回一個預設值，等於把 fail closed 變成 fail open——
    這是 design.md 決策 4 唯一不能妥協的地方。
    """

    async def _boom(prompt: str):
        raise TimeoutError("upstream timeout")

    grader = GeminiNewsGrader(invoke_judge=_boom)

    with pytest.raises(TimeoutError):
        await grader.judge("普拿疼", "標題", "內文")


@pytest.mark.asyncio
async def test_prompt_requires_neutral_third_person_summary():
    """摘要中性化是「分享零洩漏」的承重條件（design 決策 6）。

    分享路徑不做任何文字改寫，它只是不帶個人化的那兩行；因此摘要本身若寫成
    第二人稱，洩漏就會直接跟著分享卡送出去。
    """
    grader = _grader(
        {"is_about_this_drug": False, "concern_kind": "none", "summary": ""}
    )

    await grader.judge("普拿疼", "標題", "內文")

    prompt = grader._probe.prompt
    assert "第三人稱" in prompt
    assert "您" in prompt


@pytest.mark.asyncio
async def test_prompt_forbids_medication_advice():
    grader = _grader(
        {"is_about_this_drug": False, "concern_kind": "none", "summary": ""}
    )

    await grader.judge("普拿疼", "標題", "內文")

    assert "停藥" in grader._probe.prompt


@pytest.mark.asyncio
async def test_judge_truncates_long_body():
    """公告內文可能上萬字，整段送進模型是不必要的成本。"""
    grader = _grader(
        {"is_about_this_drug": False, "concern_kind": "none", "summary": ""}
    )
    grader._max_chars = 100

    await grader.judge("普拿疼", "標題", "內" * 5000)

    assert grader._probe.prompt.count("內") <= 200


def test_schema_requires_all_three_fields():
    assert set(NEWS_SCHEMA["required"]) == {
        "is_about_this_drug",
        "concern_kind",
        "summary",
    }


def test_schema_concern_kind_includes_none():
    assert "none" in NEWS_SCHEMA["properties"]["concern_kind"]["enum"]


@pytest.mark.asyncio
async def test_grader_without_backend_raises():
    grader = GeminiNewsGrader()

    with pytest.raises(RuntimeError):
        await grader.judge("普拿疼", "標題", "內文")
