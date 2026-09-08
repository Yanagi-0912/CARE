## Context

字級系統（`resources/flex_messages/theme.py`）與 `UserSettings.font_size` 的讀寫路徑都已存在且在運作。本次不新建任何字級機制，只是把 RAG 回覆接上去——接上去的唯一方式就是讓它走 Flex，因為 LINE 的純文字訊息字級由使用者的 LINE App 決定，應用程式無法干預。

## 決策 1：卡片在呈現層組裝，不在 tool 內組裝

`open_official_site` 與 `verify_claim` 的既有做法是「tool 直接回傳 Flex JSON 字串，並列入 `agent.py` 的 `medical_tool_names` 白名單跳過模型改寫」。RAG **不比照**這個做法，理由有三，任一條都足以否決：

**一、RAG 答案需要模型改寫，判定卡不需要。** 判定卡的內容是結構化的查核結果（判定值逐字取自 TFC，不容模型改寫）。RAG 答案是自由文本，本來就依賴 agent 那一輪依對話脈絡整合——尤其是同一輪呼叫多個工具時（例如既問衛教又要找醫院）。進了白名單就等於固定丟棄那次整合。

**二、白名單每擴大一次，就多一次跨後置處理的耦合事故。** `agent.py` 的「後補參考資料來源」曾經因為只檢查「有沒有跑過 `get_rag_answer`」，把來源文字接到已被覆寫的 Flex JSON 尾巴後面，導致 `reply.py._try_parse_flex_message`（要求整串以 `{` 開頭、`}` 結尾）解析失敗，使用者收到一整段裸 JSON。該問題已修（改以 `used_tool_names` 是否非空為準），但成因是「response 在管線中途被換成不可再加工的格式」，白名單愈大，這個成因的暴露面愈大。

**三、白名單路徑會污染對話歷史。** `message_handler` 的 `save_turn(ai_reply=response_text)` 存的就是最終 `response`。走白名單時，存進歷史的是整包 Flex JSON，下一輪 agent 讀到自己上一則回覆是一大坨 JSON。這是判定卡目前既有的問題（不在本次範圍），但若 RAG 也走白名單，問題會擴大到最高頻的功能上。

因此：**agent 照常產出純文字，`message_handler` 把純文字存進歷史，卡片在 `reply` 邊界才組。**

## 決策 2：結構化來源走 request-scoped ContextVar

來源要做成可點按鈕就需要 `(label, url)`，而最終文字裡的來源是 `[1] 食藥署：https://...` 這種字串。反解字串很脆：分隔符是全形冒號，而來源名本身也可能含冒號。

`RagAnswerService._append_sources` 內部本來就同時握有重編號後的 `source_lines` 與對應的 `Document`，只是攤平成字串時丟掉了結構。改為在攤平的同時，把 `SourceRef(index, label, url)` 存進 request-scoped ContextVar。

為什麼是 ContextVar 而不是回傳值：`get_rag_answer` 是 LangChain tool，回傳型別只能是字串，結構化資料沒有別的路徑傳到呈現層。這與 `app/core/user_font_size.py`、`app/core/user_language.py` 是同一套模式、同一個理由，新增的 `app/core/rag_sources.py` 沿用它們的形狀（`get_` / `set_` / `reset_` 三件組）。

編號一致性由測試鎖住：結構化來源的 index 必須與文字清單中的 `[n]` 完全對應，兩者不得漂移。

## 決策 3：三層降級，且前兩層讓第三層不該發生

由內而外：

1. **生成端字數上限**（`answer_prompts.py`）。正常情況根本到不了 LINE 的限制。
2. **組卡後量測上線位元組**（`resources/flex_messages/size_guard.py`）。以 `json.dumps(bubble)`（`ensure_ascii=True`，與 `rest.py:155` 一致）計算，超過 9 KB 即退回純文字。門檻取 9 KB 而非 10 KB，是為 LINE 側可能的額外計算方式保留約 10% 餘裕。
3. **builder 拋例外**即退回純文字，比照 `claim_tools.py` 現有的 try/except。

