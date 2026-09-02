"""LinkChecker 與 dead_urls 的行為。

全部用 httpx.MockTransport 驅動，不打真實網路：這個模組的重點是「什麼樣的
回應算死」與「快取／降級怎麼走」，不是 httpx 本身。
"""

import ssl

import httpx
import pytest

from app.services.rag.link_check import LinkChecker, dead_urls

ALIVE = "https://www.mohw.gov.tw/alive"
GONE = "https://sp1.hso.mohw.gov.tw/doctor/Often_question/type_detail.php"


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    )


def _checker(handler, **kwargs) -> LinkChecker:
    # 判死前的確認重試預設關掉：多數測試驗的不是它，而每次真的 sleep 會把
    # 整個檔案拖慢好幾秒。專門驗確認行為的測試自己傳 confirm_delay_seconds。
    kwargs.setdefault("confirm_delay_seconds", 0.0)
    return LinkChecker(http_client=_client(handler), **kwargs)


async def test_2xx_is_alive_and_404_is_dead():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200 if request.url.path == "/alive" else 404)

    result = await _checker(handler).alive([ALIVE, GONE])

    assert result == {ALIVE: True, GONE: False}


async def test_timeout_counts_as_dead():
    """實測 sp1.hso.mohw.gov.tw 就是這一類：DNS 解析得到，TCP 連不上。

    對使用者而言「點了打不開」與「回 404」結果相同，沒有理由只擋後者。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    assert await _checker(handler).alive([GONE]) == {GONE: False}


async def test_head_rejected_falls_back_to_get():
    """403/405/501 是「不接受 HEAD」而非「資源不存在」，必須改打 GET。

    少了這一段，會把大量擋 HEAD 的正常站台判死。
    """
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.method)
        if request.method == "HEAD":
            return httpx.Response(405)
        return httpx.Response(200, content=b"ok")

    assert await _checker(handler).alive([ALIVE]) == {ALIVE: True}
    assert seen == ["HEAD", "GET"]


async def test_get_fallback_still_reports_dead_when_get_also_fails():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403 if request.method == "HEAD" else 404)

    assert await _checker(handler).alive([GONE]) == {GONE: False}


@pytest.mark.parametrize("status", [404, 410])
async def test_only_not_found_statuses_are_dead(status):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    assert await _checker(handler).alive([GONE]) == {GONE: False}


@pytest.mark.parametrize("status", [400, 403, 429, 500, 502, 503])
async def test_other_error_statuses_are_inconclusive(status):
    """403／429 是我們被 WAF 或限流擋掉，5xx 多半是暫時故障——都不構成
    「資源不存在」的證據。誤殺沒有人會回報，漏網只是退回現狀。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    assert await _checker(handler).alive([GONE]) == {}


