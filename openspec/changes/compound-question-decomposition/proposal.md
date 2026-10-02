## Why

長輩常在一則訊息裡問兩件事，例如：

> 「我三酸甘油脂偏高，最近買了魚肝油，以為跟魚油一樣。如果我正在吃降血脂藥，這兩種補充品到底有什麼差別？我還需要注意哪些用藥問題？」

現行 RAG 對這類**複合問題**一律「一次查詢定生死」：

1. Agent 從不拆題（26 題實測，每題恰好呼叫一次 `get_rag_answer`），常把兩個問題揉成一串關鍵字，或由本地 RAG 分流捷徑把原句整段送去檢索。
2. CRAG 對整題只分一次級，`ambiguous` 與 `incorrect` 同樣視為知識庫不足。只要其中一個子問題查不到，**整題**就回「知識庫無資料」——另一半明明答得出來也一起丟掉。

以 8 題複合題的離線評測（2026-09-27～30，線上同映像、Web Fallback 關閉、每條件 3 輪）量化：

| 條件 | 至少答到一個子問題 |
|---|---|
| 現況 | 約 2.3–3 / 8 |
| 本變更（RAG 層條件式拆題、嚴格 CRAG） | 約 5–6 / 8 |

單題問得出來的子問題，放進複合題後現況只保住約一半；改為逐子問題處理後可全數保住（以人工子問題為上限時 8/8）。延遲只多一次拆題 LLM 呼叫（p50 約 1.5–2 秒）；子問題的檢索、精排、分級並行，答出題目的 RAG 段 p50 維持約 3 秒。

完整實驗紀錄（A／B／C／C2／C3／C4／C5 各版本、失敗方案與數據）見 `design.md`。

## What Changes

1. **RAG 層條件式拆題**：`get_rag_answer` 收到查詢時，若功能開關開啟、請求語言為 zh-TW、原始使用者訊息含 2 個以上問號，交給拆題器（一次 LLM 呼叫）判斷是否為多個獨立問題；判為 compound 才走拆題路徑，否則照現行流程，行為完全不變。
2. **拆題器輸出兩種寫法**：每個子問題有 `question`（回答用，只保留「拿掉會改變答案」的條件）與 `retrieval_query`（檢索用，只留核心主題）。
3. **逐子問題處理**：每個子問題以 `retrieval_query` 各自檢索、精排（只用自己的候選，不與其他子問題或原句合併），再以 `question` 各自跑**嚴格** CRAG（只有 `correct` 放行，與現行一致）。
4. **只把通過的子問題交給生成**：生成 prompt 只列通過的子問題、不含原始整句；未通過的子問題由程式補一句固定說明（不含引用、六語 i18n）。參考來源上限仍為 3 筆。
5. **所有子問題都不通過、拆題器失敗或判為單一問題時，退回現行流程**（含 Web Fallback），不會比現況更差。
6. **原始使用者訊息的傳遞**：`Agent.invoke` 以 request-scoped ContextVar 保存本輪使用者原文；**只有** `get_rag_answer` 會把它明確傳給 `RagAnswerService.answer(..., original_message=...)`。藥單問答、查核等其他內部呼叫 RAG 的路徑不受影響。
7. **功能開關** `RAG_COMPOUND_DECOMPOSE_ENABLED`，預設 `false`。

**不在本變更範圍**（實驗已做但尚需把關，留待後續變更）：CRAG 兩層判定／部分支持與適用範圍說明（需臨床審核放行內容）、拆題時誤刪會改變答案的條件（例如「頭暈是否缺水」刪掉心臟／腎臟病）、生成端的過度概括與夾雜外文、以「；」等非問號連接的複合訊息、zh-TW 以外語言。

## Capabilities

### New Capabilities

- `rag-compound-questions`：複合問題的偵測、拆題、逐子問題檢索／分級、合併生成與退回條件。

### Modified Capabilities

（無。既有 `rag-crag`、`rag-responses` 的行為在拆題路徑之外完全不變；拆題路徑沿用同一套分級、參考來源上限與無法回答標記。）

## Impact

- **API／route**：無。LINE webhook 與 `/api/*` 介面不變。
- **程式**：
  - 新增 `app/services/rag/question_decomposer.py`（閘門判斷、拆題器）
  - `app/services/rag/answer_service.py`：`answer()` 新增關鍵字參數 `original_message`；新增拆題路徑
  - 新增 `app/core/user_message.py`（本輪使用者原文 ContextVar）
  - `app/services/agent/agent.py`：`invoke` 設定／還原 ContextVar
  - `app/tools/rag_tools.py`：`get_rag_answer` 傳入原文
  - `app/i18n/messages.py`：新增未通過子問題的固定說明（六語）
  - `app/core/config.py`、`.env.example`、`app/dependencies.py`：功能開關與組裝
- **成本**：開關開啟且判為 compound 的訊息，多一次拆題呼叫（Gemini，與查詢改寫同模型與 thinking 設定）；每個子問題各一次檢索、精排（Cohere）、分級。
- **測試計畫**：
  - `tests/unit/services/rag/test_question_decomposer.py`：閘門、JSON 解析、單一／compound／子問題上限／欄位缺漏
  - `tests/unit/services/rag/test_answer_service_compound.py`：開關關閉、無原文、非 zh-TW、單一問題、全部通過、部分通過、全部不通過退回、拆題器例外退回、生成拒答標記處理
  - `tests/unit/core/test_user_message.py`：ContextVar 預設值與還原
  - `tests/unit/tools/test_rag_tools_original_message.py`：`get_rag_answer` 傳遞原文
  - 既有 `tests/unit/services/agent/test_urgency_routing.py`、`tests/unit/services/safety/test_emergency_pipeline.py` 的假 RAG 服務改為接受 `original_message`
  - `./init.sh` 全綠
