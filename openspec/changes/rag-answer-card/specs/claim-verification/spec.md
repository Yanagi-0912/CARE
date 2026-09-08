## MODIFIED Requirements

### Requirement: 判定卡呈現與來源標示

系統 SHALL 以 LINE Flex Message 呈現查核結果，內容 SHALL 包含判定、**使用者原本的問句**、理由與來源。判定卡 SHALL 標示判定出自原查核組織，並於該篇有 URL 時提供原文連結。

卡片 SHALL NOT 顯示知識庫的 `claim` 欄位：線上實測該欄位有 35% 裝的是查核結論而非被查核的主張，顯示出來會與另外呈現的判定重複且語意打架。

判定為「事實釐清」時 SHALL NOT 使用表示真偽的語意配色——該分類不對真偽作判斷。

**系統 SHALL 在送出前量測判定卡的上線位元組，超過門檻時 SHALL 退回既有的純文字判定格式。** 量測方式 SHALL 與 LINE SDK 實際送出的序列化一致（`json.dumps` 預設參數，非 ASCII 字元轉義），SHALL NOT 以未轉義的 UTF-8 位元組計算。

理由：未命中時 `related_info` 會放入檢索到的衛教文章全文，該欄位無長度上限。實測一則 `related_info` 為 1,136 字的真實卡片，上線位元組為 8,110 bytes，已達 10 KB／bubble 上限的 79%；再多一篇文章即會超過。超過時 `build_verdict_flex` 不會拋例外（它不檢查大小），既有的組裝失敗 fallback 因此不會觸發，訊息會在 `reply_message()` 被 LINE 以 400 拒收，例外被 `reply()` 的 `except` 接住後只留下一行 log——**使用者完全收不到回覆，且對話歷史也不會寫入**。

量測 SHALL 對所有判定卡執行，不限未命中情形。理由：命中時的 `reasoning` 由語言模型改寫產生，同樣沒有長度保證。

#### Scenario: 標示來源

- **WHEN** 判定命中一篇台灣事實查核中心的報告
- **THEN** 卡片標示判定來自台灣事實查核中心並提供該篇原文連結

#### Scenario: 事實釐清不用真偽配色

- **WHEN** 命中的 `verdict` 為「事實釐清」
- **THEN** 卡片以中性配色呈現，SHALL NOT 使用表示錯誤或正確的語意色

#### Scenario: 超大判定卡退回純文字

- **WHEN** 未命中且 `related_info` 內容使卡片的上線位元組超過門檻
- **THEN** 使用者收到純文字格式的判定內容，SHALL NOT 出現無回應
