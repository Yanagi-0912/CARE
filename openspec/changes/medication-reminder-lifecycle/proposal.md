## Why

一位使用者從 2026-08-22 起，連續 11 天、每天三次收到一張沒有藥名的服藥提醒卡——版面只剩「早／服藥時間 08:00」與「請於 30 分鐘內服藥，並點擊下方按鈕確認。」。使用者回報的是「沒有藥丸照片」，實際上整個藥品清單區塊都不見了。

### 根因：規則與藥品的生命週期由不同路徑決定，且從未對齊

處方箋提交時，療程結束日只寫進了藥品：

```
medication_reminders:  start = 2026-08-17,  end = None          ← 長期，永不失效
medications (×4):      start = 2026-08-17,  end = 2026-08-21    ← 療程五天，早已結束
```

`_build_medication` 由 `duration_days` 換算出 `Medication.end_date`，而 `_link_reminders` 呼叫 `find_or_create_reminder` 時沒有、也無法傳入結束日——那個方法的 `$setOnInsert` 一律寫 `end_date: None`。

兩邊用的是同一個日期濾網（`_active_date_window`），卻打在不同的 collection 上，於是給出相反的答案：

1. `list_active_reminders_up_to_time` 看規則 → `end_date=None` 代表長期有效 → 每天照常展開當日紀錄
2. `find_active_by_ids` 看藥品 → 四筆全部落在區間外 → 回傳 0 筆
3. `_TickMedicationNameCache` 拿到空清單，`_medication_list_block` 依約定回傳 `None`
4. 呼叫端不插入區塊 → 卡片上什麼藥都沒有

第 3、4 步本身是正確的（規則沒關聯藥品時版面要與功能導入前一致）。問題完全出在第 1 步與第 2 步的判斷依據不一致。

### 這是現行 spec 明文要求的行為

`medication-reminders` 目前有兩條條文直接造出這個結果：

> `medication_ids` SHALL 僅是關聯，SHALL NOT 影響排程器展開執行紀錄的判定。

> 當某時段規則的所有關聯藥品都已失效時，該規則 SHALL 維持啟用並照常推播，SHALL NOT 自動停用。

當初的理由是「排程器的展開、原子搶佔與停機補償行為已有既定條文與併發保證，把藥品關聯排除在展開判定之外，可讓本次變更不必重新驗證那些併發行為」。那是合理的顧慮，但它換來的代價當時沒有被算進去：**療程結束後，這條規則會叫長輩去吃一個系統自己也說不出名字的藥。** 對高齡使用者而言，一則說不出吃什麼的提醒不只沒用，還可能造成誤服。

第二條條文的用意是「不要靜默停用使用者手動設定的規則」——那個顧慮仍然成立，本提案也不停用任何規則。但「不停用」與「照常推播一張空卡片」是兩件事，舊條文把它們綁在了一起。

### 後果：這是 LINE 月額度歸零的直接原因

療程結束後，每個時段仍會推 T+0 用藥者提醒、T+20 催促、T+30 家屬警報共 3 則。`care-scheduler` 前一個 container 留下：

```
linebot.v3.messaging.exceptions.ApiException: (429)
{"message":"You have reached your monthly limit."}
Date: Fri, 28 Aug 2026 00:02:18 GMT
```

額度耗盡後所有 push 全滅，不只用藥提醒。而且推播失敗時 `release_*` 會把旗標回寫成「未送出」，下一個 tick 又重新搶佔——對 429 這類不會自行恢復的錯誤，這變成每 60 秒重試、直到月底都不會停。

## What Changes

1. **展開判定納入藥品有效性**（根因）。時段規則掛了藥、但當日一顆有效的都不剩時，不展開當日紀錄；已展開、還沒確認的紀錄改為 `cancelled`。`medication_ids` 為空的規則不受影響。
2. **推播重試設上限**（後果）。三個階段各帶一個嘗試次數，達到上限即放棄，不再無限重試。
3. **misfire 訊息只在本次才建立紀錄時記錄**。原本印在 `upsert_log` 之前且不看 `created`，同一個時段每 60 秒重印一行（一位使用者一天約 4,300 行）。
4. **一次性清理**既有的空提醒歷史紀錄。

### 明確不做：把療程結束日回寫到 `medication_reminders.end_date`

這是最直覺的修法，但它是錯的，理由見 design.md 的「為什麼不回寫 end_date」。

## Impact

- Specs：`medication-reminders`（兩條 MODIFIED、一條 ADDED）
- Code：`app/services/medication/medication_scheduler.py`、`app/repositories/medication_repository.py`、`app/models/medication.py`
- Script：新增 `scripts/cleanup_expired_course_logs.py`（預設只讀不寫）
- API/route：**無影響**。不新增、不修改任何 route，請求與回應形狀不變。
- 資料：`medication_logs` 新增三個計次欄位，缺席時視為 0，SHALL NOT 需要回填。
