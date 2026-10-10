## 1. 原始使用者訊息的傳遞

- [x] 1.1 新增 `app/core/user_message.py`：request-scoped ContextVar（預設 None）與 set／reset／get
- [x] 1.2 `Agent.invoke` 在執行圖之前設定本輪原文，`finally` 還原
- [x] 1.3 `get_rag_answer` 以 `original_message=` 傳入原文
- [x] 1.4 測試：`tests/unit/core/test_user_message.py`、`tests/unit/tools/test_rag_tools_original_message.py`
- [x] 1.5 既有假 RAG 服務改為接受 `original_message`：`tests/unit/services/agent/test_urgency_routing.py`、`tests/unit/services/safety/test_emergency_pipeline.py`

## 2. 拆題器

- [x] 2.1 新增 `app/services/rag/question_decomposer.py`：`SubQuestion`、`QuestionDecomposer` 協定、`looks_compound()`（斷句後至少兩段非空文字）、`GeminiQuestionDecomposer`（prompt 與實驗 C3 v3_split 相同；解析 JSON；子問題上限 3；缺 `retrieval_query` 時沿用 `question`）
- [x] 2.2 測試：`tests/unit/services/rag/test_question_decomposer.py`

## 3. RAG 服務的拆題路徑

- [x] 3.1 `RagAnswerService.__init__` 新增 `decomposer` 注入；`answer()` 新增關鍵字參數 `original_message`
- [x] 3.2 閘門（開關＝有注入 decomposer、有原文、zh-TW、斷句後至少兩段非空文字）＋拆題器判 compound 才走拆題路徑，路徑在總逾時之內
- [x] 3.3 逐子問題以 `retrieval_query` 檢索＋精排（只用自己的候選）、以 `question` 跑嚴格 CRAG，三者並行
- [x] 3.4 通過的子問題輪流分配文件名額；生成 prompt 只列通過的子問題、不含原句
- [x] 3.5 無法回答標記：有引用則移除標記，只有標記則退回現行流程
- [x] 3.6 未通過子問題補固定句（i18n `rag.compound_unsupported`，六語，不含引用）；沿用死鏈檢查與 `_append_sources`
- [x] 3.7 退回條件：拆題失敗、全部未通過、分級例外、生成只寫標記 → 現行流程；日誌只記數量不記原文
- [x] 3.8 測試：`tests/unit/services/rag/test_answer_service_compound.py`

## 4. 設定與組裝

- [x] 4.1 `app/core/config.py` 新增 `RAG_COMPOUND_DECOMPOSE_ENABLED`（預設 false）；`.env.example` 加註理由
- [x] 4.2 `app/dependencies.py`：開關開啟時以 `MODEL_NAME`＋查詢改寫同一 thinking 等級建立拆題器並注入 `RagAnswerService`

## 5. 驗收

- [x] 5.1 `./init.sh` 全綠
- [x] 5.2 commit、開 PR（繁中描述附實驗數據摘要）

## 6. Healthnews 100 題斷句閘門與逐題試跑（2026-10-10）

- [x] 6.1 以逗號、分號、句號、問號、驚嘆號與換行的兩段非空文字觸發 LLM 分析，排除數字中的半形逗號與小數點
- [x] 6.2 保留 single 判斷與必要條件 prompt；測試 HN033/HN066、單一問句、多病史及句尾標點
- [x] 6.3 新增 `scripts/healthnews_experiment.py`：原始 question 與 original_message、禁止參考答案輸入、關閉網搜／回寫、完整證據與 grader 有效輸入、取消與降級事件、生成 context token
- [x] 6.4 新增 `tests/unit/scripts/test_healthnews_experiment.py`：原文隔離、截斷輸入、API 錯誤、並行取消、Jev 逾時與實體快照核驗
- [x] 6.5 唯讀即時庫小批試跑 8 題 × B/D × 3 輪＝48 筆；與正式結果分開保存，醫療／獨立證據審查保持待確認
- [x] 6.6 獨立程式審查與 `./init.sh`：9,823 passed、9 skipped
- [ ] 6.7 人工審核與題庫凍結、舊新固定快照、正式四組執行及醫療審查（舊快照缺失；持久新快照建立遭自動審核拒絕，等待使用者批准）
