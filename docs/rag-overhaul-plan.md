# RAG 大改規劃：Web Fallback × 知識回報（LLM Wiki）

> 狀態：草稿（本地 `doc/`，不上 Git）  
> 目標：知識庫查無／不足時可上網補齊並附來源；使用者可回報過時／缺漏，經人工審核後寫回 RAG。  
> 相關前端現況：`CARE-LIFF`「知識回報」頁已有 mock UI，尚未接後端。

---

## 1. 為什麼要改

現行 RAG 痛點（已觀察到）：

| 現象 | 原因 |
| --- | --- |
| 答「無法根據現有資訊…」卻仍附來源 | 有向量 hit，但內容不夠回答；`_append_sources` 仍強制貼前 3 筆檢索結果 |
| 來源編號出現單獨的 `[3]` | 跳過無 URL／重複 URL 後，仍用原始 `enumerate` 序號，沒有重編 |
| 查無知識庫就結束 | 無 web fallback；只回「請換方式描述」 |
| 「知識回報」無法真用 | LIFF 僅 mock；無 API、無審核、無入庫 |

產品期望（本次大改）：

1. **Web Fallback**：知識庫找不到（或判定不足）→ 上網找 → 回答仍附 **最多 3 筆**資料來源（格式對齊一般 RAG）。
2. **知識回報（LLM Wiki 精神）**：使用者說資訊過時／缺漏 → 進入審核佇列 → 工作人員核准後，把提供／蒐集到的資料切片寫進 RAG。

---

## 2. 範圍與非範圍

### 2.1 範圍（In）

- RAG 回答管線重構：命中判定、來源編號、無命中／不足時的 web 補齊
- Web 搜尋／抓頁 → 抽出可用段落 → 生成答案 + 3 筆來源
- 知識回報：建立、列表、狀態機、審核通過後 ingestion
- 對接既有 LIFF「知識回報」追蹤頁（替換 mock）
- LINE 對話內觸發回報的入口（與前端「回 LINE 提問」敘事對齊）

### 2.2 非範圍（Out / 之後再談）

- 全自動無人工審核的寫入知識庫（安全與醫療正確性風險高）
- 完整 Staff Admin 後台（第一版可用簡易 API + 內部工具／n8n／簡易 LIFF 審核角色）
- 重訓／換 embedding 模型（沿用現有向量維度與建庫設定）
- 刪除 Guardrail（本次不動）

---

## 3. 功能一：Web Fallback（找不到就上網）

### 3.1 期望行為

```text
使用者健康問題
    │
    ▼
知識庫向量檢索（top K）
    │
    ├─ 有足夠相關內容 ──► 用 KB 生成答案 + 最多 3 筆來源（重編 [1][2][3]）
    │
    └─ 無命中／相關度過低／模型判定內容不足
            │
            ▼
        Web 搜尋（健康／可信來源優先）
            │
            ▼
        抓取頁面摘要／正文片段（可控長度）
            │
            ▼
        用「網頁內容」生成答案 + 最多 3 筆來源
            │
            ▼
        標註來源類型（建議）：知識庫 / 網路
```

### 3.2 「找不到」的判定（建議分層）

不要只靠「`docs == []`」：

| 層級 | 條件 | 行為 |
| --- | --- | --- |
| A. 硬無命中 | 檢索結果為空 | 直接走 web |
| B. 弱命中 | top1 score < 門檻（需量測後定） | 走 web（或 KB+web 混用，見下方決策） |
| C. 內容不足 | 有 docs，但生成結果表示無法回答／未引用 | 可重試 web，或僅回「不知道」且**不附無關 KB 來源** |

**第一版建議（KISS）：**

1. `docs` 空 → web fallback  
2. `docs` 非空 → 只用 KB 回答；若答案屬於「無法提供／找不到」類 → **不要附 KB 來源**，可選擇性觸發一次 web（可做 feature flag）

### 3.3 來源格式（與現行 RAG 對齊）

回答最下方固定：

```text
參考資料來源：
[1] {source_name}：{url}
[2] ...
[3] ...
```

規則：

- 最多 **3** 筆
- 只對**實際輸出**的來源從 1 連續編號（修掉單獨 `[3]`）
- 缺 `source_name` 仍可只顯示 url
- Web 來源的 `source_name` 可用網站標題或域名
- （建議）在來源前加標籤或在答案中註明「以下參考網路公開資料」，避免使用者以為已進官方知識庫

