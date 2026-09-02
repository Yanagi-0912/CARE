## Why

### 使用者調的字級，對 RAG 回覆完全無效

`resources/flex_messages/theme.py` 已經有一套完整的字級系統：`_SIZE_SCALE` 把 `title`／`heading`／`body`／`caption`／`button`／`thumbnail` 六個語義角色，各自對應到 `normal`／`large`／`xlarge` 三檔的 LINE Flex size keyword，`resolve_theme()` 從 request-scoped ContextVar 取出該使用者的 `UserSettings.font_size`。`message_handler` 在每次 webhook 進來時就把它設好了。

判定卡與用藥提醒卡都吃這套，所以它們會跟著設定變大。**但 RAG 回覆走的是純文字分支，完全繞過它。**

結果是：長輩在 LIFF 設定頁把字級調到「特大」，問用藥提醒的事情看得清楚，問衛教問題卻拿回 LINE App 預設字級的一大段文字。本專案的目標使用者正是視力需求最高的族群，而衛教問答是他們最常用的功能。

### 判定卡已經逼近 LINE 的大小上限（既有線上風險）

`verify_claim` 未命中時會把檢索到的衛教文章**全文**放進 `related_info`，該欄位目前沒有任何長度上限。

實測一則真實的「證據不足」卡片（`related_info` 1,136 字、三篇衛教全文）：

```
bubble 上線位元組 = 8,110 bytes = 7.92 KB   （10 KB 上限的 79%）
```

「上線位元組」是關鍵：`linebot/v3/messaging/rest.py:155` 是 `json.dumps(body)`，用預設的 `ensure_ascii=True`，因此每個中文字在傳輸時是 `\uXXXX` 共 6 bytes，不是 UTF-8 的 3 bytes。`claim_tools.py` 裡的 `ensure_ascii=False` 只影響傳給 agent 的中間字串，不影響上線大小。

再多一篇衛教文章就會超過 10 KB，而超過時的行為比「退回純文字」糟：

1. `build_verdict_flex` 照常組出 dict（它不檢查大小）
2. `reply.py` 認得是 Flex，組成 `FlexMessage`
3. `line_bot_api.reply_message()` 送出 → LINE 回 400 拒收
4. `reply.py` 的 `except Exception` 接住 → log 一筆 → `return False`
5. `message_handler` 因 `success=False`，連對話歷史都不存

**使用者什麼都收不到，畫面上就是沒有回應。** `claim_tools.py` 現有的純文字 fallback 救不了——它只在「組裝拋例外」時觸發，而這裡組裝是成功的，是送出才被拒。

這個風險與字級無關，但用同一道防線就能擋掉，因此併入本次變更。

## What Changes

1. **RAG 回覆改以 Flex 卡片送出**。範圍是 `get_rag_answer`（衛教問答）與 `answer_from_uploaded_document`（上傳文件問答）。卡片在**呈現層**組裝，不在 tool 內組裝（理由見 design.md）。
2. **參考來源以 URI action 按鈕呈現**，取代目前的裸網址文字行。網址本身 SHALL NOT 被改動。
3. **RAG 答案生成端加入字數上限**，讓超限在建構上不可能發生，而不是靠降級路徑事後補救。
4. **新增 bubble 大小防線**：組卡後、送出前量測上線位元組，超過門檻即退回純文字。同時套用到 RAG 卡與**既有的判定卡**。
5. **卡片路徑不加 RAG 前綴**。純文字路徑（查不到、降級）維持原前綴不變。

### 明確不做

- **查不到／失敗的回覆不變成卡片**。`is_rag_fail()` 為真的輸出維持純文字。卡片是「有內容可呈現」時的形式，把一句「請換個方式描述」包成卡片沒有意義。
- **一般閒聊回覆不變成卡片**。本次只動 RAG。
- **不把卡片組裝搬進 tool**。這是本次最重要的架構決策，理由見 design.md 的「為什麼不比照 verify_claim」。

## Impact

- **Specs**
  - `line-reply-rules`：3 條 MODIFIED（RAG 回覆前綴、保留參考資料來源、依使用者語言設定回覆純文字）、2 條 ADDED（RAG 回覆的卡片化與降級、卡片路徑的語音回覆）
  - `rag-responses`：1 條 ADDED（RAG 答案長度上限）
  - `claim-verification`：1 條 MODIFIED（判定卡呈現與來源標示，加入大小上限）
- **Code**
  - 新增 `app/core/rag_sources.py`、`app/services/line_messaging/flex/rag_answer_flex.py`、`resources/flex_messages/size_guard.py`
  - 修改 `app/services/rag/answer_service.py`、`app/services/rag/answer_prompts.py`、`app/services/agent/agent.py`、`app/services/line_messaging/handler/message_handler.py`、`app/services/line_messaging/reply/reply.py`、`app/tools/claim_tools.py`
  - `app/services/line_messaging/flex/verdict_flex.py` **不變**：大小防線接在 `claim_tools.py::_to_flex_message_text`，因為退回純文字的決策點在那裡（`_format_verdict_reply` 是它的既有 fallback），builder 本身仍只負責組裝
- **API/route**：**無影響**。不新增、不修改任何 route，請求與回應形狀不變。
- **資料**：無 schema 變更。`UserSettings.font_size` 已存在且已在使用。
- **語音**：`voice_reply_enabled=true` 的使用者在卡片路徑仍會收到語音訊息（現行實作只有純文字分支會附加，本次一併補上）。