async def test_redirect_loop_is_inconclusive():
    """實測 www.nhi.gov.tw：HEAD 回 403，改打 GET 就進 cookie 重導迴圈，
    但站台本身是活的，瀏覽器打得開。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": str(request.url)})

    assert await _checker(handler).alive([GONE]) == {}


async def test_redirect_to_200_is_alive():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/alive":
            return httpx.Response(301, headers={"Location": "https://example.org/new"})
        return httpx.Response(200)

    assert await _checker(handler).alive([ALIVE]) == {ALIVE: True}


async def test_result_is_cached_within_ttl():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200)

    checker = _checker(handler)
    await checker.alive([ALIVE])
    await checker.alive([ALIVE])

    assert len(calls) == 1


async def test_dead_result_expires_sooner_than_alive_result():
    """判死用短 TTL，站台恢復後才會重新顯示連結——這是「逾時一律判死」
    那個保守取捨的補償機制，不是可有可無的調校。"""
    status = {"code": 404}
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(status["code"])

    checker = _checker(handler, dead_ttl_seconds=0.0)
    assert await checker.alive([GONE]) == {GONE: False}

    status["code"] = 200
    assert await checker.alive([GONE]) == {GONE: True}
    assert len(calls) == 2


async def test_duplicate_and_blank_urls_are_checked_once():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200)

    result = await _checker(handler).alive([ALIVE, ALIVE, "", "   ", None])

    assert result == {ALIVE: True}
    assert len(calls) == 1


async def test_cache_evicts_oldest_beyond_limit():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    checker = _checker(handler, max_cache_entries=2)
    await checker.alive(["https://a.gov.tw/1", "https://a.gov.tw/2", "https://a.gov.tw/3"])

    assert len(checker._cache) == 2
    assert "https://a.gov.tw/1" not in checker._cache


async def test_dead_urls_returns_only_confirmed_dead():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200 if request.url.path == "/alive" else 404)

    assert await dead_urls(_checker(handler), [ALIVE, GONE]) == frozenset({GONE})


async def test_dead_urls_without_checker_returns_empty():
    """功能關閉時完全退回導入前的行為。"""
    assert await dead_urls(None, [ALIVE, GONE]) == frozenset()


async def test_dead_urls_fails_open_when_checker_raises():
    """檢查器自己壞掉是我們的故障，不該讓使用者連正常來源都看不到。"""

    class Exploding:
        async def alive(self, urls):
            raise RuntimeError("boom")

    assert await dead_urls(Exploding(), [ALIVE, GONE]) == frozenset()


# --- TLS 驗證失敗不判死 ---

GOV = "https://www.mohw.gov.tw/"


def _tls_handler(request: httpx.Request) -> httpx.Response:
    """重現實測到的失敗：httpx 把 ssl.SSLError 包成 ConnectError。"""
    raise httpx.ConnectError(
        "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
        "Missing Subject Key Identifier",
        request=request,
    ) from ssl.SSLCertVerificationError("Missing Subject Key Identifier")


async def test_tls_failure_is_not_reported_as_dead():
    """憑證驗證失敗說的是「我們這個行程不信任這條鏈」，不是「使用者打不開」。

    台灣政府憑證不在 certifi 的 root bundle 裡，而白名單主體正是 gov.tw——
    判死等於整批誤殺官方衛教來源。
    """
    assert await _checker(_tls_handler).alive([GOV]) == {}


async def test_tls_failure_yields_no_dead_urls():
    assert await dead_urls(_checker(_tls_handler), [GOV]) == frozenset()


async def test_tls_verdict_is_cached_and_not_retried():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return _tls_handler(request)

    checker = _checker(handler)
    await checker.alive([GOV])
    await checker.alive([GOV])

    assert len(calls) == 1


async def test_tls_failure_uses_short_ttl_like_dead():
    """判不出來與判死同屬「可能是暫時的」，都該早點再試。"""
    fail = {"tls": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if fail["tls"]:
            return _tls_handler(request)
        return httpx.Response(200)

    checker = _checker(handler, dead_ttl_seconds=0.0)
    assert await checker.alive([GOV]) == {}

    fail["tls"] = False
    assert await checker.alive([GOV]) == {GOV: True}


async def test_plain_connect_error_is_still_dead():
    """非 TLS 的連線失敗維持判死——sp1.hso.mohw.gov.tw 就是這一類。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    assert await _checker(handler).alive([GONE]) == {GONE: False}


# --- 判死前的確認重試 ---


async def test_dead_is_confirmed_with_a_second_probe():
    """判死是唯一會改變使用者看到的東西的判定，值得多問一次。"""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(404)

    checker = _checker(handler, confirm_delay_seconds=0.01)

    assert await checker.alive([GONE]) == {GONE: False}
    assert len(calls) == 2


async def test_unconfirmed_dead_becomes_inconclusive():
    """實測 www.fda.gov.tw 在被打快時會回假的 404 而不是 429：全庫稽核第一輪
    跑出 1 個 404，重跑與逐一手測都是 200。照單全收會讓使用者平白少掉一個
    好連結，而且沒有人會發現。"""
    responses = iter([404, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(next(responses))

    assert await _checker(handler, confirm_delay_seconds=0.01).alive([GONE]) == {}


async def test_alive_never_pays_for_the_confirmation():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(200)

    await _checker(handler, confirm_delay_seconds=5.0).alive([ALIVE])

    assert len(calls) == 1


async def test_timeout_is_not_retried():
    """逾時那條路徑不重試：已經等滿逾時，再來一次會讓最壞情況翻倍。"""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        raise httpx.ConnectTimeout("timed out", request=request)

    checker = _checker(handler, confirm_delay_seconds=5.0)

    assert await checker.alive([GONE]) == {GONE: False}
    assert len(calls) == 1
