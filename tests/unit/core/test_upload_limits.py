"""直接在 ASGI 層驗證 MaxUploadSizeMiddleware。

不透過 TestClient／完整的 FastAPI app：TestClient 底層的 httpx transport
會先把整個 multipart body 組好、算出正確的 Content-Length 才送出，沒辦法
模擬「宣稱的長度是謊言」或「body 分成好幾個 ASGI 訊息陸續送達」這兩種
情境，而這兩種正是這個 middleware 存在的理由。這裡自己組 scope／receive／
send，才能精確地斷言：宣稱長度超過上限時，下游的 receive() 完全沒被呼叫過
（body 真的沒有被讀取）；即使宣稱長度騙人，一旦累計位元組超過上限，
下游也不會再收到更多真正的 body 資料。
"""

import pytest

from app.core.upload_limits import MaxUploadSizeMiddleware

PATH = "/api/medications/prescription-scan"
METHOD = "POST"
MAX_BYTES = 100


def _scope(*, content_length: int | None, path: str = PATH, method: str = METHOD) -> dict:
    headers = []
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))
    return {"type": "http", "path": path, "method": method, "headers": headers}


class _RecordingDownstreamApp:
    """記錄自己實際被要求 receive() 幾次、收到多少真正的 body 位元組，
    以及有沒有嘗試送出自己的回應。用來斷言「下游完全沒被呼叫」或
    「下游沒有收到超過上限之後的資料」，而不是只看最終狀態碼。"""

    def __init__(self, respond: bool = False):
        self.receive_calls = 0
        self.received_bytes = b""
        self.saw_disconnect = False
        self.respond = respond
        self.sent_messages: list[dict] = []

    async def __call__(self, scope, receive, send):
        while True:
            self.receive_calls += 1
            message = await receive()
            if message["type"] == "http.disconnect":
                self.saw_disconnect = True
                break
            self.received_bytes += message.get("body") or b""
            if not message.get("more_body", False):
                break
        if self.respond:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            self.sent_messages.append({"type": "http.response.start", "status": 200})
            await send({"type": "http.response.body", "body": b"ok"})


class _RecordingSend:
    def __init__(self):
        self.messages: list[dict] = []

    async def __call__(self, message):
        self.messages.append(message)

    @property
    def status(self) -> int | None:
        for message in self.messages:
            if message["type"] == "http.response.start":
                return message["status"]
        return None

    @property
    def body(self) -> bytes:
        return b"".join(
            message.get("body", b"")
            for message in self.messages
            if message["type"] == "http.response.body"
        )


@pytest.mark.asyncio
async def test_rejects_immediately_when_declared_content_length_exceeds_the_limit():
    downstream = _RecordingDownstreamApp()
    middleware = MaxUploadSizeMiddleware(
        downstream, path=PATH, method=METHOD, max_bytes=MAX_BYTES
    )
    send = _RecordingSend()

    async def receive():  # pragma: no cover - 不該被呼叫到
        raise AssertionError("宣稱長度已經超過上限，不該再去讀 body")

    await middleware(_scope(content_length=MAX_BYTES + 1), receive, send)

    assert send.status == 413
    assert downstream.receive_calls == 0
    assert downstream.received_bytes == b""


@pytest.mark.asyncio
async def test_passes_through_when_declared_content_length_is_within_the_limit():
    downstream = _RecordingDownstreamApp(respond=True)
    middleware = MaxUploadSizeMiddleware(
        downstream, path=PATH, method=METHOD, max_bytes=MAX_BYTES
    )
    send = _RecordingSend()
    body = b"x" * 10
    sent = {"done": False}

    async def receive():
        if sent["done"]:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent["done"] = True
        return {"type": "http.request", "body": body, "more_body": False}

    await middleware(_scope(content_length=len(body)), receive, send)

    assert downstream.received_bytes == body
    assert send.status == 200


@pytest.mark.asyncio
async def test_stops_forwarding_body_once_the_streamed_total_exceeds_the_limit_even_if_declared_length_lies():
    """宣稱的 Content-Length 在上限之內（甚至完全缺席），但實際串流進來的
    位元組總量超過上限：下游收到的真正 body 位元組必須被截在門檻附近，
    不能因為 header 說謊就被迫繼續累積到攻擊者想送的任意大小。"""
    downstream = _RecordingDownstreamApp(respond=True)
    middleware = MaxUploadSizeMiddleware(
        downstream, path=PATH, method=METHOD, max_bytes=MAX_BYTES
    )
    send = _RecordingSend()

    chunk = b"x" * 40  # 3 個 chunk 共 120 bytes，超過 MAX_BYTES=100
    chunks_sent = {"count": 0}

    async def receive():
        if chunks_sent["count"] < 3:
            chunks_sent["count"] += 1
            return {"type": "http.request", "body": chunk, "more_body": True}
        # 理論上不該再被呼叫到第 4 次：一旦累計超過上限，middleware 應該
        # 直接回報 disconnect，不再向這個 receive 要更多資料。
        raise AssertionError("超過上限後，middleware 不該再向底層要更多 body")

    # 沒有宣稱 Content-Length（模擬 chunked transfer encoding 或直接騙人
    # 不帶這個 header），逼迫 middleware 只能靠邊收邊算來偵測。
    await middleware(_scope(content_length=None), receive, send)

    # 下游最多只會看到累計超過門檻「當下那個 chunk」為止的資料——
    # 也就是最多 2 個 chunk（80 bytes，尚未超過）加上讓總量跨過門檻的
    # 第 3 個 chunk（累計到 120 才超過 100），不會是 3 個 chunk 之後
    # middleware 還繼續餵給下游第 4、第 5…個 chunk。
    assert chunks_sent["count"] <= 3
    assert len(downstream.received_bytes) <= len(chunk) * 3
    assert downstream.saw_disconnect
    # 最終回應由 middleware 自己送出的 413 蓋過，不是下游原本想送的 200。
    assert send.status == 413


@pytest.mark.asyncio
async def test_ignores_requests_to_other_paths_or_methods():
    downstream = _RecordingDownstreamApp(respond=True)
    middleware = MaxUploadSizeMiddleware(
        downstream, path=PATH, method=METHOD, max_bytes=MAX_BYTES
    )
    send = _RecordingSend()
    body = b"x" * (MAX_BYTES + 50)

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    await middleware(
        _scope(content_length=len(body), path="/api/medications/reminders", method="GET"),
        receive,
        send,
    )

    # 不是我們要限制的那條路徑／方法，完全放行，不擋。
    assert downstream.received_bytes == body
    assert send.status == 200


@pytest.mark.asyncio
async def test_non_http_scope_is_passed_through_untouched():
    downstream = _RecordingDownstreamApp()
    middleware = MaxUploadSizeMiddleware(
        downstream, path=PATH, method=METHOD, max_bytes=MAX_BYTES
    )
    called = {"value": False}

    async def app(scope, receive, send):
        called["value"] = True

    middleware._app = app
    await middleware({"type": "lifespan"}, None, None)

    assert called["value"] is True
