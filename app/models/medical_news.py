"""每日醫療消息卡的資料模型。

三個 collection 各自回答不同的問題，刻意不合併：

- `drug_news`：某個藥名／成分近期有哪些官方消息。與使用者無關，因此可以被所有
  服用同一種藥的人共用——這正是索引服務按藥名而非按人快取的前提
  （design.md 決策 2）。
- `medical_news_deliveries`：某位使用者收過哪些消息。它的唯一索引一物二用，
  既是去重也是多實例下的推播權搶佔（design.md 決策 10）。
- `medical_news_shares`：某位收件人被分享過哪些消息。防的是「三位家人都按了認同，
  同一位長輩收到三張一樣的卡」。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

NewsKind = Literal["drug_news", "kb_article"]

_NEWS_KINDS: frozenset[str] = frozenset({"drug_news", "kb_article"})

# 雜湊取前 32 個十六進位字元（128 bits）。碰撞機率在本專案的資料量級下可忽略，
# 而完整的 64 字元對索引鍵沒有額外好處。
_REF_DIGEST_CHARS = 16 * 2


def make_news_ref(kind: str, key: str) -> str:
    """把 (種類, 鍵) 壓成一個定長的參考字串。

    **雜湊而非原字串**：`kb_article` 的 key 是文章 url，而 Mongo 單一索引鍵的上限是
    1024 bytes。夠長的 url 會讓帶有唯一索引的 insert 直接拋錯，而那個錯落在推播路徑
    上——使用者收不到卡片，log 裡是一個看起來與內容無關的索引錯誤。定長雜湊讓這個
    失敗模式從一開始就不存在。

    kind 前綴保留明文，是為了讓 log 與資料庫裡的值仍看得出這是哪一種來源；它不參與
    雜湊，因此兩種 kind 對同一個 key 不會相撞。
    """
    if kind not in _NEWS_KINDS:
        raise ValueError(f"unknown news kind: {kind!r}")
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:_REF_DIGEST_CHARS]
    return f"{kind}:{digest}"


class DrugNews(BaseModel):
    """一則與某個藥名／成分相關的官方消息。

    `url` 是必填且不可為空：消息卡必須帶可點的來源連結，分享出去的卡片尤其——那是
    收件人唯一能自行查證的東西。食藥署 `DataAction` feed 結構上不提供文章網址，
    因此該來源的內容進不了這個模型（design.md 決策 3）。
    """

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    drug_key: str
    key_kind: Literal["ingredient", "name_zh"]
    url: str = Field(min_length=1)
    title: str
    source_name: str
    # 抽不到日期時為 None。這種文件不得進入 Tier 1（design.md 決策 5 第 4 道防線），
    # 但仍存下來，避免同一個 url 每天被重新抓取與判定。
    published_at: Optional[str] = None
    # 中性第三人稱摘要。SHALL NOT 出現「您」「你的藥」這類第二人稱脈絡——分享路徑
    # 的零洩漏是靠這個約定達成的，不是靠分享時再改寫一次（design.md 決策 6）。
    summary: str
    # `none` 刻意不在值域內：判定為 none 的結果不該被存下來，存了就代表某天有人會
    # 把它撈出來推播。過濾發生在寫入前，不是讀取後。
    concern_kind: Literal["recall", "safety", "supply", "education"]
    content_hash: str
    indexed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MedicalNewsDelivery(BaseModel):
    """某位使用者收過某一則消息的紀錄。

    這份文件的存在本身就是「已推播」，因此排程器插入成功才推、插入失敗即跳過。

    **卡片內容一併存在這裡**，而不是分享時回頭查來源。兩個理由：
    `news_ref` 是雜湊，反解不回 url，分享的 postback 只帶得動它；而且分享卡
    應該顯示**分享者當時看到的東西**——Tier 2 的知識庫文章可能已因重新切片而
    消失，Tier 1 的公告也可能被修訂。
    """

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    user_id: str
    news_ref: str
    tier: Literal[1, 2]
    title: str = ""
    summary: str = ""
    source_name: str = ""
    url: str = ""
    pushed_at: datetime
    shared_at: Optional[datetime] = None
    share_recipient_count: int = 0


class MedicalNewsShare(BaseModel):
    """某位收件人被分享過某一則消息的紀錄。"""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    recipient_id: str
    news_ref: str
    sharer_id: str
    sent_at: datetime
