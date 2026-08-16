"""排程器心跳登記簿。

這是 scheduler pod 的 livenessProbe 唯一的判斷依據，判錯的兩個方向都有實際
代價：誤判停擺會重啟 pod，而重啟期間錯過的服藥時段不會補推；漏判停擺則讓
用藥提醒靜靜停止而沒有任何警訊。因此門檻與容忍倍數的行為要被釘住。
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.core import scheduler_heartbeat


@pytest.fixture(autouse=True)
def _clean_registry():
    scheduler_heartbeat.reset()
    yield
    scheduler_heartbeat.reset()


def test_register_counts_as_first_beat():
    """剛登記就該是健康的——啟動後還沒跑完第一輪不該被判成停擺。"""
    scheduler_heartbeat.register("med", expected_interval_seconds=60)
    assert scheduler_heartbeat.stale() == []


def test_unregistered_beat_is_ignored():
    """未登記的名稱不得拋例外：心跳是旁路，不能成為弄垮排程器的原因。"""
    scheduler_heartbeat.beat("never-registered")
    assert scheduler_heartbeat.registered() == []


def test_stale_after_tolerance_window():
    scheduler_heartbeat.register(
        "med", expected_interval_seconds=60, tolerance_factor=3.0
    )
    now = datetime.now(timezone.utc)

    # 剛好在門檻內（179 秒 < 180 秒）
    assert scheduler_heartbeat.stale(now + timedelta(seconds=179)) == []

    # 超過門檻
    stale = scheduler_heartbeat.stale(now + timedelta(seconds=181))
    assert [name for name, _age in stale] == ["med"]


def test_beat_resets_the_clock():
    scheduler_heartbeat.register(
        "med", expected_interval_seconds=60, tolerance_factor=3.0
    )
    # 先讓它過期
    assert scheduler_heartbeat.stale(
        datetime.now(timezone.utc) + timedelta(seconds=300)
    )
    scheduler_heartbeat.beat("med")
    assert scheduler_heartbeat.stale() == []


def test_each_scheduler_uses_its_own_threshold():
    """兩個排程器的正常間隔差三個數量級，不能共用門檻。

    用藥提醒 60 秒一輪，每日摘要睡到隔天。用同一個門檻的話，後者會永遠
    被判成停擺，liveness 就變成無止境的重啟迴圈。
    """
    scheduler_heartbeat.register("med", expected_interval_seconds=60)
    scheduler_heartbeat.register(
        "daily", expected_interval_seconds=24 * 60 * 60, tolerance_factor=1.5
    )
    now = datetime.now(timezone.utc)

    # 一小時後：60 秒週期的早該回報了，每日的還在正常範圍內
    stale = scheduler_heartbeat.stale(now + timedelta(hours=1))
    assert [name for name, _age in stale] == ["med"]


def test_snapshot_exposes_age_and_threshold():
    scheduler_heartbeat.register(
        "med", expected_interval_seconds=60, tolerance_factor=3.0
    )
    snap = scheduler_heartbeat.snapshot(
        datetime.now(timezone.utc) + timedelta(seconds=30)
    )
    assert snap["med"]["threshold_seconds"] == 180.0
    assert 29.0 <= snap["med"]["age_seconds"] <= 31.0


def test_register_rejects_non_positive_interval():
    with pytest.raises(ValueError):
        scheduler_heartbeat.register("bad", expected_interval_seconds=0)
