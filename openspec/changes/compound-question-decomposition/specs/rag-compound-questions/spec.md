## ADDED Requirements

### Requirement: 複合問題的偵測閘門

系統 SHALL 只在下列條件**全部**成立時嘗試拆題，任一不成立即走現行 RAG 流程、行為與未導入本功能時完全相同：

1. 功能開關 `RAG_COMPOUND_DECOMPOSE_ENABLED` 開啟；
2. 呼叫端提供了本輪原始使用者訊息（`original_message`）；
3. 請求語言為 zh-TW；
4. 原始使用者訊息含 2 個以上問號（全形或半形）。

閘門成立後，系統 SHALL 以一次 LLM 呼叫判斷訊息是否包含多個需分別查資料的獨立問題；僅在判為 compound 且子問題數 ≥ 2 時走拆題路徑。子問題數 SHALL NOT 超過 3。

同一問題帶有多個條件（例如「同時有糖尿病和腎臟病的長輩能不能吃高蛋白」）SHALL 視為單一問題。

#### Scenario: 開關關閉不拆題

- **WHEN** `RAG_COMPOUND_DECOMPOSE_ENABLED` 為 false
- **THEN** 系統不呼叫拆題器，以呼叫端給的查詢走現行流程

#### Scenario: 只有一個問號不拆題

- **WHEN** 原始使用者訊息只含一個問號
- **THEN** 系統不呼叫拆題器

#### Scenario: 非 zh-TW 不拆題

- **WHEN** 請求語言不是 zh-TW
- **THEN** 系統不呼叫拆題器

#### Scenario: 拆題器判為單一問題

- **WHEN** 拆題器判定訊息只是一個問題，或回傳少於 2 個子問題
- **THEN** 系統以呼叫端給的查詢走現行流程

### Requirement: 只有衛教問答工具傳入原始訊息

`get_rag_answer` SHALL 將本輪原始使用者訊息以 `original_message` 傳給 RAG 服務。其他在內部呼叫 RAG 服務的路徑（藥單問答、主張查核等）SHALL NOT 傳入，以免其查詢中的附加內容被拆題改寫。

#### Scenario: 衛教問答傳入原文

- **WHEN** agent 以任意查詢字串呼叫 `get_rag_answer`
- **THEN** RAG 服務收到的 `original_message` 為本輪使用者原文

### Requirement: 逐子問題檢索與嚴格分級

拆題路徑中，每個子問題 SHALL 具有兩種寫法：`question`（回答用，只保留會改變答案的條件）與 `retrieval_query`（檢索用，只留核心主題）。系統 SHALL：

1. 以各自的 `retrieval_query` 分別檢索與精排，候選 SHALL NOT 與其他子問題或原句的候選合併；
2. 以各自的 `question` 對其精排結果執行 CRAG 分級，只有 `correct` 視為通過（`ambiguous` 與 `incorrect` 皆不通過，與現行標準相同）；
3. 各子問題的檢索、精排與分級 SHALL 並行執行。

#### Scenario: 子問題各自檢索

- **WHEN** 訊息被拆成兩個子問題
- **THEN** 檢索器以兩個 `retrieval_query` 各被呼叫一次，且不以原句檢索

#### Scenario: 分級使用回答用問題

- **WHEN** 系統對子問題執行 CRAG
- **THEN** 分級輸入的問題為該子問題的 `question`

### Requirement: 合併生成與未通過子問題的說明

至少一個子問題通過時，系統 SHALL 以一次生成回答所有通過的子問題：

- 送入生成的文件 SHALL 由各通過子問題的精排結果輪流取用，總數不超過精排上限；
- 生成 prompt SHALL 只列出通過的子問題，SHALL NOT 包含原始使用者訊息；
- 未通過的子問題 SHALL 由程式在答案本文後補上固定說明（i18n），該說明 SHALL NOT 含引用標記；
- 參考來源仍 SHALL 依既有規則最多 3 筆、只列實際被引用者。

若模型輸出含無法回答標記但同時含引用，系統 SHALL 移除標記後照常輸出；若只有無法回答標記，SHALL 退回現行流程。

#### Scenario: 全部通過

- **WHEN** 兩個子問題都通過 CRAG
- **THEN** 答案涵蓋兩個子問題，且不含未通過說明

#### Scenario: 部分通過

- **WHEN** 第一個子問題通過、第二個未通過
- **THEN** 生成 prompt 只含第一個子問題，答案本文後附上第二個子問題的固定說明，且該說明不帶任何 `[n]`

#### Scenario: 生成 prompt 不含原句

- **WHEN** 系統為通過的子問題生成答案
- **THEN** 生成 prompt 不含原始使用者訊息全文

### Requirement: 拆題路徑的退回條件

下列任一情況，系統 SHALL 放棄拆題路徑，改以呼叫端給的查詢走現行流程（含 Web Fallback 與既有的無命中處理）：

- 拆題器呼叫或輸出解析失敗；
- 所有子問題都未通過 CRAG；
- CRAG 分級拋出例外；
- 生成只輸出無法回答標記。

#### Scenario: 全部未通過退回

- **WHEN** 所有子問題都未通過 CRAG
- **THEN** 系統以呼叫端給的查詢執行現行流程

#### Scenario: 拆題器失敗退回

- **WHEN** 拆題器拋出例外
- **THEN** 系統留下可觀測日誌，並以呼叫端給的查詢執行現行流程
