"""推播權的還原與重試上限，用藥提醒與掛號提醒共用。

兩個排程器都是「以旗標原子搶下推播權 → 推播 → 失敗就還原旗標讓下一個 tick
重試」。還原這一步的重試上限若兩邊各寫一份，日後調整上限（或修正缺欄位的舊
資料那個邊界）時必定漏改其中一邊，所以只有一份實作，collection 由呼叫端傳入。
"""

import logging
from typing import Any, Optional

from pymongo import ReturnDocument

logger = logging.getLogger(__name__)

# 單一階段的推播嘗試次數上限（含第一次）。超過就放棄，不再把推播權還回去。
#
# 為什麼要有上限：`release_*` 把旗標回寫成「未送出」是為了讓下一個 tick 重試，
# 這對瞬時故障（資料庫瞬斷、LINE 端 5xx）是對的。但有一整類錯誤不會自行恢復
# ——最典型的是 LINE 月推播額度耗盡的 429——在那種情況下「還回去、下一輪再試」
# 會變成每 60 秒重試一次、直到月底都不會停，而且每一輪都會重新查 profile、
# 重組 Flex、再打一次注定失敗的 API。
#
# 取 5：涵蓋約 5 分鐘的瞬時故障（tick 間隔 60 秒），仍遠小於下一個推播階段的
# 門檻，因此一個階段耗盡預算不會延後或吃掉下一個階段的推播時機。
MAX_PUSH_ATTEMPTS = 5


async def release_push_claim(
    collection: Any,
    doc_id: str,
    *,
    stage: str,
    sent_field: str,
    attempts_field: str,
    extra_filter: Optional[dict] = None,
    extra_set: Optional[dict] = None,
    log_prefix: str = "[PushClaim]",
) -> bool:
    """還原某個階段的推播權，並累加嘗試次數；達到上限時不還原。

    回傳 True 代表旗標已還原、下一個 tick 會重試；False 代表沒有還原
    （已達上限而放棄，或這筆紀錄已不符合還原條件）。

    先 `$inc` 再視結果決定要不要清掉旗標，分兩次寫入而不是一次
    條件式更新：`{"$lt": N}` 對「欄位不存在」的舊紀錄不成立，用單一條件式
    更新會讓所有既有紀錄第一次就被判定為已達上限而直接放棄。`$inc` 沒有
    這個問題（缺欄位視為 0），因此以它的回傳值當判斷依據。這條路徑只在
    推播已經失敗時才走到，多一次往返不影響正常流程。

    放棄之後旗標維持「已送出」，這在資料上確實不精確（其實沒送成功），但真正的
    替代方案是無限重試，那個代價更大；而且嘗試次數本身留在紀錄裡，事後查得出來
    是放棄還是送達。
    """
    doc = await collection.find_one_and_update(
        {"_id": doc_id, sent_field: True, **(extra_filter or {})},
        {"$inc": {attempts_field: 1}},
        return_document=ReturnDocument.AFTER,
    )
    if doc is None:
        return False

    attempts = doc.get(attempts_field, 0)
    if attempts >= MAX_PUSH_ATTEMPTS:
        logger.error(
            "%s Giving up %s for %s after %d attempts; "
            "the push flag stays set so it will not be retried",
            log_prefix,
            stage,
            doc_id,
            attempts,
        )
        return False

    result = await collection.update_one(
        {"_id": doc_id},
        {"$set": {sent_field: False, **(extra_set or {})}},
    )
    return result.modified_count > 0
