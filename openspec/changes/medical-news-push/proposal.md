# 每日醫療消息卡與認同分享

## Why

CARE 目前只在使用者主動發問時提供衛教資訊。已建立的用藥資料（`medications`）沒有被用來
主動保護使用者：藥品被回收、出現新的安全警訊、供應短缺時，正在服用該藥的人不會從 CARE
得到任何訊息，即使那些公告就掛在食藥署的官網上。

同時，家庭功能目前是單向的——家人收得到用藥逾時通報，但使用者沒有任何方式主動把一則
他認為重要的健康消息轉給家人。族譜已經建好，卻沒有一條「分享」的路。

本提案新增每日一則的醫療消息卡，並在卡片上提供「認同分享」按鈕，按下即把該則消息轉給
族譜成員。

## What Changes

**新增能力 `medical-news-push`**，由三個獨立單元組成：

1. `DrugNewsIndexService`（每日背景排程，與使用者無關）——收集全體使用者用藥中的
   不重複藥名／成分，經既有 `WebSearchService`（官方域限定）搜尋、判定、摘要後寫入
   `drug_news` collection。快取鍵是藥名，不是使用者。
2. `MedicalNewsPushScheduler`（每日背景排程，與使用者相關）——為每位使用者挑一則卡片
   推出：優先推命中其用藥的 Tier 1 警訊，沒有命中則退回 Tier 2 的一般衛教時事。
3. `MedicalNewsShareService`（事件驅動）——處理 `share_medical_news` postback，把該則
   消息以去個人化的形式推給族譜成員。

**Tier 2 的內容來自既有知識庫**（`CARE_database.health_articles_chunks`，由 CARE-data 每日
ETL 維護），依 `published_at` 取近期文章，不新增外部依賴。

**修改**：
- `app/services/line_messaging/dispatcher/dispatcher.py`：新增一個 postback action 分支
- `app/main.py`、`app/dependencies.py`：新排程器的啟動與組裝
- `app/repositories/medication_repository.py`：新增「列出全體不重複用藥藥名」的查詢

**不修改**：既有 RAG 問答流程、`WebSearchService` 的行為、用藥提醒排程器、LIFF 前端。

## Impact

**API/route**：不新增對外 route。整條流程由排程器與 LINE postback 驅動。

**推播量**：每位使用者每日至多一則。用藥提醒目前每位使用者每月已產生約 90～180 則 push
（三時段 × 三階遞進），本功能新增約 30 則／月，增幅約 20～30%。LINE 官方帳號方案的實際
月配額**尚未查證**，實作前須確認（見 design.md「證據缺口」）。

**外部呼叫**：搜尋與抓取沿用既有的 Firecrawl／搜尋客戶端，成本隨「全體不重複藥品數」而非
使用者數成長。

**測試計畫**：
- `tests/unit/services/medical_news/test_relevance.py`——相關性後置檢查與去個人化剝除（純函式）
- `tests/unit/services/medical_news/test_index_service.py`——索引服務（注入替身，不 monkey patch）
- `tests/unit/services/medical_news/test_push_scheduler.py`——兩層選材、每日一則上限、原子搶佔
- `tests/unit/services/medical_news/test_share_service.py`——去個人化、收件人解析、族譜為空
- `tests/unit/services/line_messaging/test_medical_news_flex.py`——兩種卡片版面與大小上限
- `tests/unit/repositories/test_medical_news_repository.py`——唯一索引與去重

## 不在本次範圍

- **衛福部本部「真相說明」爬蟲**：屬 CARE-data 的 ETL 新增來源，與本功能無程式相依，
  且 Tier 2 以現有語料已足夠。另開 CARE-data 變更處理，順帶修正 `hpa.gov.tw` 被標成
  「衛福部闢謠網站」的來源名誤標（`hpa.gov.tw` 是國民健康署，非衛福部本部）。
- **LIFF 呈現**：本功能完全在 LINE 聊天室內完成，不新增 LIFF 頁面。
- **交互作用檢查、重複成分偵測、劑量安全計算**：本功能只轉述已公開的消息，不做任何
  臨床判斷。
