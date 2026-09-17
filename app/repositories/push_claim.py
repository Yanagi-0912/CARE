"""推播權的還原與重試上限，用藥提醒與掛號提醒共用。

兩個排程器都是「以旗標原子搶下推播權 → 推播 → 失敗就還原旗標讓下一個 tick
重試」。還原這一步的重試上限若兩邊各寫一份，日後調整上限（或修正缺欄位的舊
資料那個邊界）時必定漏改其中一邊，所以只有一份實作，collection 由呼叫端傳入。
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from pymongo import ReturnDocument

logger = logging.getLogger(__name__)

# 搶下推播權之後、多久沒有寫下「送達」或「刻意略過」就視為搶佔者已經死了，
# 允許別的實例重新搶下。
#
# 為什麼需要它：旗標在搶佔的當下就設成「已送出」，推播卻是之後才發生的外部
# 呼叫。搶佔與推播之間 pod 被 OOM kill、SIGKILL 滾動更新、或 asyncio task 被
# 取消（`CancelledError` 不是 `Exception`，`except Exception` 接不到），旗標就
# 永遠停在 True，這一則提醒從此沒有任何人會再送——使用者這一頓沒收到提醒，
# 資料上卻寫著送過了。
#
# 取 5 分鐘：一次搶佔到推播完成只有幾秒（LINE API 逾時 10 秒），5 分鐘遠大於
# 任何正常的搶佔壽命，不會把還在推播中的實例誤判為死亡；同時它等於
# `MAX_PUSH_ATTEMPTS` × tick 間隔 60 秒的瞬時故障容忍視窗，也小於 T+20 到
# T+30 之間的 10 分鐘——T+0 死掉的搶佔在下一階段門檻之前就會被接手，不會
# 出現「T+20 催促先到、T+0 提醒後到」的順序倒錯。
PUSH_CLAIM_LEASE = timedelta(minutes=5)

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


def claimable_filter(
    *,
    sent_field: str,
    sent_at_field: str,
    skipped_at_field: str,
    claimed_at_field: str,
    attempts_field: str,
    now: datetime,
    fresh: Optional[dict] = None,
    stale: Optional[dict] = None,
) -> dict:
    """搶佔查詢的「可以搶」條件：旗標還沒設起（正常路徑），或旗標設起了但
    搶佔者在租約內既沒送達也沒略過（搶佔者已死，租約到期接手）。

    第二個分支的每一項都不是多餘的：
    - `sent_at`／`skipped_at` 為 None（同時命中「欄位不存在」與「值為 null」）
      ——真的送達或刻意略過的紀錄不能被重送。
    - `claimed_at` 必須存在且早於租約——本欄位落地前寫入、已經送達的舊紀錄
      沒有 claimed_at，比較運算子對缺欄位不成立，因此不會被誤判為死掉的搶佔；
      停機補建時直接標成「已送出」的錯過時段也是同一種形狀。
    - 嘗試次數未達上限——放棄的紀錄旗標同樣停在 True 而沒有 sent_at，少了這
      一項，`release_push_claim` 的放棄會在 5 分鐘後被租約接手、無限重來。

    `fresh`／`stale` 讓呼叫端把各自分支額外的條件（例如 status）放進去：
    T+30 家屬警報搶佔時把 status 改成 missed，租約接手時看到的就是 missed。
    """
    return {
        "$or": [
            {sent_field: False, **(fresh or {})},
            {
                sent_field: True,
                sent_at_field: None,
                skipped_at_field: None,
                claimed_at_field: {"$lt": now - PUSH_CLAIM_LEASE},
                attempts_field: {"$not": {"$gte": MAX_PUSH_ATTEMPTS}},
                **(stale or {}),
            },
        ]
    }


async def mark_push_stage(
    collection: Any, doc_id: str, *, field: str, now: Optional[datetime] = None
) -> bool:
    """寫下某個階段的時刻標記（送達 `*_sent_at` 或略過 `*_skipped_at`）。

    無條件以 _id 寫入：這是搶到推播權的實例在推播完成後才呼叫的，不需要再
    與旗標比對；就算使用者在這幾毫秒內按了「已用藥」，「這則提醒送達過」
    仍然是事實。
    """
    result = await collection.update_one(
        {"_id": doc_id},
        {"$set": {field: now or datetime.now(timezone.utc)}},
    )
    return result.matched_count > 0


async def give_up_push_claim(
    collection: Any,
    doc_id: str,
    *,
    stage: str,
    attempts_field: str,
    error: str,
    log_prefix: str = "[PushClaim]",
) -> bool:
    """立刻放棄某個階段：LINE 明確拒絕了對象或內容（400／404），同樣的東西
    再送四次只是白花四次呼叫。嘗試次數直接寫成上限，旗標維持已設起，租約
    也不會接手（`claimable_filter` 排除已達上限者）；失敗分類留在
    `last_push_error` 供事後查詢。
    """
    result = await collection.update_one(
        {"_id": doc_id},
        {"$set": {attempts_field: MAX_PUSH_ATTEMPTS, "last_push_error": error}},
    )
    logger.error(
        "%s Giving up %s for %s immediately: LINE rejected it (%s); "
        "the push flag stays set so it will not be retried",
        log_prefix,
        stage,
        doc_id,
        error,
    )
    return result.matched_count > 0


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
    count_attempt: bool = True,
    error: Optional[str] = None,
) -> bool:
    """還原某個階段的推播權，並累加嘗試次數；達到上限時不還原。

    回傳 True 代表旗標已還原、下一個 tick 會重試；False 代表沒有還原
    （已達上限而放棄，或這筆紀錄已不符合還原條件）。

    `count_attempt=False` 是給 LINE 429（額度用完）用的：那不是這一則提醒的
    失敗，是整個 channel 這個月都送不出去。照常累加的話，五個 tick 之後所有
    待送的提醒都會被「放棄」而旗標停在已送出，等額度恢復（升級方案或下個月）
    也不會補送；不累加則旗標立刻還原，額度一恢復下一個 tick 就送出。代價是
    額度用完期間每 60 秒會重試一次——排程器每個 tick 只印一行彙整警告，
    不會逐筆刷 log。`error` 有給時一併寫進 `last_push_error`。

    先 `$inc` 再視結果決定要不要清掉旗標，分兩次寫入而不是一次
    條件式更新：`{"$lt": N}` 對「欄位不存在」的舊紀錄不成立，用單一條件式
    更新會讓所有既有紀錄第一次就被判定為已達上限而直接放棄。`$inc` 沒有
    這個問題（缺欄位視為 0），因此以它的回傳值當判斷依據。這條路徑只在
    推播已經失敗時才走到，多一次往返不影響正常流程。

    放棄之後旗標維持「已送出」，這在資料上確實不精確（其實沒送成功），但真正的
    替代方案是無限重試，那個代價更大；而且嘗試次數本身留在紀錄裡，事後查得出來
    是放棄還是送達。
    """
    error_set = {"last_push_error": error} if error else {}
    if not count_attempt:
        result = await collection.update_one(
            {"_id": doc_id, sent_field: True, **(extra_filter or {})},
            {"$set": {sent_field: False, **(extra_set or {}), **error_set}},
        )
        return result.modified_count > 0

    doc = await collection.find_one_and_update(
        {"_id": doc_id, sent_field: True, **(extra_filter or {})},
        {"$inc": {attempts_field: 1}, **({"$set": error_set} if error_set else {})},
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
