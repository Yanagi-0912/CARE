## MODIFIED Requirements

### Requirement: 提醒規則關聯藥品

`MedicationReminder` SHALL 有 `medication_ids` 欄位，記錄該時段應服用的藥品。欄位預設為空陣列。

既有規則於資料庫中沒有此欄位，讀回時 SHALL 視為空陣列，其排程與推播行為 SHALL 與本欄位導入前完全一致。

`medication_ids` 非空時，排程器 SHALL 於展開當日執行紀錄前，判定其對應藥品在當日是否至少有一個有效；一個都沒有時 SHALL NOT 展開該時段的當日紀錄。`medication_ids` 為空時 SHALL NOT 做此判定，展開行為 SHALL 僅依 `slot_type`、`scheduled_time`、`start_date`、`end_date`、`enabled` 與規則 `created_at` 決定。

理由：規則的日期區間回答「這個時段本身還在不在」，藥品的日期區間回答「今天有沒有東西要吃」。兩者由不同的寫入路徑決定——處方箋提交時療程結束日只寫進藥品，規則的 `end_date` 一律是 null——因此展開判定若只讀規則自己的日期區間，療程結束後會持續推出沒有任何藥名的提醒卡。對高齡使用者而言，一則說不出吃什麼的提醒不只沒用，還可能造成誤服。

此判定 SHALL 置於展開階段而非推播路徑。理由：三階推播查詢均限定 `status="pending"`，紀錄不存在即三則推播全部停下，因此原子搶佔與停機補償的既有保證 SHALL NOT 因本判定而需要重新驗證。

藥品查詢失敗時 SHALL NOT 抑制任何規則，SHALL 記錄錯誤後照常展開。理由：漏推一次真正該吃的藥，代價高於多推一張空卡片。

同一個藥品 MAY 同時關聯至同一位用藥者的多個時段規則。

#### Scenario: 既有規則無藥品欄位

- **WHEN** 排程器讀取一筆本欄位導入前建立、資料庫中沒有 `medication_ids` 的規則
- **THEN** 該規則 SHALL 正常展開與推播，`medication_ids` SHALL 視為空陣列

#### Scenario: 療程結束後不再展開

- **WHEN** 某時段規則關聯的藥品當日全部無效
- **THEN** 排程器 SHALL NOT 為該時段展開當日紀錄，T+0、T+20 與 T+30 三則推播 SHALL NOT 送出

#### Scenario: 藥品查詢失敗

- **WHEN** 判定藥品有效性的查詢拋出例外
- **THEN** 系統 SHALL 記錄錯誤並照常展開所有規則，SHALL NOT 因此抑制任何時段

#### Scenario: 一種藥關聯多個時段

- **WHEN** 某藥品需一日三次
- **THEN** 該藥品的 id SHALL 出現在該用藥者 `morning`、`noon`、`evening` 三筆規則的 `medication_ids` 中

### Requirement: 藥品的有效性獨立於時段規則

藥品 SHALL 有自己的 `enabled` 與療程起訖日期。藥品被停用、或當日不在其療程區間內時，該藥品 SHALL 視為當下無效，SHALL NOT 出現在推播的藥品清單中。

停用或結束一個藥品 SHALL NOT 停用任何時段規則——同一時段可能還有其他藥要吃。

刪除一筆時段規則 SHALL NOT 刪除其關聯的藥品；藥品 SHALL 獨立存在，並可被重新關聯至其他規則。

當某時段規則的所有關聯藥品都已失效時，該規則 SHALL 維持啟用，SHALL NOT 自動停用；但當日 SHALL NOT 展開執行紀錄，因此當日 SHALL NOT 推播。已展開且仍為 `pending` 的當日紀錄 SHALL 轉為 `cancelled`；更早的紀錄 SHALL NOT 被更動。

理由：規則可能是使用者手動建立的，自動停用會靜默移除他明確設定過的提醒，而且下一張處方提交到同一時段時會觸發非預期的「規則已復活」告知。但「不停用規則」與「照常推播一張沒有藥名的卡片」是兩件事——前者保護使用者的設定，後者傷害使用者。

