"""分享卡的回覆內容，兩條路共用：message handler 的關鍵字秒回與 AI 工具 share_care。

回傳 reply.py 認得的工具 Flex JSON 字串（見 `LineReplier._try_parse_flex_message`），
兩條路送出去的就一定是同一張卡。拿不到官方帳號 ID 時改回一句純文字——少了 ID，
連結與 QR 全是壞的，不如直說。

語言與字級沿用 request-scoped 設定：兩條路都在 message handler 設好之後才走到這裡。
"""

import json

from app.i18n.messages import t
from resources.flex_messages.share_care_flex_message import (
    generate_share_care_flex_message,
)


class ShareCardService:
    def __init__(self, official_account, liff_url: str) -> None:
        self._official_account = official_account
        self._liff_url = liff_url or ""

    async def build_reply_text(self) -> str:
        basic_id = await self._official_account.get_basic_id()
        if not basic_id:
            return t("share.unavailable")
        payload = generate_share_care_flex_message(basic_id, self._liff_url)
        return json.dumps(payload, ensure_ascii=False)
