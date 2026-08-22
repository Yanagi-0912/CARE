import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

if "motor.motor_asyncio" not in sys.modules:
    motor_module = types.ModuleType("motor")
    motor_asyncio_module = types.ModuleType("motor.motor_asyncio")
    motor_asyncio_module.AsyncIOMotorClient = object
    motor_asyncio_module.AsyncIOMotorCollection = object
    motor_module.motor_asyncio = motor_asyncio_module
    sys.modules["motor"] = motor_module
    sys.modules["motor.motor_asyncio"] = motor_asyncio_module

from app.services.rag.claim_verification.matcher import (
    ClaimMatch,
    MongoAtlasClaimMatcher,
)


def _fake_collection(rows):
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=rows)
    collection = MagicMock()
    collection.aggregate.return_value = cursor
    return collection


def _make_matcher(**overrides):
    emb = MagicMock()
    emb.aembed_query = AsyncMock(return_value=[0.1, 0.2, 0.3])
    kwargs = {
        "embeddings": emb,
        "mongo_uri": "mongodb://localhost",
        "db_name": "db",
        "collection_name": "health_articles_chunks",
        "index_name": "claim_vector_idx",
        "k": 5,
        "min_score": 0.8,
    }
    kwargs.update(overrides)
    return MongoAtlasClaimMatcher(**kwargs), emb


@pytest.mark.asyncio
async def test_match_returns_claim_match_on_hit():
    matcher, emb = _make_matcher()
    matcher._collection = _fake_collection(
        [
            {
                "claim": "網傳「疫苗是人口滅絕工具」？",
                "verdict": "錯誤",
                "verdict_slug": "incorrect",
                "url": "https://tfc.example/123",
                "original_title": "【錯誤】網傳「疫苗是人口滅絕工具」？",
                "chunk_content": "查核中心訪問多位公衛學者，均表示無實證支持此說法。",
                "score": 0.95,
            }
        ]
    )

    result = await matcher.match("疫苗是人口滅絕工具")

    assert result == ClaimMatch(
        claim="網傳「疫苗是人口滅絕工具」？",
        verdict="錯誤",
        verdict_slug="incorrect",
        url="https://tfc.example/123",
        title="【錯誤】網傳「疫苗是人口滅絕工具」？",
        content="查核中心訪問多位公衛學者，均表示無實證支持此說法。",
        score=0.95,
    )
    emb.aembed_query.assert_awaited_once_with("疫苗是人口滅絕工具")


@pytest.mark.asyncio
async def test_match_returns_none_when_below_min_score():
    matcher, _emb = _make_matcher(min_score=0.8)
    matcher._collection = _fake_collection(
        [
            {
                "claim": "吃鳳梨心可以溶解血栓",
                "verdict": "錯誤",
                "verdict_slug": "incorrect",
                "url": "https://tfc.example/456",
                "original_title": "【錯誤】網傳吃鳳梨心可以溶解血栓？",
                "chunk_content": "無醫學實證支持。",
                "score": 0.79,
            }
        ]
    )

    assert await matcher.match("吃鳳梨心能溶血栓嗎") is None