療程結束日 SHALL NOT 回寫至 `medication_reminders.end_date`。理由：時段規則是 `(user_id, slot_type)` 唯一的共用容器，同一筆規則可同時關聯多張處方的不同療程與長期用藥（`end_date` 為 null），單一欄位無法表達；且 `find_or_create_reminder` 在復活規則時本就會清空已過期的 `end_date`，回寫值無法存續至下一次同時段提交。

#### Scenario: 療程結束

- **WHEN** 某藥品的療程結束日期早於今日
- **THEN** 該藥品 SHALL NOT 出現在當日推播的藥品清單中，該時段規則 SHALL 維持啟用

#### Scenario: 全部藥品失效

- **WHEN** 某時段規則關聯的藥品全部療程結束或被停用
- **THEN** 該規則 SHALL 維持 `enabled=true`，當日 SHALL NOT 推播，當日殘留的 `pending` 紀錄 SHALL 轉為 `cancelled`

#### Scenario: 停用單一藥品

- **WHEN** 使用者停用某時段兩種藥中的一種
- **THEN** 該時段 SHALL 照常推播，藥品清單 SHALL 僅列出另一種

## ADDED Requirements

### Requirement: 推播重試有次數上限

`MedicationLog` SHALL 為 T+0、T+20、T+30 三個階段各保留一個推播嘗試次數。既有紀錄沒有這些欄位，讀回時 SHALL 視為 0，SHALL NOT 需要資料回填。

推播失敗而還原該階段旗標時，系統 SHALL 累加該階段的嘗試次數。次數達到上限時 SHALL NOT 還原旗標，該階段 SHALL 就此放棄並記錄錯誤。

三個階段的次數 SHALL 各自獨立計算；一個階段耗盡預算 SHALL NOT 影響其餘階段的重試機會。

上限 SHALL 使該階段的重試總時長明顯短於 T+20 催促的門檻，避免一個階段的重試延後下一個階段的推播時機。

放棄家屬逾時警報時，紀錄狀態 SHALL 維持 `missed`——使用者確實未在時限內確認服藥，此事實 SHALL NOT 因通知送不出去而改變。

理由：還原旗標交由後續 tick 重試，對瞬時故障（資料庫瞬斷、LINE 端 5xx）是正確的；但對不會自行恢復的錯誤——LINE 月推播額度耗盡的 429、收件人已封鎖官方帳號——等同每 60 秒重試一次直到該狀態解除，且每輪都會重新查詢收件人設定、重組 Flex、再發出一次注定失敗的請求。

#### Scenario: 瞬時故障後恢復

- **WHEN** 某階段推播失敗且該階段嘗試次數尚未達上限
- **THEN** 系統 SHALL 累加次數並還原旗標，後續 tick SHALL 重試

#### Scenario: 額度耗盡

- **WHEN** 某階段推播連續失敗至嘗試次數達上限
- **THEN** 系統 SHALL 記錄錯誤且 SHALL NOT 還原旗標，後續 tick SHALL NOT 再重試該階段

#### Scenario: 既有紀錄無計次欄位

- **WHEN** 一筆本需求導入前建立、無計次欄位的紀錄推播失敗
- **THEN** 系統 SHALL 視其為第一次失敗並正常還原旗標，SHALL NOT 判定為已達上限

### Requirement: 停機補償訊息只在建立當下記錄

判定為錯過（misfire）的時段，其記錄訊息 SHALL 僅在該筆執行紀錄為本次 tick 才建立時輸出一次。

理由：錯過與否是對同一個時段每輪重算的結果，每輪都會再次成立。不以「本次才建立」為條件，同一個時段會每 60 秒重印一行——一位使用者一天約四千行——而該訊息要記錄的是「這個時段被靜默記為錯過」這個一次性事件，不是每次重新判定的結果。

#### Scenario: 同一個錯過時段的後續 tick

- **WHEN** 某個已記錄為錯過的時段在後續 tick 再次被判定為錯過
- **THEN** 系統 SHALL NOT 重複輸出該訊息
