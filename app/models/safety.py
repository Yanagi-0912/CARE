"""用藥風險偵測的抽取結果、通報節流紀錄與其列舉型別。

抽取與判斷刻意分成兩層：這裡的 `DrugMention` 只承載「輸入文字裡實際出現了
什麼」，不含任何風險欄位。風險等級由 `app/services/safety/risk_rules.py` 的純
函式依這些事實計算，通報家人這種不可逆的動作才不會取決於單次模型呼叫的輸出
穩定性，也才有辦法為判定門檻寫窮舉測試。
"""

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field

# 風險三級。`none` 不打擾、`low` 只回當事人、`high` 才通報族譜成員；每一級都
# 對應一套明確的收件人，因此不新增沒有揭露規則的等級。
RiskLevel = Literal["none", "low", "high"]

# 取得通路。電視購物、熟人介紹與網路賣場一律高風險（不論藥證庫是否命中），
# 境外個人攜帶則要與藥證庫結果合看。未提及通路時是 `unknown`，不是預設安全。
AcquisitionChannel = Literal[
    "medical_institution",
    "licensed_pharmacy",
    "overseas_personal",
    "online_marketplace",
    "acquaintance",
    "tv_shopping",
    "unknown",
]


class DrugMention(BaseModel):
    """輸入文字中提到的單一藥品。除藥名外全部允許為空——缺漏就留空，不推測。

    此模型 SHALL NOT 出現任何風險或安全性欄位：抽取階段只記錄事實，結論由
    純函式產生。
    """

    raw_name: str
    # 使用者或藥袋上的原文描述。保留原文是因為判定要看的訊號（外文標示、
    # 「代購」「朋友給的」）就藏在原字串裡，正規化後會被抹掉。
    source_text: Optional[str] = None
    channel: AcquisitionChannel = "unknown"
    # 藥品調劑包裝的法定必載欄位訊號（病患姓名、調劑機構、調劑者、調劑日期）。
    # 衛署藥字第0910033863號要求標示這些欄位，齊備時是「合法醫療機構調劑」的
    # 強訊號，用來擋掉藥袋 OCR 其中一個藥名未命中藥證庫造成的 `low` 誤報。
    dispensed_package_markers: list[str] = Field(default_factory=list)
    # 以下兩欄由抽取之後的藥證庫比對回填，不由模型輸出。未比對前一律視為未命中。
    catalog_hit: bool = False
    license_number: Optional[str] = None


class SafetyAlertRecord(BaseModel):
    """通報節流紀錄。同一位使用者對同一個藥品在 TTL 內只通報一次。

    `drug_key` 是正規化後的藥名，讓同一個藥的不同寫法落在同一筆；通報權以
    `(user_id, drug_key)` 的唯一索引原子取得，`expires_at` 交給 TTL 索引自動
    清除，不依賴應用端排程。
    """

    user_id: str
    drug_key: str
    risk_level: RiskLevel
    notified_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime
