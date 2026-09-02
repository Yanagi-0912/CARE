"""參考來源網址的存活檢查（citation liveness）。

為什麼需要：知識庫的 chunk 帶著 ingest 當下的 url，而 url 會 rot——政府衛教
站台改版、子系統除役、路徑重整，庫裡的 metadata 不會跟著變。答案本文仍然
正確（內容是抓下來存的），但附上去的來源按鈕點下去是死的。對衛教問答而言
這比沒有來源更糟：使用者無法驗證，而「附了來源」本身就是一種可信度宣稱。

實測案例（2026-09-02）：sp1.hso.mohw.gov.tw 整台 TCP 不通（80/443 皆
timeout），DNS 仍解析得到 210.241.104.139；Wayback 對該站 /doctor/ 路徑
2015–2022 的抓取全部是 404。這種來源不會被任何既有機制擋下——白名單看的是
網域後綴（gov.tw 通過），CRAG 看的是內容相關性，兩者都不碰「這個 url 現在
還在不在」。

檢查放在**回答出口**而不是 ingest：link rot 的定義就是「入庫當下是好的，
後來壞掉」，ingest 端檢查依定義抓不到這件事。代價是把 HTTP 往返放進使用者
等待路徑，用 TTL 快取與嚴格逾時把它壓到可接受（見 LinkChecker 的參數說明）。

信任鏈用 `app.core.ca_bundle`（certifi ＋ 釘選的 TWCA 中繼憑證），否則
www.mohw.gov.tw 與 www.hpa.gov.tw 會直接驗不過。即便如此，**TLS 驗證失敗
仍一律不判死**——見 `_is_tls_failure`。兩者是互補而不是二選一：bundle 讓
我們對多數 gov.tw 站台**有能力**判斷，不判死則是對剩下那些仍驗不過的站台
保持安全。
"""

from __future__ import annotations

import asyncio
import logging
import ssl
import time
from collections import OrderedDict
from collections.abc import Iterable

import httpx

from app.core.ca_bundle import get_ca_bundle

logger = logging.getLogger(__name__)

# 逾時要短。這條路徑掛在使用者等待上（LINE reply token 有 30s 上限，而
# 這之前已經花掉檢索＋精排＋生成的時間），不能為了問一個網址拖垮整輪。
#
# 逾時是全模組唯一「證據不足仍判死」的地方——其他不確定的情況（403、5xx、
# 重導迴圈、TLS 驗不過）一律放行。留這個例外是因為逾時同時也是延遲問題：
# 連不上的站台使用者一樣打不開，而我們又不能一直等下去。誤判由 DEAD_TTL
# 的短週期重試補回，見下。
DEFAULT_TIMEOUT_SECONDS = 3.0
# 活著的結果快取久一點：來源網址在庫裡是穩定值，同一批熱門問題會反覆命中。
DEFAULT_OK_TTL_SECONDS = 86400.0
# 判死與判不出來共用這個較短的 TTL。兩者都可能只是暫時的（站台維護、我方
# 出口網路抖動、對方限流），TTL 短才能讓狀況恢復後很快重新顯示連結。
# 這是「逾時即判死」那個唯一例外的補償機制，不是可有可無的調校。
DEFAULT_DEAD_TTL_SECONDS = 600.0
# 一輪最多 CITE_TOP_K（3）個網址，併發上限主要是防呼叫端一次丟進大量網址。
DEFAULT_MAX_CONCURRENCY = 5
DEFAULT_MAX_CACHE_ENTRIES = 2048
# 判死前的確認重試間隔。判死是全模組唯一會改變使用者看到的東西的判定，
# 而 404 並不像它看起來那麼確定：
#
# 2026-09-02 全庫稽核（2420 個網址）第一次跑出 1 個 404，隔一輪重跑同一個
# 網址變成 200，逐一間隔 2 秒手測三次全部 200——www.fda.gov.tw 在被打快時
# 會回假的 404 而不是 429。若照單全收，使用者就會平白少掉一個好連結，而且
# 沒有人會發現（沒人回報「有個連結沒出現」）。
#
# 只對 HTTP 狀態碼判死做這個確認。逾時那條路徑不重試：它已經等滿逾時，
# 再來一次會讓最壞情況翻倍，而那條路徑的誤判本來就由 DEAD_TTL 補回。
DEFAULT_CONFIRM_DELAY_SECONDS = 0.5
# 整批檢查的總上限。None＝由 timeout 推導（見 __init__）。
#
# 為什麼單次逾時不夠：一個網址最多會打四次 HTTP——HEAD 被擋就退 GET，
# 判死後再確認一輪（HEAD＋GET）。而 httpx 的 timeout 是 per-request，
# 所以「單次 3 秒」實際可以累積到 12 秒以上。實測 HEAD 403 → GET 2.9s
# → 404 → 確認再一輪 = 6.31s，兩倍於宣稱值。
#
# 這條路徑掛在使用者等待上，而整條 RAG 管線目前沒有任何一段有總逾時
# （另一個 session 實測撞到 rag_retrieve 卡 94 秒才回 0 筆，使用者等
# 107 秒換一句「查無資料」）。這裡不重複那個失效模式。
#
# 超出預算的網址視為「判不出來」而非死——與其他所有不確定情況一致，
# 照常顯示連結。已完成的判定照常採用，不會因為同批有人慢就整批放棄。
DEFAULT_TOTAL_BUDGET_SECONDS: float | None = None

