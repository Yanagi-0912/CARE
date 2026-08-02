## 1. 資料層

- [x] 1.1 Mongo getter＋indexes；KnowledgeReport model／repository
- [x] 1.2 KnowledgeReportService（create／list／approve＋IngestService／reject）
- [x] 1.3 單元測試（mock collection／ingest）

## 2. API／Agent

- [x] 2.1 Config `KNOWLEDGE_REPORTS_ADMIN_API_KEY`；user＋admin routers；DI；main include
- [x] 2.2 Tool `submit_knowledge_report`＋contextvars line_user_id；message_handler set／reset；registry
- [x] 2.3 API／tool／registry 測試

## 3. LIFF

- [x] 3.1 `knowledgeReportsApi.ts`＋KnowledgeReports 頁去 mock
- [x] 3.2 前端測試更新

## 4. 收尾

- [x] 4.1 跑 CARE／LIFF 相關測試
- [x] 4.2 勾選 tasks；commit＋push CARE `jamesbranch`（LIFF 若可則推對應 branch）
