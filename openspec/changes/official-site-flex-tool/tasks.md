## 1. Flex 與 Tool

- [x] 1.1 新增官網入口 Flex 純函式（雙按鈕；缺 URL 時降級）
- [x] 1.2 新增 `open_official_site` tool（讀 settings／可 DI 注入 URL），回傳 JSON 字串
- [x] 1.3 註冊至 `registry.py`；必要時在 `dependencies.py` configure
- [x] 1.4 單元測試：Flex 結構、雙／單 URL、皆空、tool 註冊

## 2. Agent 路由與 Prompt

- [x] 2.1 `prompt.py`：官網／LIFF 入口優先 `open_official_site`；Flex 原樣輸出
- [x] 2.2 `nodes.py`：官網意圖偵測、force tool、跳過 force RAG；略過媒體前綴
- [x] 2.3 單元測試：打開官網 → force `open_official_site`、不 force RAG；媒體不誤觸

## 3. 收尾

- [x] 3.1 跑相關 pytest 全綠
- [x] 3.2 勾選 tasks