@pytest.mark.asyncio
async def test_match_dedupes_same_article_chunks_by_url_before_scoring():
    """同一篇文章的三個 chunk 分數不同時，只取該篇最高分那筆；
    且不能因為單篇塞滿候選名額而漏掉真正分數最高的其他文章。"""
    matcher, _emb = _make_matcher(min_score=0.5)
    matcher._collection = _fake_collection(
        [
            {
                "claim": "A 主張",
                "verdict": "部分錯誤",
                "verdict_slug": "partially-incorrect",
                "url": "https://tfc.example/article-a",
                "original_title": "文章 A",
                "chunk_content": "A 的第一段",
                "score": 0.60,
            },
            {
                "claim": "A 主張",
                "verdict": "部分錯誤",
                "verdict_slug": "partially-incorrect",
                "url": "https://tfc.example/article-a",
                "original_title": "文章 A",
                "chunk_content": "A 的第二段（分數最高的 chunk）",
                "score": 0.91,
            },
            {
                "claim": "A 主張",
                "verdict": "部分錯誤",
                "verdict_slug": "partially-incorrect",
                "url": "https://tfc.example/article-a",
                "original_title": "文章 A",
                "chunk_content": "A 的第三段",
                "score": 0.70,
            },
            {
                "claim": "B 主張",
                "verdict": "正確",
                "verdict_slug": "correct",
                "url": "https://tfc.example/article-b",
                "original_title": "文章 B",
                "chunk_content": "B 的內容",
                "score": 0.55,
            },
        ]
    )

    result = await matcher.match("查詢字串")

    # 分數最高的是文章 A 的第二個 chunk，不是隨便一筆同 url 的 chunk。
    assert result.url == "https://tfc.example/article-a"
    assert result.content == "A 的第二段（分數最高的 chunk）"
    assert result.score == 0.91


@pytest.mark.asyncio
async def test_match_ignores_documents_without_verdict():
    """其他來源（衛福部闢謠、食藥署等）沒有 verdict，即使分數更高也不能被採計。"""
    matcher, _emb = _make_matcher(min_score=0.5)
    matcher._collection = _fake_collection(
        [
            {
                "claim": None,
                "verdict": None,
                "verdict_slug": None,
                "url": "https://other.example/no-verdict",
                "original_title": "非 TFC 文章",
                "chunk_content": "衛教內容",
                "score": 0.99,
            },
            {
                "claim": "C 主張",
                "verdict": "事實釐清",
                "verdict_slug": "clarification",
                "url": "https://tfc.example/article-c",
                "original_title": "文章 C",
                "chunk_content": "C 的內容",
                "score": 0.85,
            },
        ]
    )

    result = await matcher.match("查詢字串")

    assert result is not None
    assert result.url == "https://tfc.example/article-c"
    assert result.verdict == "事實釐清"


@pytest.mark.asyncio
async def test_match_returns_none_when_aggregate_raises():
    matcher, _emb = _make_matcher()
    failing_collection = MagicMock()
    failing_collection.aggregate.side_effect = RuntimeError("index not found")
    matcher._collection = failing_collection

    assert await matcher.match("查詢字串") is None


def test_ensure_collection_requires_index_name():
    """迴歸測試（I5 finding）：錯誤訊息曾經寫著 MONGODB_CLAIM_VECTOR_INDEX，
    但那個環境變數在 config.py／.env.example 都不存在（design.md 決策 2
    已改為沿用既有的 MONGODB_VECTOR_INDEX）。維運照舊訊息去找不存在的
    設定，正是這條分支已經付過一次代價的失效形態。"""
    matcher, _emb = _make_matcher(index_name="")
    with pytest.raises(ValueError, match="MONGODB_VECTOR_INDEX"):
        matcher._ensure_collection()
    with pytest.raises(ValueError) as exc_info:
        matcher._ensure_collection()
    assert "MONGODB_CLAIM_VECTOR_INDEX" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_match_builds_vector_search_pipeline_on_vector_field():
    matcher, emb = _make_matcher(k=5)
    emb.aembed_query = AsyncMock(return_value=[0.1, 0.2, 0.3])
    collection = _fake_collection(
        [
            {
                "claim": "D 主張",
                "verdict": "證據不足",
                "verdict_slug": "unproven",
                "url": "https://tfc.example/article-d",
                "original_title": "文章 D",
                "chunk_content": "D 的內容",
                "score": 0.9,
            }
        ]
    )
    matcher._collection = collection

    await matcher.match("查詢字串")

    pipeline = collection.aggregate.call_args.args[0]
    vector_stage = pipeline[0]["$vectorSearch"]
    # path 預設須為 "embedding"（既有內容向量欄位），不是 "claim"（純文字）
    # ——2026-08-18 production Atlas 實測：誤指向 "claim" 會讓 MongoDB 回
    # OperationFailure，並被 match() 的 fail-open 吞成「未命中」不報錯。
    assert vector_stage["index"] == "claim_vector_idx"
    assert vector_stage["path"] == "embedding"
    assert vector_stage["queryVector"] == [0.1, 0.2, 0.3]
    assert vector_stage["numCandidates"] == 5 * 30
    assert vector_stage["limit"] == 5


