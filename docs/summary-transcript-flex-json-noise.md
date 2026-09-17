# 待修：摘要對話稿裡混入整包卡片 JSON

**狀態**：待修正（尚未排入）
**記錄日期**：2026-09-16
**相關程式**：`app/services/consultation/consultation_service.py` 的 `_generate_summary`

## 問題

每日摘要把當天的對話紀錄逐則排成對話稿交給 Gemini。其中 AI 回覆若是工具產生的 Flex 卡片，
存進對話紀錄的就是整段卡片 JSON（版面、顏色、按鈕、URI），摘要時也原封不動塞進 prompt。

成因：`message_handler` 回覆成功後呼叫 `history_service.save_turn`，把 `response_text` 當作
`assistant_reply` 寫進 Mongo 對話紀錄。當回覆被工具接管時，`response_text` 就是要送往 LINE 的
Flex JSON（見 `app/services/agent/agent.py` 的 `medical_tool_names`）。

會以卡片 JSON 存檔的回覆（實作前須逐一確認實際格式）：

| 來源 | 內容 |
|---|---|
| `find_nearby_hospitals` | 附近醫療院所 |
| `find_nearby_facilities_by_department` | 附近特定科別院所 |
| `lookup_medical_facility` | 特定醫療院所 |
| `request_location_quick_reply` | 分享位置 |
| `open_official_site` | 官網／LIFF 入口 |
| `verify_claim` | 查核判定卡 |
| `suggest_department_for_symptom` | 症狀科別建議卡（含紅旗卡） |
| 急迫度短路（`emergency_node`） | 緊急紅卡——**已處理**，見下方「範圍」 |

不受影響：RAG／文件問答的回答卡由 replier 自行組卡，存檔的是組卡前的純文字。

## 影響

- **摘要品質**：真正有用的資訊（建議了哪個科別、查到哪幾家醫院、查核結果）埋在大量版面欄位裡，
  Gemini 可能漏掉，也可能把按鈕文字、URI 當成對話內容。
- **成本與上限**：一張卡片常有數千字元，查醫院多的一天，prompt 大半是版面資料。
- **語言**：卡片文字依使用者當時的語言產生，無法用中文關鍵字辨識卡片種類。

## 範圍

- 只改摘要組對話稿的方式，**不改存檔格式**。LIFF 原始紀錄頁與 agent 讀的歷史照舊。
- 緊急紅卡已處理：卡片頂層帶 `riskAlert`（`RISK_ALERT_KEY`），摘要的 `_transcript_line`
  把它換成「觸發風險警示｜使用者輸入：「…」｜判定原因：…」。其他卡片建議沿用同一做法——
  組卡時在頂層放一個結構化 key，摘要只讀這個 key，不從卡片節點反解文字。
- 限制同樣適用：上線前已存下的卡片沒有這個 key，無法被辨識。

## 通過條件

每一條都要有對應的單元測試。

1. **不再出現卡片 JSON**：上表每一種卡片（紅卡除外），各以一份實際產出的卡片 JSON 作為
   `assistant_reply` 餵進 `_generate_summary`，斷言送給 Gemini 的 prompt 不含 `"type": "bubble"`、
   `"altText"`、`"action"` 等 Flex 結構字串。
2. **保留關鍵資訊**：每種卡片轉成一行可讀文字，並保留該卡片的重點，且斷言 prompt 含有該重點：
   - 科別建議卡：建議的科別；紅旗卡須標示「建議立即就醫」，不得寫成可以慢慢掛號
   - 院所查詢卡：院所名稱
   - 查核判定卡：判定結果
   - 分享位置、官網入口：只標示卡片種類即可
3. **辨識不依賴文字語言**：同一種卡片以 zh-TW 與至少一種外語各測一次，轉換結果的卡片種類相同。
4. **認不出的卡片不外洩也不消失**：合法的 Flex JSON 但不屬於已知種類時，轉成一行通用標記
   （例如 `[系統卡片]`），不輸出原始 JSON，也不整則刪除。
5. **一般文字不受影響**：非 JSON 的 AI 回覆、以 `{` 開頭但不是合法 JSON 的文字，原樣保留。
6. **存檔不變**：`ConversationLogRepository` 與 `save_turn` 無任何改動（以 diff 確認），
   `tests/unit/routers/test_consultations.py` 的原始紀錄測試全數通過。
