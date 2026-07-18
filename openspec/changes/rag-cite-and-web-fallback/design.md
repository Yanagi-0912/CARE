## Context

現行 `RagAnswerService.answer`：檢索 → 若有 docs 則生成並一律 `_append_sources`；若無 docs 則回 `NO_HITS_MESSAGE`。`_append_sources` 以 `enumerate(docs[:3])` 當顯示編號，跳過無 URL／重複 URL 後會出現斷號；「無法回答」仍附 KB 來源。

本次在既有 `get_rag_answer` 工具內完成 cite 修復與 Web Fallback，不改 agent 工具面、不開新 REST API。知識回報／入庫不在範圍。

約束：分層與 DI 組裝於 `app/dependencies.py`；測試禁止 monkey patch，外部服務以注入 mock；LINE 回覆維持純文字。

## Goals / Non-Goals

**Goals:**

- 修復 cite 連續編號與「無法回答不附 KB 來源」
- KB 空或內容不足時走 Firecrawl Search + Scrape，白名單過濾後生成答案並標註網路來源
- 維持單一 `get_rag_answer`；KB 與 Web 不混用同一則回答
- 單元測試覆蓋主路徑（Firecrawl 以 adapter mock）

**Non-Goals:**

- 知識回報、審核、向量入庫
- 弱命中分數門檻、KB+Web 混用 cite
- Feature flag（永久啟用）
- 完整 Admin／改 embedding 模型

## Decisions

### 1. Orchestration 放在 `RagAnswerService`

```text
docs = retrieve(query)
if docs:
  kb_answer = generate_from_kb(docs)
  if can_answer(kb_answer):
    return cite(kb_answer, docs, kind=kb)
  # 無法回答：不附 KB 來源，落入 web
web_docs = web_search_and_fetch(query)  # 白名單內 ≤3
if not web_docs:
  return NO_ANSWER_MESSAGE  # 不附來源
web_answer = generate_from_web(web_docs)
return annotate_web(web_answer) + cite(..., kind=web)
```

**理由：** agent 已依賴單一工具；內部 orchestrate 符合 KISS。  
**替代：** 拆兩個 tool → agent 決策複雜，本次不做。

### 2. 「無法回答」判定（第一版）

以生成文字啟發式偵測（如「不知道」「無法」「未找到」「找不到相關」等），輔以單元測試固定案例。

**理由：** 不改 prompt 契約即可修信任 bug。  
**替代：** 結構化 JSON `{can_answer, text}` 較穩，列為後續優化。

### 3. Firecrawl adapter + DI

新增可注入介面（例如 `WebSearchClient`）：`search(query) -> candidates`、`scrape(url) -> text`。實作呼叫 Firecrawl；測試注入 fake。組裝於 `dependencies.py`；金鑰如 `FIRECRAWL_API_KEY`。

**理由：** 符合後端 DI 慣例、可測、可換供應商。

### 4. 白名單寫死在程式碼

常數允許後綴：`gov.tw`、`hpa.gov.tw`、`cdc.gov.tw`、`mohw.gov.tw`（含子網域）。不進 `.env`。

**理由：** 清單穩定、變更需 code review。  
**注意：** `hpa.gov.tw` 等已涵蓋於 `gov.tw` 後綴；明確列出以表達意圖與文件對齊。

### 5. 來源標註

Web 成功時：答案加一句「以下參考網路公開資料」（或等同意涵純文字）；來源列如 `[1] 網路：{source_name}：{url}`。KB 成功路徑維持現有「參考資料來源」格式（可不加「網路」前綴）。

### 6. Cite 共用邏輯

`_append_sources`（或同等函式）改為對「通過過濾後的輸出清單」從 1 編號；支援可選 `source_kind`（kb／web）。最多 3 筆。

### 7. 無 feature flag

Web Fallback 隨程式上線即啟用。無金鑰或呼叫失敗時降級為「無法回答、不附來源」，不中斷整體 bot。

## Risks / Trade-offs

- **[Risk] Firecrawl 延遲拉長 LINE 等待** → 合理 timeout；失敗快速降級；既有 loading animation 可緩解體感  
- **[Risk] 啟發式誤判「無法回答」** → 測試覆蓋常見句式；誤觸 web 成本可接受；之後可改結構化輸出  
- **[Risk] 白名單過嚴，口語問題仍找不到** → 接受第一版；之後再擴白名單或搜尋 query 改寫  
- **[Risk] 醫療內容品質仍依賴政府頁面** → 審核不在本次；僅限白名單降低論壇風險  
- **[Trade-off] 無開關無法瞬間關掉 web** → 產品已選擇永久開啟；緊急時需 rollback 部署或暫時拿掉 API key 讓其降級

## Migration Plan

1. 實作 cite 修復 + 測試（可先獨立驗證）
2. 接 Firecrawl adapter + 白名單 + web 路徑測試（mock）
3. 設定環境金鑰，本機／staging 驗證真實呼叫
4. 部署後觀察延遲與無來源降級率
5. Rollback：回退該版；或移除／無效化 API key 使 web 路徑失敗並降級

## Open Questions

- Firecrawl 具體 SDK／HTTP 端點與套件版本（實作時依官方文件選定）
- 「無法回答」關鍵字清單是否需產品再潤飾（實作可先收斂一小組，PR 可調）
- `NO_HITS_MESSAGE` 文案在 web 也失敗時是否改為更中性的「目前無法提供…」（建議微調，實作時定稿）