@pytest.mark.asyncio
async def test_match_uses_vector_field_for_search_and_claim_field_for_projection():
    """迴歸測試（2026-08-18 production Atlas 實測發現的 Critical bug）：
    最初只有 claim_field 一個參數、預設同時充當 $vectorSearch 的 path 與
    claim 文字投影欄位，導致 path 誤指向 "claim"（純文字，非向量），
    MongoDB 回 OperationFailure，又被 match() 的 fail-open 吞成「未命中」，
    線上因此每次都回「證據不足」且不報錯。這裡用刻意不同的兩個值鎖住：
    $vectorSearch 的 path 只認 vector_field，ClaimMatch.claim 的內容
    只認 claim_field，兩者互不影響、缺一不可。"""
    matcher, emb = _make_matcher(
        k=5, vector_field="embedding", claim_field="claim_text"
    )
    emb.aembed_query = AsyncMock(return_value=[0.1, 0.2, 0.3])
    collection = _fake_collection(
        [
            {
                "claim_text": "E 主張",
                "verdict": "錯誤",
                "verdict_slug": "incorrect",
                "url": "https://tfc.example/article-e",
                "original_title": "文章 E",
                "chunk_content": "E 的內容",
                "score": 0.9,
            }
        ]
    )
    matcher._collection = collection

    result = await matcher.match("查詢字串")

    pipeline = collection.aggregate.call_args.args[0]
    vector_stage = pipeline[0]["$vectorSearch"]
    project_stage = pipeline[1]["$project"]
    assert vector_stage["path"] == "embedding"
    assert project_stage.get("claim_text") == 1
    assert "embedding" not in project_stage  # 向量欄位本身不該被投影回來
    assert result is not None
    assert result.claim == "E 主張"


# ── verdict 合法值檢核（I4 finding）：執行期過去沒有任何一處驗證 verdict
# 屬於 spec 明訂的五個合法值，CARE-data 的前綴對照表出過事故（漏收「正確」
# 導致 verdict 寫成 None），換一種形狀出錯（誤植空白、TFC 改用詞）時，
# 非法字串會原樣透傳到呈現層，被 verdict_flex 的配色表誤判成中性灰 ──────


@pytest.mark.asyncio
async def test_match_returns_none_when_verdict_is_not_a_valid_value(caplog):
    matcher, _emb = _make_matcher(min_score=0.5)
    matcher._collection = _fake_collection(
        [
            {
                "claim": "F 主張",
                "verdict": "錯誤 ",  # 尾隨空白：CARE-data 前綴對照表類型的資料異常
                "verdict_slug": "incorrect",
                "url": "https://tfc.example/article-f",
                "original_title": "文章 F",
                "chunk_content": "F 的內容",
                "score": 0.9,
            }
        ]
    )

    with caplog.at_level("WARNING"):
        result = await matcher.match("查詢字串")

    assert result is None
    assert any(
        "invalid verdict" in rec.getMessage() for rec in caplog.records
    ), "非法 verdict 必須留下 log，否則配色反向卻無人發現"


