## ADDED Requirements

### Requirement: Web fallback 成功後觸發知識回報

當 `RagAnswerService` 因知識庫不足（空檢索、CRAG `incorrect`、或 `ambiguous` 且 rewrite 後仍不足）而成功取得白名單網路回答時，系統 SHALL 將該次查詢與引用來源 URL 交給知識回報流程建立 pending（見 knowledge-reports）。此步驟 SHALL NOT 改變已回傳給代理的網路答案內容；觸發失敗時 SHALL 僅記錄錯誤。

#### Scenario: CRAG incorrect 網路成功後建報

- **WHEN** CRAG 評為 incorrect、web fallback 成功並附白名單來源
- **THEN** 代理仍收到網路答案，且系統建立對應 pending 知識回報

#### Scenario: 僅知識庫答案不建報

- **WHEN** 知識庫檢索充足並直接生成答案（未走 web fallback）
- **THEN** 系統不因此建立知識回報
