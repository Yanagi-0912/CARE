# CARE v3 離線 Batch 實驗

使用者已要求補上正式設定、Batch 執行器與固定快照流程，最後整理實驗前狀態；本次不提交正式題目。
基準為本機 v3（SHA256 49180741cbe53248876acdf9b45584022e1959ffda1fedd271b2b738b260c34a）及 864b6a00 的斷句閘門。
人工審核完成依使用者陳述記錄，不改寫原始題庫。B/D 各三輪共 600 筆；A/C 須有完整 old 快照才可執行。

只新增離線 CLI 與測試，API/LINE 服務不受影響。復用既有 RagAnswerService 判斷、prompt、Cohere 與 Jev，
將 Gemini 模型呼叫接到非同步 Batch。Batch 等待不受互動式 45 秒 timeout 限制；成本及取消行為獨立記錄。

驗證：傳輸／續跑狀態機、跨輪隔離、完整 RAG fake-provider 流程、快照拒絕 live/內容漂移及 ./init.sh。
