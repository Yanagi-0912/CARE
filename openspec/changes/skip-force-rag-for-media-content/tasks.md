## 1. Skip force RAG for media content

- [x] 1.1 `nodes.py`：force RAG 條件加上 `not _is_media_extracted_content(user_text)`
- [x] 1.2 `prompt.py`：補充媒體抽出內容應依文件回答、勿 force／誤稱媒體類型
- [x] 1.3 測試：飲食指南 PDF 媒體 → 不 force RAG／location；一般衛教文字仍 force RAG
- [x] 1.4 勾選 tasks；`pytest tests/unit/services/agent/test_force_rag.py`（及相關 prompt 測試）全綠