### 3.4 Web 技術選項（待定）

| 方案 | 優點 | 風險 |
| --- | --- | --- |
| Firecrawl Search + Scrape | 團隊已有 firecrawl 相關 ignore／工具習慣 | 成本、延遲、醫療內容品質不一 |
| 搜尋 API（Tavily / Bing / SerpAPI）+ 自抓 | 可控 | 多一層金鑰與維運 |
| 僅允許白名單網域（衛福部、疾管署、教學醫院…） | 較安全 | 覆蓋率低，「烙賽」類口語可能仍找不到 |

**建議：** 第一版「搜尋 + 白名單過濾（可配置）+ 抓前 N 頁摘要」；禁止任意論壇當唯一依據。

### 3.5 與 Agent 的接法

維持單一工具 `get_rag_answer`（對 agent 透明）內部做 KB → web；或拆成兩個 tool（較複雜）。

**建議 KISS：** 仍一個 `get_rag_answer`，內部 orchestrate：

```text
answer(query):
  docs = retrieve(query)
  if usable(docs):
    return generate_from_kb(docs) + cite(docs, k=3)
  web_docs = web_search_and_fetch(query, k=3)
  if not web_docs:
    return NO_HITS_MESSAGE  # 真的都沒有，不附來源
  return generate_from_web(web_docs) + cite(web_docs, k=3)
```

### 3.6 必須順手修的既有 bug

1. **不知卻附來源**：答案判定為「無法回答」時不呼叫 `_append_sources`  
2. **來源編號斷號**：cite 用「輸出序」而非「docs 原始 index」

---

## 4. 功能二：知識回報（LLM Wiki × 人工審核）

### 4.1 產品故事（對齊前端）

前端 `CARE-LIFF/src/pages/KnowledgeReports` 已定義敘事：

- 入口：首頁／側欄「知識回報」→ 追蹤人工審核進度  
- CTA：回 LINE 提問（目前無 LIFF 內提交表單）  
- 原因類型：`資訊可能過期` / `知識庫未收錄`  
- 狀態：`pending` → `reviewing` → `resolved`（文案：已更新知識庫）

**解讀：** LIFF = 使用者追蹤面板；**真正提交回報**預期走 LINE 對話（或之後加表單）。後端要補齊整條資料管線。

### 4.2 理想流程（LLM Wiki 精神）

```text
使用者在 LINE 說：你們這個資訊過時了 / 缺這個
        │
        ▼
Agent 或專用指令辨識為「知識回報」
        │
        ▼
建立 KnowledgeReport 草稿
  - 原問題 / 原回答摘要
  - 原因（outdated | missing | other）
  - 使用者可選填：來源 URL、補充說明、貼文連結
  - （可選）系統自動幫搜一輪候選來源給審核員
        │
        ▼
進入「資料審核」佇列（pending）
        │
        ▼
工作人員審核
  ├─ 拒絕 → rejected + 原因；LIFF 顯示結果
  └─ 核准 → 選定要用的來源（使用者提供 or 系統蒐集）
              │
              ▼
           Fetch → Chunk → Embed → Upsert Mongo `health_articles_chunks`
              │
              ▼
           report.status = resolved；可寫 resolution 備註
              │
              ▼
           （可選）LINE 通知使用者「知識庫已更新」
```

### 4.3 資料要存哪？（目前未定 → 建議方案）

| 方案 | 說明 | 建議 |
| --- | --- | --- |
| **A. Mongo 新 collection** `knowledge_reports` | 與既有 CARE Mongo 一致、實作快 | **第一版首選** |
| B. 另開 Postgres | 結構化審核好查 | 若團隊之後統一 SQL 再遷 |
| C. n8n / Google Sheet 當佇列 | 快速驗證流程 | 只適合原型，不建議當正式 |
| D. GitHub Issues | 工程向、對使用者不友善 | 否 |

**建議欄位草稿（`knowledge_reports`）：**

