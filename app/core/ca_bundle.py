"""certifi 根憑證 ＋ 手動釘選的中繼憑證，供 TLS 驗證使用。

**移植自 CARE-data/ca_bundle.py**（2026-09-02）。那邊兩年前就踩過並解掉了
同一個問題，這裡是同一套做法的 httpx 版本；兩份要一起維護，換憑證時別漏掉。

為什麼需要：www.mohw.gov.tw 與 www.hpa.gov.tw 的伺服器只送出 leaf 憑證，
沒有附上中繼憑證 TWCA Secure SSL Certification Authority。瀏覽器與 macOS 的
curl 會依 leaf 憑證的 AIA 欄位自動補抓，Python 的 ssl 模組不會，於是 httpx
拋 SSLError（被包成 ConnectError，看起來很像站台掛掉，極容易誤診）。

對 CARE 的實際影響：`RAG_ALLOWED_DOMAIN_SUFFIXES` 的主體是 gov.tw，而
2026-09-02 的全庫稽核顯示 2420 個來源網址裡有 1011 個在 www.hpa.gov.tw。
沒有這張中繼憑證，那 42% 的來源在 link_check 眼中永遠是「判不出來」——
不會被誤殺（那條路徑是安全的），但也永遠驗不出其中真正失效的是哪些。

不是 verify=False：這裡是「信任原本那批根憑證，外加 TWCA 這一張中繼憑證」，
憑證驗證仍然完整有效。釘選的憑證是公開資料（任何人連上衛福部都拿得到同
一張），存於 resources/certs/，有效期至 2030-10-16。

到期後那兩個站會連不上並拋出明確的 SSLError——這是刻意的：大聲失敗遠優於
verify=False 那種默默什麼都不檢查。屆時從 leaf 憑證的 AIA 網址重新下載即可。
另一個失效情境是未來的 certifi 移除了 TWCA Global Root CA：Python 預設不設
X509_V_FLAG_PARTIAL_CHAIN，憑證鏈必須終止於自簽根憑證，單靠釘選的中繼憑證
救不回來。兩種情況都會讓 link_check 把那些網址記成「判不出來」而非死鏈，
不會傷到使用者看到的來源。
"""

from __future__ import annotations

import atexit
import logging
import os
import tempfile

import certifi

logger = logging.getLogger(__name__)

_PINNED_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "resources",
    "certs",
)

_bundle_path: str | None = None


def get_ca_bundle() -> str:
    """回傳合併後 CA bundle 的檔案路徑（同一程序內只建立一次）。

    釘選目錄不存在或沒有 .pem 時回傳 `certifi.where()`，也就是完全退回
    預設信任鏈——少了中繼憑證只會讓那些站台變成「判不出來」，不該讓整個
    行程起不來。
    """
    global _bundle_path
    if _bundle_path and os.path.exists(_bundle_path):
        return _bundle_path

    pinned_parts: list[str] = []
    if os.path.isdir(_PINNED_DIR):
        for name in sorted(os.listdir(_PINNED_DIR)):
            if name.endswith(".pem"):
                with open(os.path.join(_PINNED_DIR, name), encoding="utf-8") as fh:
                    pinned_parts.append(fh.read().strip() + "\n")

    if not pinned_parts:
        logger.warning(
            "no pinned certificates in %s; falling back to certifi defaults",
            _PINNED_DIR,
        )
        return certifi.where()

    # certifi 的內容原封不動保留（含開頭可能有的空白行），確保它仍是 bundle
    # 的逐字子字串；只對我們自己附加的釘選憑證做 strip，避免多餘空白造成
    # PEM 解析問題。
    with open(certifi.where(), encoding="utf-8") as fh:
        certifi_content = fh.read()

    fd, path = tempfile.mkstemp(prefix="care_ca_", suffix=".pem")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(certifi_content)
        if not certifi_content.endswith("\n"):
            fh.write("\n")
        fh.write("\n".join(pinned_parts))

    atexit.register(lambda: os.path.exists(path) and os.unlink(path))
    _bundle_path = path
    return path