第 2 層是共用 helper，同時接到判定卡。接的位置是 `claim_tools.py::_to_flex_message_text` 而非 `verdict_flex.py`：退回純文字的決策點在前者（`_format_verdict_reply` 已是它的 fallback），builder 維持只負責組裝的單一職責。判定卡超限時目前的行為是「LINE 回 400 → 例外被吞 → 使用者完全收不到回覆」，接上這道防線後會退回既有的 `_format_verdict_reply` 純文字判定。

量測數據（以本次的衛教卡版型，`large` 字級，三個來源按鈕）：

```
版型骨架（0 字答案）        = 1,839 bytes
答案本文可用空間            = 8,401 bytes
→ 答案本文上限約 1,400 個中文字
```

字數上限設在 400–500 字，距離技術極限有將近三倍餘裕。這個數字不只是為了繞開限制：對高齡使用者而言，LINE 卡片裡塞 1,400 字本來就是壞設計。

**已知的證據缺口**：`evals/rag/golden.jsonl` 只存 query 與 expected substrings，不存完整答案，因此無法從既有資料給出「production 答案長度的實際分布」。「一般答案不會接近 1,400 字」是從 `answer_prompts.py` 目前沒有任何長度約束、加上這類問答的典型輸出推得，不是量測結果。第 2 層防線正是為了讓這個推論即使錯了也不會傷到使用者。

## 決策 4：卡片路徑剝除 RAG 前綴，prompt 不動

「以下為 RAG 回應：」是靠 `app/services/agent/prompt.py` 的 system prompt 約束的軟規則，不是程式碼強制。

卡片不放前綴，但**不改 prompt**：prompt 是軟約束，模型本來就不保證照做；而純文字路徑（查不到、降級）仍然需要這個前綴。因此由組卡時剝除首行前綴——剝除是確定性的，改 prompt 不是。

剝除須對所有支援語言的前綴生效，比照 `all_sources_headings()` 的既有做法。

## 決策 5：卡片路徑補上語音

`reply.py` 目前只有純文字分支會呼叫 `_append_tts_audio_message`。若不處理，開啟語音回覆的使用者會在 RAG 回覆上靜默失去語音。

卡片分支改為同樣附加語音訊息，合成用的文字取**卡片組裝前的純文字**（不是卡片 JSON）。

Quick Reply 掛在陣列最後一則的既有行為不變。

## 版型

**衛教問答卡**（`get_rag_answer`）

```
header   衛教資訊（BRAND 底色）
body     ├ 問句塊（SURFACE_ALT 底、圓角）：caption「你問的」+ body 使用者問句
         ├ 答案本文（body，保留 [1][2] 引註標記）
         ├ separator
         └ 「參考資料來源」section title
footer   來源按鈕 ×N（FlexTheme.secondary_button，URI action）
```

所有 size 取自 `resolve_theme()`，不寫死。來源最多 3 筆，沿用 `rag-responses` 既有的上限與重編號規則。

**上傳文件問答卡**（`answer_from_uploaded_document`）

同版型但**沒有來源區段與 footer**——`UserDocumentAnswerService.answer()` 只回傳答案本文，不產生來源清單。header 文案區隔為「文件內容問答」，避免與衛教知識庫混淆。

## 測試策略

依 `openspec/config.yaml` 的規則，一律以依賴注入傳入 mock，不使用 monkey patch。

- 三種字級各產一張卡，斷言 size keyword 與 `_SIZE_SCALE` 一致——這是本次功能的核心斷言
- 超過大小門檻 → 退回純文字；builder 拋例外 → 退回純文字
- 結構化來源的 index 與文字清單的 `[n]` 一致
- `is_rag_fail()` 為真的輸出不得變成卡片
- 卡片分支在 `voice_reply_enabled=true` 時確實附加 `AudioMessage`
- 前綴剝除對所有支援語言生效
- 判定卡超過門檻時退回 `_format_verdict_reply`
