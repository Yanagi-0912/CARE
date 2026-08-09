## Context

`LineMediaHandler` 將 OCR／抽字結果包成：

```text
以下為使用者傳送的{image|video|audio|file}媒體內容：
{extracted}
```

前一修已用 `_is_media_extracted_content` 排除附近院所 force。但仍會在 `allow_rag=True` 時把**整段媒體全文** force 進 `get_rag_answer`，導致無效 KB 查詢與矛盾回覆。

## Goals / Non-Goals

**Goals:**

- 媒體抽出全文：不 force RAG、不 force location
- 模型依抽出內容摘要／回答
- 一般衛教文字 force RAG 行為不變

**Non-Goals:**

- 使用者上傳文件 ingest 進 Mongo KB
- 圖片 OCR 服務／n8n workflow 修復
- 改 CRAG／retriever 分數邏輯

## Decisions

1. **復用** `_is_media_extracted_content(user_text)`（已存在）  
   - force RAG 條件再加：`and not _is_media_extracted_content(user_text)`  
   - 不另建 message_type 狀態欄位（最小改動；前綴已是契約）

2. **prompt** 增補一條媒體規則：  
   - 見媒體前綴 → 依抽出內容回答／摘要  
   - 正確使用媒體類型用語（file／image／…）  
   - 禁止為此強制呼叫 `get_rag_answer`，也禁止因 KB 無命中而說「無法從圖片找到資訊」

3. **測試**  
   - 飲食指南 PDF 媒體全文 → 無 tool_calls、`force_rag`／`force_location` 皆無  
   - 「我有六隻腳趾頭」仍 force RAG（回歸）

## Risks / Trade-offs

- [Risk] 使用者上傳「附近醫院地圖」照片也無法 force location → Mitigation：前綴排除本就如此；可另用文字「幫我找醫院」  
- [Risk] 模型仍自行呼叫 `get_rag_answer` → Mitigation：prompt 禁止；force 層至少不再強制  
- [Trade-off] 媒體內容不查官方 KB → 文件理解優先於官方交叉驗證（符合上傳意圖）

## Migration Plan

- 部署後端即可；無資料遷移  
- 回滾：還原 `nodes.py` force 條件與 prompt 條文