# 判死的門檻刻意訂得很高：**只有這兩個狀態碼算「資源確定不存在」**。
#
# 這個取捨來自失效代價不對稱。誤殺（好來源被藏起來）沒有任何人會回報——
# 不會有使用者說「有個連結沒出現」，我們也就永遠不知道自己藏錯了；漏網
# （死連結照樣顯示）則只是退回導入這個功能之前的現狀。所以寧可漏，不可殺。
_DEAD_STATUSES = frozenset({404, 410})
# 這些代表「伺服器活著但不想理我們」或「不接受 HEAD」，不是資源不存在。
# 改用 GET 再確認一次——有些站台只擋 HEAD，GET 是正常的。
_RETRY_WITH_GET_STATUSES = frozenset({401, 403, 405, 406, 429, 501})

USER_AGENT = "CARE-citation-check/1.0 (+https://care.jamessu2016.com)"

# 快取的第三態：判不出來。與「快取沒有這一筆」必須分得開，所以用哨兵物件
# 而不是 None——None 本身就是「判不出來」的值。
_MISS = object()


def _is_tls_failure(exc: BaseException) -> bool:
    """這個例外是不是 TLS／憑證驗證失敗。

    為什麼要特別放行：憑證驗證失敗說的是「**我們這個行程**不信任這條鏈」，
    不是「使用者打不開這一頁」。兩邊的信任鏈本來就不一樣——使用者從 LINE
    點出去用的是手機作業系統的 CA store，我們用的是 Python 的 certifi。

    實測（2026-09-02，macOS + certifi）：
        www.mohw.gov.tw → CERTIFICATE_VERIFY_FAILED: Missing Subject Key Identifier
        www.hpa.gov.tw  → CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate
        同一台機器上 curl（走系統 keychain）→ 302／403，兩站都活著。
    這兩站只送 leaf 憑證、不附中繼憑證，瀏覽器會依 AIA 自動補抓而 Python 不會。
    `app.core.ca_bundle` 釘選那張中繼憑證後兩站都通了（mohw 200、hpa 403）。

    那為什麼還留著這條規則？因為 bundle 解決的是「我們有沒有能力驗」，這條
    規則管的是「驗不過時怎麼辦」。憑證到期、certifi 移除 TWCA 根憑證、或又
    冒出一個同樣不附中繼憑證的新站台，都會讓某批網址重新驗不過——那時的
    正確行為仍然是照常顯示，而不是把一整批官方來源默默藏起來。

    代價是抓不到「憑證真的過期」的站台。那是可以接受的漏網：憑證過期的頁面
    使用者仍然點得進去（瀏覽器出警告後可續行），內容也還在，與這裡要解決的
    「點下去什麼都沒有」不是同一個問題。
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ssl.SSLError):
            return True
        current = current.__cause__ or current.__context__
    return False


class LinkChecker:
    """判斷一組網址現在是否可存取，帶 TTL 快取。

    刻意做成 in-process 快取而不是 Redis：這份資料是可重建的純快取，遺失
    的代價只是多打幾次 HEAD，為它引入一個會失敗的外部依賴不划算。多副本
    部署時每個 pod 各自持有一份，只是快取命中率低一些，正確性不受影響。
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        ok_ttl_seconds: float = DEFAULT_OK_TTL_SECONDS,
        dead_ttl_seconds: float = DEFAULT_DEAD_TTL_SECONDS,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
        max_cache_entries: int = DEFAULT_MAX_CACHE_ENTRIES,
        confirm_delay_seconds: float = DEFAULT_CONFIRM_DELAY_SECONDS,
        total_budget_seconds: float | None = DEFAULT_TOTAL_BUDGET_SECONDS,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._ok_ttl_seconds = ok_ttl_seconds
        self._dead_ttl_seconds = dead_ttl_seconds
        self._max_concurrency = max(1, max_concurrency)
        self._max_cache_entries = max(1, max_cache_entries)
        self._confirm_delay_seconds = max(0.0, confirm_delay_seconds)
        # 預設容得下「HEAD ＋ GET fallback」各一次逾時再加確認的間隔，
        # 也就是一個網址的正常最壞路徑；真正病態的第二輪會被這道閘擋掉。
        self._total_budget_seconds = (
            total_budget_seconds
            if total_budget_seconds is not None
            else timeout_seconds * 2 + self._confirm_delay_seconds
        )
        self._http_client = http_client
        # url -> (判定, 到期時間)。判定為 None 代表「查過但判不出來」，
        # 那也值得快取——不然每一輪都會為同一個 TLS 驗證不過的網址重打一次。
        # OrderedDict 當 LRU 用，避免長時間執行的行程無上限累積網址。
        self._cache: OrderedDict[str, tuple[bool | None, float]] = OrderedDict()

    async def alive(self, urls: Iterable[str]) -> dict[str, bool]:
        """回傳 {url: 是否可存取}。

        只包含判得出結果的網址。**缺項代表「判不出來」而非「死了」**——
        呼叫端必須把缺項當成可用。會缺項的情況有兩種：TLS 驗證不過
        （見 `_is_tls_failure`），以及檢查器自己出錯（見 `_check_many`）。
        """
        unique: list[str] = []
        seen: set[str] = set()
        for raw in urls:
            url = (raw or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            unique.append(url)
        if not unique:
            return {}

        now = time.monotonic()
        result: dict[str, bool] = {}
        pending: list[str] = []
        for url in unique:
            cached = self._cache_get(url, now)
            if cached is _MISS:
                pending.append(url)
            elif cached is not None:
                # 快取住的「判不出來」同樣不進結果，呼叫端當成可用。
                result[url] = bool(cached)

        if pending:
            result.update(await self._check_many(pending))
        return result

    async def _check_many(self, urls: list[str]) -> dict[str, bool]:
        client = self._http_client or httpx.AsyncClient(
            timeout=self._timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
            # certifi ＋ 釘選的 TWCA 中繼憑證。少了它，www.mohw.gov.tw 與
            # www.hpa.gov.tw（庫裡 42% 的來源）會全部落進「判不出來」。
            verify=get_ca_bundle(),
        )
        owns_client = self._http_client is None
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def check(url: str) -> tuple[str, bool | None]:
            async with semaphore:
                return url, await self._check_one(client, url)

        # 依 url 建索引而不是只留一個 list：`asyncio.wait` 回傳的 done 是
        # 無序 set，直接照它的順序寫快取會讓 LRU 驅逐掉的不是最舊的那筆。
        # 下面一律依 *urls* 的原順序收集。
        task_by_url = {url: asyncio.create_task(check(url)) for url in urls}
        try:
            _, pending = await asyncio.wait(
                task_by_url.values(), timeout=self._total_budget_seconds
            )
            if pending:
                # 撞到總預算。取消未完成的，但保留已經有答案的那些——
                # 被取消的不寫入結果也不寫快取，於是呼叫端當成「判不出來」
                # 而照常顯示連結（安全方向）。
                logger.info(
                    "link_check_budget_exceeded pending=%d of=%d budget=%.1fs",
                    len(pending),
                    len(urls),
                    self._total_budget_seconds,
                )
                for task in pending:
                    task.cancel()
                # 等取消真正生效再關 client，否則收尾中的請求會撞到
                # 已關閉的連線池，噴出與這件事無關的例外。
                await asyncio.gather(*pending, return_exceptions=True)
            # 不能直接 task.result()：那會把 check() 自己的例外重新拋出來，
            # 而下面的迴圈本來就要把它當成一筆「判不出來」處理。
            settled: list[object] = []
            for url in urls:
                task = task_by_url[url]
                if task.cancelled() or not task.done():
                    continue
                error = task.exception()
                settled.append(error if error is not None else task.result())
        finally:
            if owns_client:
                await client.aclose()

        out: dict[str, bool] = {}
        now = time.monotonic()
        for item in settled:
            if item is None:
                continue
            if isinstance(item, BaseException):
                # `_check_one` 已經吃掉所有 httpx 例外，走到這裡代表檢查器
                # 自己出了問題（例如 semaphore 被取消）。那是我們的故障，
                # 不是來源的故障，不該拿來懲罰來源——這一筆不寫入結果，
                # 呼叫端會當成「判不出來」而照常顯示連結。這與逾時被判死
                # 並不矛盾：逾時是關於**那個網址**的證據，這裡沒有證據。
                logger.warning("link_check_task_failed error=%r", item)
                continue
            url, verdict = item
            self._cache_put(url, verdict, now)
            if verdict is not None:
                out[url] = verdict
        return out

    async def _check_one(
        self, client: httpx.AsyncClient, url: str
    ) -> bool | None:
        """True＝可存取，False＝確定打不開，None＝判不出來（照常顯示）。

        狀態碼判死時會再確認一次，見 DEFAULT_CONFIRM_DELAY_SECONDS。
        """
        verdict, worth_confirming = await self._probe(client, url)
        if not worth_confirming or self._confirm_delay_seconds <= 0:
            return verdict
        # 只有狀態碼判死才走到這裡，而判死本身很罕見（實測全庫 0/2420），
        # 所以這個確認幾乎不影響一般路徑的延遲。
        await asyncio.sleep(self._confirm_delay_seconds)
        confirmed, _ = await self._probe(client, url)
        if confirmed is not False:
            logger.info("link_check_dead_unconfirmed url=%s", url)
            return None
        return False

    async def _probe(
        self, client: httpx.AsyncClient, url: str
    ) -> tuple[bool | None, bool]:
        """回傳（判定, 這個判定值不值得再確認一次）。

        只有「伺服器明確回了 404／410」值得確認——那是唯一可能造假的判死
        訊號（見 DEFAULT_CONFIRM_DELAY_SECONDS 的實測）。連不上造成的判死
        不值得：它已經等滿逾時，重試只會讓最壞情況翻倍。
        """
        try:
            response = await client.head(url, timeout=self._timeout_seconds)
            status = response.status_code
            if status in _RETRY_WITH_GET_STATUSES:
                # 用 stream 打 GET：拿到 header 就離開 context，不下載 body。
                async with client.stream(
                    "GET", url, timeout=self._timeout_seconds
                ) as streamed:
                    status = streamed.status_code
        except httpx.TooManyRedirects:
            # 重導向迴圈幾乎都是站台想先發 cookie／導去登入，而我們不帶
            # cookie 也不執行 JS，於是繞不出來。真實瀏覽器不會卡在這裡。
            # 實測 www.nhi.gov.tw：HEAD 回 403，改打 GET 就進迴圈——站台
            # 本身是活的。
            logger.info("link_check_redirect_loop url=%s", url)
            return None, False
        except Exception as exc:
            if _is_tls_failure(exc):
                # 我們的信任鏈不認得，不等於使用者打不開——見 _is_tls_failure。
                logger.info("link_check_tls_unverified url=%s", url)
                return None, False
            # 逾時、DNS 失敗、連線被拒——視為不可用。對使用者來說「點了
            # 打不開」與「回 404」的結果完全一樣，沒有理由只擋後者。
            # 暫時性故障造成的誤殺由 DEAD_TTL 的短週期重試補回，不在這裡
            # 重試：已經等滿逾時了。
            logger.info(
                "link_check_unreachable url=%s error=%s", url, type(exc).__name__
            )
            return False, False

        if status < 400:
            return True, False
        if status in _DEAD_STATUSES:
            logger.info("link_check_dead url=%s status=%s", url, status)
            return False, True
        # 其餘 4xx／5xx 一律判不出來。403／429 是我們被 WAF 或限流擋掉
        # （實測 www.nhi.gov.tw、www.hpa.gov.tw 對 curl 也回 403，但瀏覽器
        # 打得開）；5xx 多半是暫時故障。這些都不構成「資源不存在」的證據。
        logger.info("link_check_inconclusive url=%s status=%s", url, status)
        return None, False

    def _cache_get(self, url: str, now: float) -> bool | None | object:
        """回傳快取判定；`_MISS` 代表沒有可用的快取（未存過或已過期）。"""
        entry = self._cache.get(url)
        if entry is None:
            return _MISS
        verdict, expires_at = entry
        if now >= expires_at:
            self._cache.pop(url, None)
            return _MISS
        self._cache.move_to_end(url)
        return verdict

    def _cache_put(self, url: str, verdict: bool | None, now: float) -> None:
        # 判不出來與判死共用短 TTL：兩者都可能是暫時的，都該早點再試。
        ttl = self._ok_ttl_seconds if verdict is True else self._dead_ttl_seconds
        self._cache[url] = (verdict, now + ttl)
        self._cache.move_to_end(url)
        while len(self._cache) > self._max_cache_entries:
            self._cache.popitem(last=False)


async def dead_urls(checker: LinkChecker | None, urls: Iterable[str]) -> frozenset[str]:
    """回傳其中判定為不可存取的網址集合。

    `checker` 為 None（功能關閉）或整個檢查流程拋例外時回空集合——降級方向
    是「照舊顯示所有連結」，也就是完全退回導入本功能之前的行為。這一層的
    失敗是我們的失敗，不該讓使用者連正常的來源都看不到。
    """
    candidates = [u for u in ((raw or "").strip() for raw in urls) if u]
    if checker is None or not candidates:
        return frozenset()
    try:
        statuses = await checker.alive(candidates)
    except Exception:
        logger.exception("link_check_failed; showing all sources unchecked")
        return frozenset()
    # 只收明確判死的。不在 statuses 裡的是「判不出來」，維持可用。
    return frozenset(url for url, ok in statuses.items() if not ok)
