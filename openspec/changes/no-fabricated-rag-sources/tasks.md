## 1. 禁止亂編來源

- [x] 1.1 新增 `strip_sources_section`（或同等）＋單元測試
- [x] 1.2 `prompt.py` 規則 8：無來源時嚴禁自造；有來源才保留
- [x] 1.3 `agent.py` 後置：tool 無來源 → strip；tool 有來源 → 既有後補
- [x] 1.4 Agent／prompt 測試；勾選 tasks；pytest 綠
