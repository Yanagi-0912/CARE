## Context

CARE Agent 以 LangGraph：`guardrail → agent → tools`。`allow_rag` 由 Guardrail 分類器決定；為 True 時才綁定 `get_rag_answer`。現有 `SYSTEM_PROMPT` 對院所／位置有硬規則，對衛教／識詐沒有「必須查庫」規則，導致模型常直接回答。白名單已含 `*.gov.tw`，適合承載衛福部／食藥署／165 等官方醫療詐騙／假藥內容；ingest 管線已存在。

## Goals / Non-Goals

**Goals:**

- Agent 角色明確涵蓋「醫療場景識詐」（假藥、假醫師、假醫院簡訊、保證療效、要求匯款／點連結的醫療話術等）。
- 當 `get_rag_answer` 已提供時：健康衛教與疑似醫療詐騙問題 MUST 先呼叫該工具再作答。
- Guardrail 將醫療詐騙相關訊息判為可啟用 RAG。
- 提供可 ingest 的官方種子 URL 清單，方便補齊知識庫。

**Non-Goals:**

- 不做一般（非醫療）打詐專用 bot／獨立模式切換 UI。
- 不新增報案 API、不自動撥打 165、不做法務判定。
- 不在本 change 強制執行線上 ingest（僅提供清單與文件說明）。
- 不改 LIFF、不改 knowledge-reports 審核流程。
- 不擴充白名單（除非種子 URL 證明需要；預設 `gov.tw` 足夠）。

## Decisions

1. **Prompt 擴充，不新增獨立 tool**  
   - 醫療識詐仍走 `get_rag_answer`（＋既有 CRAG web fallback）。  
   - 替代方案：新增 `check_medical_scam` tool → 增加維護成本且與 RAG 重複。採用擴充 prompt＋docstring。

2. **Guardrail 擴充分類語意，不另建第二道分類器**  
   - 在 `_CLASSIFICATION_PROMPT` 加入醫療詐騙／假藥／可疑醫療訊息等類別。  
   - 替代方案：關鍵字規則 → 易漏；雙分類器 → 延遲與成本加倍。

3. **「必須呼叫 RAG」寫進 SYSTEM_PROMPT 硬規則**  
   - 僅在工具已綁定時適用（`allow_rag=True`）；寒暄仍可不呼叫。  
   - 無法以單元測試保證 LLM 100% 遵守，但以 prompt 契約＋工具 docstring 雙重訊號；後續可用 eval harness 抽樣。

4. **種子 URL 以 repo 檔案列出**  
   - 路徑：`resources/medical_anti_fraud_seed_urls.txt`（一行一個 https URL，`#` 註解）。  
   - 營運用既有 `scripts/ingest_url.py` 逐筆 ingest。

5. **緊急匯款話術**  
   - Prompt 要求強烈勸阻並提及可向 165 反詐騙諮詢專線／官方管道查證；不代替報案、不保證個案判定。

## Risks / Trade-offs

- [Risk] Prompt 過長或規則過多，模型仍偶發不呼叫工具 → Mitigation：規則精簡、docstring 同步；後續可加 eval。  
- [Risk] 知識庫尚無醫療詐騙文，查庫後仍「無資料」→ Mitigation：種子 URL＋web fallback（白名單內）。  
- [Risk] Guardrail 過寬把無關訊息開 RAG → Mitigation：分類語意仍綁「醫療／健康／醫療詐騙」，非一般網購詐騙。  
- [Risk] 使用者把 CARE 當執法單位 → Mitigation：prompt 明確非執法、緊急匯款勸阻＋官方管道。

## Migration Plan

1. 部署程式（prompt／guardrail／docstring／種子檔）。  
2. 對種子 URL 執行 ingest（手動或作業）。  
3. 以 LINE 抽樣：症狀問題、假藥／假醫院簡訊問題，確認有查庫與來源。  
4. Rollback：還原上述檔案即可；知識庫 chunk 可另清或保留無害。

## Open Questions

- 種子 URL 最終清單以衛福部／食藥署／165 公開頁為準，實作時選定 3–8 筆穩定連結；若連結失效改註解並換頁。
