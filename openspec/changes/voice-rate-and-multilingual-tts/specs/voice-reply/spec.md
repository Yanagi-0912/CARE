## ADDED Requirements

### Requirement: 語音語言跟隨使用者語言設定

產生語音回覆時，系統 SHALL 以該使用者 `settings.language`（經 normalize，集合為 `zh-TW`、`en`、`id`、`vi`、`th`、`ja`）決定合成語言與音色。語言未設定、為空或不在支援集合內時 SHALL fallback 為 `zh-TW`。系統 SHALL NOT 將文字回覆的語言與語音的合成語言分離。

#### Scenario: 日文使用者取得日文語音

- **WHEN** 使用者 `settings.language` 為 `ja` 且系統產生一則純文字回覆並附加語音
- **THEN** 語音以日文音色合成（而非以英文發音唸日文）

#### Scenario: 未知語言 fallback

- **WHEN** 使用者 `settings.language` 為 `ko` 或空值且需要產生語音
- **THEN** 語音以 `zh-TW` 音色合成

### Requirement: 語音語速可由使用者設定

系統 SHALL 提供 `settings.voice_rate`，值域為 `slow`、`normal`、`fast`，預設 `normal`。合成時 SHALL 依該設定調整語速。使用者變更後 SHALL 於下一則回覆即生效，不需重新登入或重啟。

#### Scenario: 選擇慢速後語音變慢

- **WHEN** 使用者將 `voice_rate` 設為 `slow` 並發送下一則訊息
- **THEN** 該則回覆的語音長度明顯長於同樣文字在 `normal` 下的長度

#### Scenario: 缺欄位的舊資料取得預設值

- **WHEN** 使用者 profile 的 `settings` 不含 `voice_rate`
- **THEN** 讀取設定時回傳 `normal`，且不需資料 migration

#### Scenario: 非法值被拒絕

- **WHEN** 以 PATCH 更新 `voice_rate` 為 `super_fast`
- **THEN** API 回應 422，且資料庫既有值不變

### Requirement: 語音合成失敗不得阻斷文字回覆

語音合成發生任何錯誤時，系統 SHALL 仍送出文字回覆，且 SHALL NOT 讓例外傳播至 LINE webhook 處理流程。系統 SHALL 在主要合成引擎失敗時嘗試備援引擎，兩者皆失敗時僅回覆文字。

#### Scenario: 主要引擎失敗改用備援

- **WHEN** 主要合成引擎拋出例外而備援引擎成功
- **THEN** 使用者仍收到文字與語音兩則訊息

#### Scenario: 全部引擎失敗仍回文字

- **WHEN** 主要與備援引擎皆失敗
- **THEN** 使用者僅收到文字訊息，webhook 回應成功，錯誤寫入 log

### Requirement: 僅純文字回覆附加語音

系統 SHALL 僅在回覆為純文字訊息時附加語音訊息。回覆為 Flex Message 時 SHALL NOT 附加語音。使用者的語音回覆開關為關閉時 SHALL NOT 進行合成。

#### Scenario: Flex 回覆不附語音

- **WHEN** 回覆內容被解析為 Flex Message（例如院所查詢結果卡片）
- **THEN** 不呼叫語音合成，僅送出 Flex 訊息

#### Scenario: 開關關閉時不合成

- **WHEN** 使用者 `voice_reply_enabled` 為 false
- **THEN** 不呼叫語音合成，僅送出文字訊息

### Requirement: 語音合成不得阻塞事件迴圈

語音合成 SHALL 以非阻塞方式執行於 async 請求路徑中。系統 SHALL NOT 於 async 處理函式內直接呼叫同步的網路合成請求。

#### Scenario: 合成期間仍可處理其他 webhook

- **WHEN** 某使用者的語音正在合成
- **THEN** 其他使用者的 LINE webhook 事件可同時被接收與處理

### Requirement: 語音檔生命週期與對外存取

本地合成產生的音檔 SHALL 以 `tts_` 前綴命名並為 `.mp3` 格式，SHALL 經由公開端點提供給 LINE 下載，且 SHALL 於逾期後被清除。對外端點 SHALL 僅接受符合前述命名與副檔名的檔案請求。

#### Scenario: 非法檔名被拒絕

- **WHEN** 請求的檔名不以 `tts_` 開頭、含路徑分隔字元、或副檔名非 `.mp3`
- **THEN** 端點回應 404

#### Scenario: 過期音檔被清除

- **WHEN** 音檔的修改時間早於保留期限
- **THEN** 該檔案於後續合成時被刪除

### Requirement: 語音設定具備兩個使用者入口

語音回覆開關 SHALL 同時可由 LINE Rich Menu 一鍵切換與 LIFF 設定頁調整；語速 SHALL 於 LIFF 設定頁調整。兩個入口 SHALL 讀寫同一份使用者設定，任一入口變更後另一入口再次讀取時 SHALL 反映最新值。

#### Scenario: Rich Menu 切換後設定頁同步

- **WHEN** 使用者以 Rich Menu 關閉語音回覆後開啟 LIFF 設定頁
- **THEN** 設定頁的語音回覆開關顯示為關閉
