"""背景排程器的心跳登記簿。

存在的理由：排程器與 API 拆成不同 pod 之後，K8s 需要一個方式判斷排程器 pod
還活著。但「HTTP 有回應」不等於「排程器還在跑」——排程器是事件迴圈上的一個
asyncio task，即使它整個停掉，uvicorn 仍然會正常回應 `/health`，探針完全看不
出異常，而用藥提醒已經停止推播且錯過的時段不會補推。

因此健康的定義必須是「最近一次 tick 距今多久」，由各排程器主動回報。

每個排程器登記自己的預期間隔，因為兩者差距很大：用藥提醒每 60 秒跑一次，
每日諮詢摘要則睡到隔天的排程時刻。用同一個門檻會讓後者永遠被判為停擺。

執行緒安全：以 threading.Lock 保護。排程器都在事件迴圈上呼叫 beat()，但健康
檢查端點可能由 uvicorn 的工作執行緒讀取，兩者不保證同一條執行緒。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass(frozen=True)
class _Registration:
    expected_interval_seconds: float
    tolerance_factor: float


_lock = threading.Lock()
_registrations: dict[str, _Registration] = {}
_last_beat: dict[str, datetime] = {}


def register(
    name: str,
    expected_interval_seconds: float,
    tolerance_factor: float = 3.0,
) -> None:
    """登記一個排程器，並把登記當下視為第一次心跳。

    把登記時間當成第一次心跳，是為了讓「剛啟動、還沒跑完第一輪」不被誤判為
    停擺。容忍倍數預設 3：偶爾一次 tick 因為資料庫變慢而延遲不該觸發重啟，
    連續三輪都沒有回報才是真的有問題。
    """
    if expected_interval_seconds <= 0:
        raise ValueError("expected_interval_seconds 必須為正數")
    with _lock:
        _registrations[name] = _Registration(
            expected_interval_seconds=expected_interval_seconds,
            tolerance_factor=tolerance_factor,
        )
        _last_beat[name] = datetime.now(timezone.utc)


def beat(name: str) -> None:
    """回報一次心跳。未登記的名稱一律忽略，不拋例外。

    忽略而非拋錯：心跳是觀測用的旁路，它自己絕不能成為讓排程器崩潰的原因。
    """
    with _lock:
        if name in _registrations:
            _last_beat[name] = datetime.now(timezone.utc)


def stale(now: Optional[datetime] = None) -> list[tuple[str, float]]:
    """回傳超過各自容忍時間未回報心跳的排程器與其停擺秒數。

    `now` 可注入，讓測試不必真的等待時間流逝。
    """
    current = now or datetime.now(timezone.utc)
    result: list[tuple[str, float]] = []
    with _lock:
        for name, registration in _registrations.items():
            last = _last_beat.get(name)
            if last is None:
                continue
            age = (current - last).total_seconds()
            threshold = (
                registration.expected_interval_seconds * registration.tolerance_factor
            )
            if age > threshold:
                result.append((name, age))
    return sorted(result)


def snapshot(now: Optional[datetime] = None) -> dict[str, dict[str, float]]:
    """回傳每個排程器的心跳年齡與門檻，供健康檢查端點揭露細節。"""
    current = now or datetime.now(timezone.utc)
    with _lock:
        return {
            name: {
                "age_seconds": round((current - _last_beat[name]).total_seconds(), 1),
                "threshold_seconds": round(
                    registration.expected_interval_seconds
                    * registration.tolerance_factor,
                    1,
                ),
            }
            for name, registration in _registrations.items()
            if name in _last_beat
        }


def registered() -> list[str]:
    with _lock:
        return sorted(_registrations)


def reset() -> None:
    """清空登記簿。僅供測試在案例之間隔離狀態使用。"""
    with _lock:
        _registrations.clear()
        _last_beat.clear()