@pytest.mark.asyncio
async def test_match_returns_none_when_verdict_is_unrecognized_string(caplog):
    """非五個合法值之一的任意字串（例如上游改版用詞、拼字錯誤）同樣要擋下，
    不是只擋「有尾隨空白」這種特例。"""
    matcher, _emb = _make_matcher(min_score=0.5)
    matcher._collection = _fake_collection(
        [
            {
                "claim": "G 主張",
                "verdict": "誤導",  # 不在五個合法值內
                "verdict_slug": "misleading",
                "url": "https://tfc.example/article-g",
                "original_title": "文章 G",
                "chunk_content": "G 的內容",
                "score": 0.9,
            }
        ]
    )

    assert await matcher.match("查詢字串") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "verdict", ["錯誤", "部分錯誤", "正確", "事實釐清", "證據不足"]
)
async def test_match_accepts_all_five_valid_verdicts(verdict):
    matcher, _emb = _make_matcher(min_score=0.5)
    matcher._collection = _fake_collection(
        [
            {
                "claim": "H 主張",
                "verdict": verdict,
                "verdict_slug": "some-slug",
                "url": "https://tfc.example/article-h",
                "original_title": "文章 H",
                "chunk_content": "H 的內容",
                "score": 0.9,
            }
        ]
    )

    result = await matcher.match("查詢字串")

    assert result is not None
    assert result.verdict == verdict


# ── content 為空視為未命中（C2 finding）：content_field 若接線疏漏（例如
# dependencies.py 忘記傳 settings.MONGODB_TEXT_FIELD，落到與真實資料不符的
# 硬寫預設值），match 到的文件會沒有內容可改寫理由。判定沒有依據就不該
# 發卡片，這裡是最後一道防線 ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_match_returns_none_when_content_field_is_blank(caplog):
    matcher, _emb = _make_matcher(min_score=0.5, content_field="chunk_content")
    matcher._collection = _fake_collection(
        [
            {
                "claim": "I 主張",
                "verdict": "錯誤",
                "verdict_slug": "incorrect",
                "url": "https://tfc.example/article-i",
                "original_title": "文章 I",
                "chunk_content": "",  # content_field 接線疏漏時的典型徵狀
                "score": 0.9,
            }
        ]
    )

    with caplog.at_level("WARNING"):
        result = await matcher.match("查詢字串")

    assert result is None
    assert any("empty content" in rec.getMessage() for rec in caplog.records)


@pytest.mark.asyncio
async def test_match_returns_none_when_content_field_is_missing_entirely():
    """content_field 指向一個資料庫裡根本不存在的欄位名（比空字串更貼近
    真實的接線疏漏：硬寫的欄位名跟資料庫實際欄位對不上）時，同樣要視為
    未命中，而不是讓 ClaimMatch.content 帶著空字串繼續往下游流動。"""
    matcher, _emb = _make_matcher(min_score=0.5, content_field="wrong_field_name")
    matcher._collection = _fake_collection(
        [
            {
                "claim": "J 主張",
                "verdict": "錯誤",
                "verdict_slug": "incorrect",
                "url": "https://tfc.example/article-j",
                "original_title": "文章 J",
                "chunk_content": "真正的內容在這個欄位，但 content_field 指錯了",
                "score": 0.9,
            }
        ]
    )

    assert await matcher.match("查詢字串") is None


@pytest.mark.asyncio
async def test_match_prefilters_to_valid_verdicts_inside_vector_search():
    """判定過濾要在 $vectorSearch 內前置，不是取回 top-k 之後才 $match。

    後置過濾是結構性的召回上限：知識庫裡查核報告只佔約三分之一，其餘是衛教文，
    而使用者問的謠言題目往往兩邊都寫過，於是 top-k 可能整批是衛教文，真正查核
    過那篇根本進不了候選。實測「微波爐加熱產生致癌物」後置只拿到 4/10 篇查核
    報告，前置是 10/10。拉高 numCandidates 解決不了，因為瓶頸是 limit。
    """
    from app.services.rag.claim_verification.matcher import _VALID_VERDICTS

    matcher, _ = _make_matcher()
    collection = _fake_collection([])
    matcher._collection = collection

    await matcher.match("查詢字串")

    pipeline = collection.aggregate.call_args.args[0]
    vector_stage = pipeline[0]["$vectorSearch"]
    assert vector_stage["filter"] == {"verdict": {"$in": sorted(_VALID_VERDICTS)}}
    assert not any("$match" in stage for stage in pipeline), (
        "判定過濾已前置，後置 $match 是多餘的一層")
