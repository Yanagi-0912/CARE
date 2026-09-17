"""行程內頻率限制：sliding window、Retry-After、有界記憶體。時鐘一律注入。"""

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.core.rate_limit import RateLimiter, client_ip


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


def test_requests_within_the_limit_pass():
    limiter = RateLimiter(limit=3, window_seconds=60, clock=Clock())
    assert [limiter.hit("k") for _ in range(3)] == [None, None, None]


def test_the_request_over_the_limit_reports_how_long_to_wait():
    clock = Clock(1000.0)
    limiter = RateLimiter(limit=2, window_seconds=60, clock=clock)
    limiter.hit("k")
    clock.now = 1010.0
    limiter.hit("k")
    clock.now = 1020.0
    # 最早那次在 1000，視窗到 1060，還要等 40 秒
    assert limiter.hit("k") == pytest.approx(40.0)


def test_window_slides_instead_of_resetting():
    clock = Clock(1000.0)
    limiter = RateLimiter(limit=2, window_seconds=60, clock=clock)
    limiter.hit("k")
    clock.now = 1030.0
    limiter.hit("k")
    clock.now = 1061.0  # 第一次已滑出視窗，第二次還在
    assert limiter.hit("k") is None
    assert limiter.hit("k") is not None


def test_rejected_requests_do_not_extend_the_lockout():
    """持續重試的客戶端不該把自己永遠鎖在外面。"""
    clock = Clock(1000.0)
    limiter = RateLimiter(limit=1, window_seconds=60, clock=clock)
    limiter.hit("k")
    for t in (1010.0, 1020.0, 1030.0):
        clock.now = t
        assert limiter.hit("k") is not None
    clock.now = 1061.0
    assert limiter.hit("k") is None


def test_keys_are_independent():
    limiter = RateLimiter(limit=1, window_seconds=60, clock=Clock())
    assert limiter.hit("a") is None
    assert limiter.hit("b") is None
    assert limiter.hit("a") is not None


def test_memory_is_bounded_by_evicting_the_least_recently_used_key():
    limiter = RateLimiter(limit=1, window_seconds=60, max_keys=2, clock=Clock())
    limiter.hit("a")
    limiter.hit("b")
    limiter.hit("c")  # 把 a 擠掉
    assert len(limiter._hits) == 2
    assert "a" not in limiter._hits
    assert limiter.hit("a") is None  # 被擠掉等於重新計數


def test_enforce_raises_429_with_integer_retry_after():
    clock = Clock(1000.0)
    limiter = RateLimiter(limit=1, window_seconds=60, clock=clock)
    limiter.enforce("k")
    clock.now = 1000.5
    with pytest.raises(HTTPException) as exc:
        limiter.enforce("k")
    assert exc.value.status_code == 429
    assert exc.value.headers["Retry-After"] == "60"  # 59.5 無條件進位


def test_reset_clears_every_key():
    limiter = RateLimiter(limit=1, window_seconds=60, clock=Clock())
    limiter.hit("k")
    limiter.reset()
    assert limiter.hit("k") is None


@pytest.mark.parametrize("bad", [dict(limit=0, window_seconds=1), dict(limit=1, window_seconds=0)])
def test_degenerate_limits_are_rejected(bad):
    with pytest.raises(ValueError):
        RateLimiter(**bad)


# ── 來源 IP ──────────────────────────────────────────────────────────


def make_request(headers=None, client=("10.0.0.9", 1234)):
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": raw,
        "client": client,
        "query_string": b"",
    }
    return Request(scope)


def test_cloudflare_header_wins():
    request = make_request(
        {"CF-Connecting-IP": "203.0.113.5", "X-Forwarded-For": "198.51.100.1, 203.0.113.5"}
    )
    assert client_ip(request) == "203.0.113.5"


def test_first_forwarded_hop_when_no_cloudflare_header():
    request = make_request({"X-Forwarded-For": " 198.51.100.1 , 10.0.0.1"})
    assert client_ip(request) == "198.51.100.1"


def test_falls_back_to_the_socket_peer():
    assert client_ip(make_request()) == "10.0.0.9"


def test_unknown_when_nothing_is_available():
    assert client_ip(make_request(client=None)) == "unknown"
