# reply-i18n Specification

## Purpose
TBD - created by archiving change reply-language-from-settings. Update Purpose after archive.
## Requirements
### Requirement: 支援語系與 normalize

系統 SHALL 支援與 LIFF／Rich Menu 相同的語系集合：`zh-TW`、`en`、`id`、`vi`、`th`、`ja`。解析使用者語言時，若不在集合內或為空，SHALL fallback 為 `zh-TW`。

#### Scenario: 已知語系原樣通過

- **WHEN** 輸入語言為 `ja`
- **THEN** normalize 結果為 `ja`

#### Scenario: 未知語系 fallback

- **WHEN** 輸入語言為 `ko` 或空值
- **THEN** normalize 結果為 `zh-TW`

### Requirement: 請求處理期間可取得使用者語言

處理 LINE 使用者訊息時，系統 SHALL 自該使用者 profile 的 `settings.language` 解析語言，並使同一次處理路徑內的工具與固定字串查表可取得該語言（例如 ContextVar）。未設定時 SHALL 使用 `zh-TW`。

#### Scenario: 從 profile 設定語言

- **WHEN** 使用者 `settings.language` 為 `en` 且開始處理一則文字訊息
- **THEN** 該次處理路徑取得的請求語言為 `en`

### Requirement: 固定字串依語言輸出

下列使用者可見固定字串 SHALL 依請求語言輸出對應翻譯（缺譯文時 fallback `zh-TW`）：RAG fail（KB_EMPTY／WEB_EMPTY／WEB_ERROR／MODEL_REFUSE）、無法理解／處理錯誤 fallback、請分享位置提示、分享位置 Quick Reply label、附近無院所訊息。

#### Scenario: 英文使用者看到英文 fail 文案

- **WHEN** 請求語言為 `en` 且 RAG 回傳 KB_EMPTY fail
- **THEN** 使用者可見訊息為英文文案（仍可含 `[RAG_ERR:KB_EMPTY]` 前綴代碼）

#### Scenario: 分享位置 Quick Reply 跟隨語言

- **WHEN** 請求語言為 `ja` 且回覆需附位置 Quick Reply
- **THEN** Quick Reply label 為日文對應文案