```text
_id
report_id          # 對外顯示 KR-YYYY-XXXX
line_user_id
status             # pending | reviewing | resolved | rejected
reason             # outdated | missing | other
question           # 使用者關心的問題／原文
context_answer     # （可選）觸發當下 bot 回覆摘要
user_note         # 使用者補充
user_source_urls  # string[] 使用者提供
candidate_sources  # [{url, title, snippet, collected_by: user|system}]
reviewer_id / reviewer_note
resolution         # 審核結論文案（給 LIFF 看）
ingest_job         # {status, chunk_ids[], source_urls[], error?}
created_at / updated_at
```

向量庫仍寫既有：`health_articles_chunks`（`chunk_content` / `embedding` / `source_name` / `url` + 建議加 `report_id`、`ingested_at` 便於追蹤）。

### 4.4 審核介面放哪？

| 選項 | 說明 |
| --- | --- |
| **內部 API + 簡易 Admin（第一版）** | `GET/PATCH /api/admin/knowledge-reports`；可用簡易網頁或先用 curl／腳本 |
| n8n 審核看板 | 接 webhook，人在 n8n 點通過 | 適合過渡 |
| LIFF 審核角色 | 同一前端加 staff 模式 | 產品完整但較重 |

**建議：** 資料與 API 先落地；審核 UI 可用「最小 Admin」或 n8n；使用者側繼續用既有 KnowledgeReports 追蹤頁。

### 4.5 入庫（核准後）

```text
approve(report, selected_urls):
  for url in selected_urls:
    page = fetch_clean(url)          # Firecrawl / 白名單抓取
    chunks = split(page.text)
    embeddings = embed(chunks)       # 必須與現有向量庫同一模型／維度
    insert_many(health_articles_chunks, {
      chunk_content, embedding, source_name, url,
      report_id, ingested_at, ...
    })
  mark report resolved
```

注意：

- embedding 設定必須與建庫時一致（見 `.env` / `MONGODB_VECTOR_*`）
- 重複 URL 策略：更新舊 chunk 或跳過（需定規則）
- 醫療內容：審核員負責正確性；系統只做管線，不做自動「權威判定」

### 4.6 LINE 如何觸發回報（建議）

第一版（對齊前端 CTA）：

- 使用者在對話說「這個資訊過時」「我要回報知識」等 → Agent 呼叫 tool `submit_knowledge_report`  
- 或固定指令：`回報：…`  

第二版：LIFF 內加「提交回報」表單（填 URL、原因），直打 API。

---

## 5. 現況對照（As-Is）

### 5.1 Backend（CARE）

- 讀側：`retriever.py` → `answer_service.py` → tool `get_rag_answer`
- 集合：`health_articles_chunks`（只讀，無 ingestion 程式）
- 無 web search／無 knowledge report API
- n8n 多媒體流程 ≠ 知識入庫

### 5.2 Frontend（CARE-LIFF）

已有可複用殼層：

- 路由 `/knowledge-reports`
- 狀態文案與篩選 UI（pending / reviewing / resolved）
- reason：outdated / missing
- **缺：** API client、真實資料、排序、提交表單、staff 審核

---

## 6. 目標架構（To-Be）

```text
┌──────────── LINE / LIFF ────────────┐
│ 對話：問答 + 提交回報 tool           │
│ LIFF：追蹤我的 knowledge_reports    │
└───────────────┬─────────────────────┘
                │
                ▼
┌──────────── CARE API ───────────────┐
│ RagAnswerService                     │
│   retrieve KB → (optional) WebFallback│
│   cite ≤3，編號連續，不知則不附來源   │
│ KnowledgeReportService               │
│   create / list_mine / admin review  │
│ IngestService                        │
│   fetch → chunk → embed → Mongo      │
└───────┬───────────────┬─────────────┘
        │               │
        ▼               ▼
 health_articles_chunks   knowledge_reports
 （向量知識庫）            （審核佇列）
```

---

## 7. API 草案（討論用）

### 7.1 使用者

| Method | Path | 說明 |
| --- | --- | --- |
| `GET` | `/api/knowledge-reports/me` | 我的回報列表（給 LIFF） |
| `GET` | `/api/knowledge-reports/me/{id}` | 單筆詳情 |
| `POST` | `/api/knowledge-reports` | （可選）LIFF 表單提交；LINE tool 也可走 service 層 |

### 7.2 審核（需保護）

