"""ASGI 層級的請求體大小限制。

**為什麼不能只在路由層檢查大小**：FastAPI 的 `UploadFile = File(...)` 參數
繫結，會在任何路由層程式碼（甚至 `Depends`）執行之前，經由
`fastapi/routing.py` 裡的 `body = await request.form()` 把整個 multipart
body 讀完——而 Starlette 的 multipart parser（`starlette/formparsers.py`
`MultiPartParser.on_part_data`）對「檔案」欄位完全沒有大小限制，
`max_part_size` 只檢查純文字欄位；檔案欄位的資料一律先寫進
`SpooledTemporaryFile` 再說。實測驗證過：在路由函式內部，不管是先讀
`Content-Length` 還是把 `file.read()` 拆成一段一段讀，此時請求體早就已經
被完整接收完畢——這些檢查只決定要不要把已經吃下去的資料丟給辨識服務，
完全無法阻止行程被迫緩衝任意大小的內容。唯一能在「body 還沒被完整接收」
這個時間點介入的位置，只有 ASGI 層：在 Starlette 開始解析 body 之前，
直接檢查、並在超過上限時中止底層的 `receive()` channel。
"""

import json
import logging
from typing import Optional

from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)


class MaxUploadSizeMiddleware:
    """限制指定路徑、指定方法的請求體大小，兩層防護缺一不可：

    1. 宣稱的 `Content-Length` 一旦超過上限就直接拒絕，連一個位元組都不從
       底層 `receive()` 拉取——這是誠實回報長度的客戶端會走的路徑，
       成本最低，也是多數合法過大上傳會命中的情況。
    2. `Content-Length` 完全由客戶端填寫，可以造假，也可以缺席（例如
       chunked transfer encoding 就不會帶這個 header）。因此仍要邊接收邊
       累計實際位元組數；一旦累計超過上限，後續不再從底層 `receive()`
       拉取更多資料，改回報一個 `http.disconnect`，讓下游的 body 解析
       盡快中止——保證行程不會被迫緩衝任意大小的內容，即使 header 說謊。
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        path: str,
        method: str,
        max_bytes: int,
    ) -> None:
        self._app = app
        self._path = path
        self._method = method
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope.get("type") != "http"
            or scope.get("path") != self._path
            or scope.get("method") != self._method
        ):
            await self._app(scope, receive, send)
            return

        declared_length = _declared_content_length(scope)
        if declared_length is not None and declared_length > self._max_bytes:
            await _send_413(send)
            return

        total = 0
        exceeded = False

        async def bounded_receive() -> Message:
            nonlocal total, exceeded
            if exceeded:
                # 已經確定超量：不再向底層要更多資料，直接回報用戶端已斷線，
                # 讓下游的 multipart 解析盡快中止，而不是繼續把後續位元組
                # 寫進暫存檔或記憶體。
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body") or b"")
                if total > self._max_bytes:
                    exceeded = True
            return message

        response_started = False

        async def guarded_send(message: Message) -> None:
            nonlocal response_started
            if exceeded:
                if not response_started:
                    # 不管下游想送什麼（多半是它自己因為 body 被截斷而產生
                    # 的錯誤回應），一律由這裡送出我們自己的 413，且只送
                    # 一次；之後下游任何後續的 send 呼叫都靜默丟棄。
                    response_started = True
                    await _send_413(send)
                return
            await send(message)

        await self._app(scope, bounded_receive, guarded_send)


def _declared_content_length(scope: Scope) -> Optional[int]:
    for name, value in scope.get("headers") or []:
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


async def _send_413(send: Send) -> None:
    payload = json.dumps({"detail": "影像檔案過大，請重新拍攝或壓縮後再試"}).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": payload})
