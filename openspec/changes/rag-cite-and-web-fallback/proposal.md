## Why

現行 RAG 在「有向量命中但內容不足」時仍強制附上知識庫來源，且來源編號會因跳過無 URL／重複 URL 而出現斷號（例如單獨的 `[3]`），損害使用者信任。知識庫完全無命中時也只能回「請換方式描述」，無法上網補齊可信政府來源。

## What Changes

- 修正來源引用：僅對實際輸出的來源從 1 連續編號；答案判定為「無法回答」時不附 KB 來源
- 新增 Web Fallback：KB 無命中，或有 docs 但模型判定內容不足時，以 Firecrawl Search + Scrape 上網補齊
- Web 結果僅允許白名單網域（寫死於程式碼）：`gov.tw`、`hpa.gov.tw`、`cdc.gov.tw`、`mohw.gov.tw`
- Web 來源須標註類型（例如答案註明「以下參考網路公開資料」，來源列可標「網路」）
- 維持單一工具 `get_rag_answer`（對 agent 透明）；無 feature flag，改完即永久啟用
- 來源上限仍為最多 3 筆；KB 與 Web 不混用同一則回答
- KB 與 Web 皆失敗時，清楚說明無法回答且不附任何來源
- **非本次範圍**：知識回報／審核入庫、弱命中分數門檻、完整 Admin

## Capabilities

### New Capabilities

- （無）Web Fallback 併入既有 RAG 行為規格，不另開 capability

### Modified Capabilities

- `rag-responses`：無命中處理改為可走 Web Fallback；新增「無法回答不附來源」、連續編號、網路來源標註與白名單約束（`line-reply-rules` 仍保留工具回傳的「參考資料來源」純文字，無需改 requirement）

## Impact

- **程式**：`app/services/rag/answer_service.py`（cite 與 orchestrate）、新增 web fallback／Firecrawl adapter、`app/dependencies.py` 組裝、可能微量調整 `app/tools/rag_tools.py`
- **API／route**：無新對外 REST route；仍經既有 LINE → agent → `get_rag_answer`
- **依賴**：Firecrawl API（Search + Scrape）；需設定金鑰（如 `FIRECRAWL_API_KEY`）
- **測試計畫**：單元測試覆蓋 cite 連續編號、「無法回答」不附來源、空 docs → web、web 失敗不附來源、白名單過濾；外部呼叫以 adapter mock，禁止 monkey patch
- **Spec**：合併後更新 `openspec/specs/rag-responses/`
