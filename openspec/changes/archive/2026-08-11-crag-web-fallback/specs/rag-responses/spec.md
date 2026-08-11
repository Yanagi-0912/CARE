## MODIFIED Requirements

### Requirement: RAG 與網路搜尋工具閘門

系統 SHALL 僅在 guardrail 判定訊息與健康醫療相關（`allow_rag = True`）時，才對代理提供 `get_rag_answer` 工具。位置座標訊息 SHALL NOT 啟用該工具。代理工具集 SHALL NOT 再提供獨立的 `search_public_web`；公開網路補充 SHALL 由 `RagAnswerService` 在知識庫不足時內部觸發（經 `WebSearchService`／Firecrawl／白名單）。

#### Scenario: 非健康問題不啟用 RAG

- **WHEN** guardrail 判定使用者訊息與健康醫療無關
- **THEN** 該輪不提供 `get_rag_answer` 給代理

#### Scenario: 健康問題僅提供 get_rag_answer

- **WHEN** guardrail 判定允許 RAG（`allow_rag = True`）
- **THEN** 工具集可含 `get_rag_answer`，且 SHALL NOT 含 `search_public_web`

## REMOVED Requirements

### Requirement: RAG 僅查知識庫

（由「知識庫不足時內建 Web Fallback」取代。）

### Requirement: 公開網路搜尋工具

（代理不再暴露 `search_public_web`；行為併入 `RagAnswerService` web fallback。`WebSearchService` 與 `web_tools.search_public_web` 可保留供內部／測試，但 SHALL NOT 出現在 `get_all_tools`。）

## ADDED Requirements

### Requirement: 知識庫不足時內建 Web Fallback

`get_rag_answer` / `RagAnswerService` SHALL 先檢索內部知識庫（含 CRAG 分級與最多一次 query rewrite，若已啟用）。當下列任一情況成立且 web fallback 已啟用並已注入 `WebSearchService` 時，SHALL 呼叫該服務回答（沿用既有 Firecrawl 搜尋、允許網域白名單、回答前綴「以下參考網路公開資料」、來源以「網路：」標示），而非僅回傳知識庫無命中字串：

1. 知識庫檢索（含精排）結果為空
2. CRAG 評為 `incorrect`
3. CRAG 評為 `ambiguous` 且一次 rewrite 後仍不足（空結果、`incorrect`、或再次 `ambiguous`）

當 web fallback 關閉、未注入、搜尋無可用頁面、服務失敗或模型無法回答時，SHALL 回傳友善提示（既有無命中／無法提供類訊息），而非拋出未處理例外。

#### Scenario: 空檢索走 web

- **WHEN** RAG 檢索未命中任何文件且 web fallback 已啟用
- **THEN** 系統呼叫 `WebSearchService`（Firecrawl＋白名單）嘗試回答

#### Scenario: CRAG incorrect 走 web

- **WHEN** CRAG 評為 `incorrect` 且 web fallback 已啟用
- **THEN** 系統不生成知識庫答案，改呼叫 `WebSearchService`

#### Scenario: web 亦無結果

- **WHEN** 觸發 web fallback 但無可用允許網域頁面或服務失敗
- **THEN** 回傳友善提示，不中斷流程

#### Scenario: web fallback 關閉

- **WHEN** `RAG_WEB_FALLBACK_ENABLED` 為 false 或未注入 `WebSearchService`，且知識庫不足
- **THEN** 回傳知識庫無命中／無法提供類訊息，且不進行網路搜尋

### Requirement: get_rag_answer 說明內含網路補充

`get_rag_answer` 的工具說明 SHALL 描述：必要時會在知識庫不足後參考允許網域的公開網路資料；SHALL NOT 指示代理另呼叫 `search_public_web`。

#### Scenario: docstring 不再指向獨立 web tool

- **WHEN** 代理讀取 `get_rag_answer` 工具描述
- **THEN** 描述不要求呼叫 `search_public_web`
