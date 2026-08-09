## Context

- 官方 KB：`IngestService.ingest_url` → whitelist scrape → `split_text_to_chunks` → embed → `MONGODB_COLLECTION`
- 上傳：`LineMediaHandler` 抽字 → 前綴包裝 → Agent；媒體前綴已 skip force 官方 RAG
- User scope：`line_user_id` ContextVar（`knowledge_report_tools`，於 `message_handler` 設定）
- 本階段**不做 Redis**；過期只靠 Mongo TTL

## Goals / Non-Goals

**Goals:**

- PDF／file 抽字後寫入 user-scoped 暫存向量庫
- 後續問題可對該使用者未過期 chunk 做向量檢索並回答
- TTL 自動清除（預設 1 天，與聊天紀錄量級對齊）

**Non-Goals:**

- Redis 指標／快取
- 上傳文件寫入官方 KB collection
- 圖片 OCR 管線修復
- 官方 KB 與 user docs 的 RRF 融合（可後續加）

## Decisions

1. **獨立 collection**  
   - Env：`MONGODB_USER_DOCS_COLLECTION`（及可選獨立 `MONGODB_USER_DOCS_VECTOR_INDEX`，若與官方共用 index 名稱策略則在實作註明）  
   - 避免污染官方 `MONGODB_COLLECTION`，TTL／權限隔離較單純

2. **文件 shape**（對齊官方欄位習慣 + user 欄位）  
   ```text
   text_field, vector_field,
   line_user_id, document_id, source_name, media_type,
   chunk_index, content_hash, ingested_at, expires_at
   ```  
   - `document_id`：單次上傳 UUID  
   - `expires_at`：`datetime`（UTC），TTL index `expireAfterSeconds: 0`

3. **Ingest 時機**  
   - `media_handler` 抽字成功且 `media_type == "file"`（或副檔名 pdf）後呼叫 `UserDocumentIngestService.ingest_text(...)`  
   - 失敗只 log，不阻斷既有「依抽出文字回覆」路徑

4. **問答入口**  
   - 新工具 `answer_from_uploaded_document(query: str)`  
   - 內讀 `get_line_user_id()`；無 user → 友善錯誤字串  
   - 服務：user filter + vector search → 取 top chunks → Gemini 依段落回答（可復用／精簡 `build_rag_prompt`）  
   - **不**呼叫官方 `get_rag_answer`／Firecrawl

5. **Agent**  
   - `get_all_tools` 在 `allow_rag` 或「使用者有未過期 docs」時納入新工具（MVP：與 `allow_rag` 一同提供，或 always 提供但服務內無資料則說明）  
   - Prompt：上傳文件相關問題優先 `answer_from_uploaded_document`；一般衛教仍 `get_rag_answer`

6. **TTL**  
   - `ensure_indexes` 於啟動時建立 TTL on `expires_at`  
   - 預設 TTL seconds：`USER_DOCS_TTL_SECONDS=86400`（可設定）

## Risks / Trade-offs

- [Risk] Atlas 需手動／IaC 建 user-docs vector index → Mitigation：README／tasks 註明；測試 mock retriever  
- [Risk] 同 user 多次上傳混在一起 → Mitigation：metadata 含 `document_id`／`source_name`；MVP 檢索該 user 全部未過期 chunk（可接受）  
- [Risk] 無 Redis 時「是否有上傳」只能查 Mongo → Mitigation：可接受  
- [Trade-off] 不做官方交叉驗證 → 後續可加

## Migration Plan

1. 加 env／config  
2. 部署前在 Atlas 建 user-docs vector index  
3. 滾動後端；回滾刪工具與 ingest hook 即可（殘留 chunk 靠 TTL 清）

## Open Questions

- Atlas index 名稱是否與官方分開：預設分開（`MONGODB_USER_DOCS_VECTOR_INDEX`）
