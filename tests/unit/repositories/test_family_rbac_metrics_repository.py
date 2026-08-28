"""RBAC 遷移差異計數器。

這裡守的是一條原則：**這個 repository 只存數字，不存判斷。** 「多少算夠低」
是部署決策，一旦有人在這裡寫下門檻，那個決定就凍結在寫程式的那一天，而它
需要看當時的實際分布才判斷得出來。
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.repositories.family_rbac_metrics_repository import (
    DIRECTIONS,
    FamilyRbacMetricsRepository,
)

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
OWNER = "U-owner"


def make_collection(find_one_result=None, find_results=None, aggregate_results=None):
    collection = MagicMock()
    collection.update_one = AsyncMock()
    collection.find_one = AsyncMock(return_value=find_one_result)
    collection.create_index = AsyncMock()

    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.to_list = AsyncMock(return_value=find_results or [])
    collection.find.return_value = cursor

    agg_cursor = MagicMock()
    agg_cursor.to_list = AsyncMock(return_value=aggregate_results or [])
    collection.aggregate.return_value = agg_cursor
    return collection


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", DIRECTIONS)
async def test_record_increments_the_named_direction(direction):
    collection = make_collection()
    await FamilyRbacMetricsRepository.record(
        OWNER, direction, now=NOW, collection=collection
    )
    query, update = collection.update_one.await_args.args
    assert query == {"owner_id": OWNER}
    assert update["$inc"] == {direction: 1}


@pytest.mark.asyncio
async def test_two_directions_are_counted_separately():
    """收緊與放寬的意義完全不同，混在同一個數字裡就分不出 bug 與遷移成本。"""
    collection = make_collection()
    await FamilyRbacMetricsRepository.record(OWNER, "tighten", collection=collection)
    await FamilyRbacMetricsRepository.record(OWNER, "loosen", collection=collection)
    increments = [
        call.args[1]["$inc"] for call in collection.update_one.await_args_list
    ]
    assert increments == [{"tighten": 1}, {"loosen": 1}]


@pytest.mark.asyncio
async def test_unknown_direction_is_rejected_before_writing():
    collection = make_collection()
    with pytest.raises(ValueError):
        await FamilyRbacMetricsRepository.record(
            OWNER, "sideways", collection=collection
        )
    collection.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_decisions_are_counted_as_the_denominator():
    """判準 1 要的是比例；只有分子沒有分母算不出來。"""
    collection = make_collection()
    await FamilyRbacMetricsRepository.record_decision(
        OWNER, now=NOW, collection=collection
    )
    _, update = collection.update_one.await_args.args
    assert update["$inc"] == {"decisions": 1}


@pytest.mark.asyncio
async def test_get_returns_zeros_for_an_unknown_owner():
    """「沒有差異」與「沒有這個人」在數字上是同一件事，回 None 會逼呼叫端
    到處判空。"""
    collection = make_collection(find_one_result=None)
    result = await FamilyRbacMetricsRepository.get(OWNER, collection=collection)
    assert result == {
        "owner_id": OWNER,
        "tighten": 0,
        "loosen": 0,
        "decisions": 0,
        "last_diff_at": None,
    }


@pytest.mark.asyncio
async def test_list_owners_with_tighten_sorts_by_impact():
    """判準 4：受影響對象要能逐一列舉，而且從影響最大的開始看。"""
    collection = make_collection(
        find_results=[
            {"owner_id": "U-a", "tighten": 9, "decisions": 100},
            {"owner_id": "U-b", "tighten": 2, "decisions": 50},
        ]
    )
    owners = await FamilyRbacMetricsRepository.list_owners_with_tighten(
        collection=collection
    )
    assert [o["owner_id"] for o in owners] == ["U-a", "U-b"]
    assert collection.find.call_args.args[0] == {"tighten": {"$gt": 0}}
    collection.find.return_value.sort.assert_called_once_with("tighten", -1)


@pytest.mark.asyncio
async def test_totals_returns_zeros_when_nothing_recorded():
    collection = make_collection(aggregate_results=[])
    assert await FamilyRbacMetricsRepository.totals(collection=collection) == {
        "tighten": 0,
        "loosen": 0,
        "decisions": 0,
        "owners": 0,
    }


@pytest.mark.asyncio
async def test_totals_sums_across_owners():
    collection = make_collection(
        aggregate_results=[
            {"_id": None, "tighten": 11, "loosen": 0, "decisions": 400, "owners": 7}
        ]
    )
    assert await FamilyRbacMetricsRepository.totals(collection=collection) == {
        "tighten": 11,
        "loosen": 0,
        "decisions": 400,
        "owners": 7,
    }


def test_repository_stores_no_thresholds():
    """門檻是部署決策，SHALL NOT 硬編。

    任何看起來像百分比門檻的常數出現在這裡，都代表那個決定被凍結在寫程式的
    那一天——而它需要看當時的實際分布才判斷得出來。
    """
    import inspect

    from app.repositories import family_rbac_metrics_repository as module

    source = inspect.getsource(module)
    for banned in ("threshold", "THRESHOLD", "is_ready", "IS_READY"):
        assert banned not in source, f"計數器不得內建門檻，但出現了 {banned}"


# ── 啟動時真的會建索引（tasks 4.x 的隱含前提）──────────────────────


def test_startup_creates_indexes_for_every_new_collection():
    """三份新 collection 的 `ensure_indexes` SHALL 在 lifespan 被呼叫。

    這一條原本漏掉了：三個 repository 都寫了 `ensure_indexes`，但沒有任何地方
    呼叫，於是索引在正式環境永遠不會存在。

    最嚴重的是 `family_rbac_metrics` 的 owner_id 唯一索引——差異計數是以
    owner_id 為鍵的 upsert `$inc`，少了唯一約束，併發會生出同一位擁有者的多份
    文件。那份指標正是決定何時對真實使用者開啟強制的依據，錯的指標比沒有指標
    更危險。

    以原始碼比對而非啟動整個 app：lifespan 會連資料庫、建索引、載入院所名稱
    索引並組裝排程器，在單元測試裡跑不動。
    """
    import inspect

    from app import main

    source = inspect.getsource(main.lifespan)
    for repo in (
        "FamilyDelegationRepository",
        "FamilyRoleAuditRepository",
        "FamilyRbacMetricsRepository",
    ):
        assert f"{repo}.ensure_indexes()" in source, f"{repo} 的索引不會被建立"