| Method | Path | 說明 |
| --- | --- | --- |
| `GET` | `/api/admin/knowledge-reports?status=` | 佇列 |
| `PATCH` | `/api/admin/knowledge-reports/{id}` | 改狀態、寫 note、選 URL |
| `POST` | `/api/admin/knowledge-reports/{id}/approve-ingest` | 核准並觸發入庫 |

權限：第一版可用 admin token／內部 IP／獨立 secret；之後再接角色。

---

## 8. 實作分期（建議）

### Phase 0 — 修可信度（小、可先上）

- [ ] 來源編號改為輸出序 `[1]…`
- [ ] 「無法回答」類回覆不附 KB 來源
- [ ] 補單元測試

### Phase 1 — Web Fallback

- [ ] 定義「無命中／弱命中」規則與 feature flag
- [ ] 接搜尋 + 抓頁（白名單）
- [ ] 生成答案 + 最多 3 網路來源
- [ ] 延遲／失敗 fallback 文案
- [ ] OpenSpec：`rag-responses` MODIFIED（無命中改為可 web）

### Phase 2 — 知識回報資料面

- [ ] Mongo `knowledge_reports` schema
- [ ] `POST`（LINE tool）+ `GET /me` 列表
- [ ] LIFF KnowledgeReports 接真 API，拿掉 mock
- [ ] 狀態：pending / reviewing / resolved / rejected

### Phase 3 — 審核與入庫

- [ ] Admin API 或 n8n 審核流
- [ ] IngestService：fetch → chunk → embed → upsert
- [ ] 核准後更新 report +（可選）通知使用者
- [ ] 重複 URL／失敗重試策略

### Phase 4 — 體驗加強（可選）

- [ ] LIFF 提交表單（含自填來源 URL）
- [ ] 系統自動蒐集 candidate_sources 供審核勾選
- [ ] Staff 簡易審核頁

---

## 9. 開放問題（需你／團隊拍板）

1. **Web 供應商**：Firecrawl？Tavily？還是只白名單爬衛福部系？  
2. **弱命中要不要自動上網**：還是只有「完全沒 docs」才上網？  
3. **回報儲存**：是否同意第一版用 Mongo `knowledge_reports`？  
4. **審核誰來做、用什麼 UI**：內部 API、n8n、還是要正式 Admin？  
5. **使用者提供的來源**：是否限制網域？個人部落格能不能入庫？  
6. **LINE 觸發語句**：要不要固定指令，還是全靠 LLM 意圖？  
7. **入庫後是否通知**：只更新 LIFF，還是推 LINE message？

---

## 10. 建議下一步（依你們的 OpenSpec × Superpowers 流程）

本地速查見：`doc/openspec-superpowers-cheatsheet.md`。

1. 用本文件當 explore 輸入，在 Claude Code：  
   `/opsx:explore RAG web fallback 與知識回報審核入庫，對照 CARE 與 CARE-LIFF 現況`  
2. 拍板第 9 節開放問題後：  
   `/opsx:propose rag-web-fallback-and-knowledge-reports`  
3. 能測的部分（cite 編號、不知不附來源、report 狀態機、ingest）走 Superpowers TDD；接外部搜尋的部分用 adapter + mock 測。

---

## 11. 成功標準（Acceptance）

- [ ] KB 有好內容：行為與現在類似，來源 ≤3 且編號連續  
- [ ] KB 空或明確不足：會嘗試 web；成功則答案附 ≤3 網路來源  
- [ ] KB／web 都失敗：清楚說明無法回答，**不附假來源**  
- [ ] 使用者可從 LINE 建立回報；LIFF 看得到真實狀態  
- [ ] 審核通過後，選定來源會進 `health_articles_chunks`，之後同題應較易命中 KB  
- [ ] 「烙賽」這類口語：至少不會再出現「說不知道卻貼無關 `[3]` 來源」

---

## 12. 參考檔案

| 區域 | 路徑 |
| --- | --- |
| RAG 回答 | `CARE/app/services/rag/answer_service.py` |
| 檢索 | `CARE/app/services/rag/retriever.py` |
| Spec | `CARE/openspec/specs/rag-responses/spec.md` |
| LIFF 回報頁 | `CARE-LIFF/src/pages/KnowledgeReports/index.tsx` |
| LIFF 文案 | `CARE-LIFF/src/i18n/messages.ts`（`knowledgeReports.*`） |
