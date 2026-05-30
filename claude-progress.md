# claude-progress.md - Project Progress & Session Memory

## 當前已驗證狀態 (Current Verified State)

- **標準啟動指令 (Standard Start Command)**: `python -m uvicorn app.main:app --port 8000 --reload --reload-exclude .venv`
- **標準驗證路徑 (Standard Verification Path)**: `python -m pytest tests/ -v`
- **當前最高優先級未完成功能 (Highest Priority Pending Feature)**: [無]
- **當前 Blocker (Current Blockers)**: [無]

---

## 會話記錄 (Session History)

### 會話 [2026-05-30]
- **本輪目標 (Goals)**: 
  - 優化 RAG 回應的參考網址顯示邏輯，並限制輸出為關聯度最高的前 3 筆資料。
  - 設計並實現 LINE Location Quick Reply 位置互動雙工作流與 Google 地圖導航連結。
  - 為多人開發團隊整理並提交一套通用、乾淨、無本機絕對路徑的 AI 開發輔助配置 (Harness)。
- **已完成 (Completed)**:
  - **RAG 網址優化 & 限制 3 筆**：修改 `rag_answer_service.py` 讓沒 `source_name` 只有 `url` 的文檔也能顯示，並將 context 及來源列表限制為前 3 筆。更新了 `SYSTEM_PROMPT` 規則 7，並在 `Agent.invoke` 加裝防禦性提取後補機制。
  - **位置 Quick Reply 與地圖雙工作流**：在 `medical_tools.py` 新增 `request_location_quick_reply` 工具，並修改 `SYSTEM_PROMPT` 規則 5 與 8。在 `LineMessageService` 的 `send_line_reply` 整合 LINE 快速回覆位置按鈕。在 `format_facility_list` 中，為醫療院所地址加上 URL 編碼的 Google Maps 搜尋連結。
  - **多人團隊 AI Harness 配置整理**：重建並優化了 `CLAUDE.md`、`AGENTS.md`、`init.sh`/`init.ps1`（改為相對路徑並自動檢測建立虛擬環境）、`feature_list.json` 以及 `claude-progress.md`，完全去除了個人本機的絕對路徑。
- **執行過的驗證 (Verification Run)**: 執行 `pytest tests/` 通過全部 106 個自動化測試案例。
- **下一步最佳動作 (Next Best Action)**: 開啟並啟動 Line Webhook 或是 UI/LIFF 網頁測試，驗證 RAG 對答與位置快速回覆按鈕在真實前端顯示的效果。
