"""行程內的請求頻率限制（sliding window log）。

**這是每個行程各自計數的。** backend 目前跑 2 個 replica、Traefik 輪流分配，
所以實際的上限是設定值的 2 倍。現階段可以接受的理由：

- 這四支端點的限制是**成本上限**（Gemini 呼叫、LINE verify 往返），不是精確
  的配額。抓 2 倍以內的誤差對成本沒有影響，對真正的濫用（每秒數十次）仍然
  攔得住。
- 共用計數要靠 Redis。Redis 目前只放對話快取，把它拉進認證路徑等於讓登入
  多一個失敗點，而 CARE 還在開發階段、真實使用者很少（memory
  care-dev-stage-few-real-users），這個代價現在不值得付。

replica 數變多或要做精確配額時，把 `RateLimiter.hit` 換成 Redis 的
INCR + EXPIRE 即可，呼叫端不必動。

刻意不引入 slowapi 之類的套件：需要的只有一個 deque 和一個 dict。
"""

import math
import threading
import time
from collections import OrderedDict, deque
from typing import Callable, Deque, Optional

from fastapi import HTTPException, Request

RATE_LIMITED_DETAIL = "請求過於頻繁，請稍後再試"

# 每個 limiter 最多記幾個 key。超過時淘汰最久沒動的那個（LRU）。
#
# 記憶體是有界的：每個 key 最多 `limit` 個 float，10,000 個 key × 20 個
# timestamp × 8 bytes ≈ 1.6 MB，遠低於 backend 768Mi 上限裡的任何噪音
# （memory care-backend-memory-limit）。被淘汰的 key 等於重新計數，攻擊者
# 要靠這一點繞過，得先用一萬個不同的 IP 把表塞滿——那時候該擋的是
# Cloudflare，不是這裡。
DEFAULT_MAX_KEYS = 10_000


class RateLimiter:
    """固定 `limit` 次／`window_seconds` 秒的 sliding window。

    `clock` 可注入，讓測試不必真的等視窗過去（專案慣例：以依賴注入取代
    monkey patch）。
    """

    def __init__(
        self,
        limit: int,
        window_seconds: float,
        max_keys: int = DEFAULT_MAX_KEYS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit <= 0:
            raise ValueError("limit 必須大於 0：0 等於整支端點關閉，那該用路由層做")
        if window_seconds <= 0:
            raise ValueError("window_seconds 必須大於 0")
        self.limit = limit
        self.window_seconds = float(window_seconds)
        self._max_keys = max_keys
        self._clock = clock
        self._hits: "OrderedDict[str, Deque[float]]" = OrderedDict()
        # dependency 是 async def、跑在事件迴圈上，但 TestClient 與任何同步
        # 呼叫端可能從別的執行緒進來；鎖的成本是奈秒級，不值得省。
        self._lock = threading.Lock()

    def hit(self, key: str) -> Optional[float]:
        """記一次請求。超過上限時回傳「還要等幾秒」，否則回 None。

        超限的那一次**不計入**：否則持續重試的客戶端會把自己永遠鎖在外面，
        Retry-After 也就永遠不準。
        """
        now = self._clock()
        cutoff = now - self.window_seconds
        with self._lock:
            bucket = self._hits.get(key)
            if bucket is None:
                bucket = deque()
                self._hits[key] = bucket
            else:
                self._hits.move_to_end(key)
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.limit:
                return max(bucket[0] + self.window_seconds - now, 0.0)
            bucket.append(now)
            while len(self._hits) > self._max_keys:
                self._hits.popitem(last=False)
        return None

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()

    def enforce(self, key: str) -> None:
        """超限時拋 429，並帶 Retry-After（整數秒、無條件進位）。"""
        retry_after = self.hit(key)
        if retry_after is None:
            return
        raise HTTPException(
            status_code=429,
            detail=RATE_LIMITED_DETAIL,
            headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
        )


def client_ip(request: Request) -> str:
    """未登入請求的計數鍵。

    正式環境的鏈是 Cloudflare → Traefik → backend。Cloudflare 會把真實來源放在
    `CF-Connecting-IP`；`X-Forwarded-For` 的第一段是 Cloudflare 看到的 XFF——
    客戶端可以自己先塞一個假值進去，所以只在沒有 CF 標頭時才退而用它。
    直接打 origin 的請求兩個標頭都可以偽造，但 origin 只接受 Cloudflare 的
    Origin 憑證流量（memory care-gcp-vm），不在這裡的威脅模型內。
    """
    cf_ip = request.headers.get("cf-connecting-ip")
    if cf_ip:
        return cf_ip.strip()
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    if request.client is not None and request.client.host:
        return request.client.host
    return "unknown"
