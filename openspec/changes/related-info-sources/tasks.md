# Tasks

- [x] `VerificationResult` 新增 `related_sources: tuple[SourceRef, ...] = ()`
- [x] `_fetch_related_info` 回傳 `(text, sources)`；去重鍵改為 url→「來源名＋標題」
- [x] 空內容檢查移到去重之前
- [x] `_related_info_block` 加出處清單（逐筆列出，含無網址者）
- [x] `_related_source_buttons`；`_footer` 改收 list
- [x] `build_verdict_flex` 未命中側接上 footer 按鈕
- [x] `claim_tools._format_verdict_reply` 同步列出出處
- [x] `_RELATED_INFO_TOP_K` 3 → 2（實測：取 3 筆在滿版情況下本來就超標）
- [x] 迴歸測試鎖住 TOP_K 與 `size_guard` 門檻的關係
- [x] 測試：結構化出處、無網址仍保留、無網址去重迴歸、空 chunk 不佔名額、
      命中側不受影響、卡片出處與按鈕、免責說明順序、純文字 fallback
- [ ] `./init.sh` 全綠後 commit／PR
