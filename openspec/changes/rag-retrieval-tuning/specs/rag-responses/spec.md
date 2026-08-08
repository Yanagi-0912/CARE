## MODIFIED Requirements

### Requirement: 向量檢索候選過濾

向量檢索 SHALL NOT 以固定的相似度門檻過濾候選文件；預設 `RAG_VECTOR_MIN_SCORE` 為 `0.0`，第一階段的職責是最大化召回，過濾與排序 SHALL 由精排階段負責。系統 SHALL 保留該設定項，使需要時可由環境變數調回非零門檻。

送入精排的文件文本 SHALL 與建立 embedding 時的格式一致：當文件具備 `original_title` 時，SHALL 組為「主題：{original_title}\n內容：{chunk}」；缺標題時 SHALL 退回純內容。精排回傳的文件 `page_content` SHALL 維持原始 chunk 內容不變。

#### Scenario: 低分候選仍進入精排

- **WHEN** 向量檢索取回的文件中包含相似度低於 0.5 的候選
- **THEN** 這些候選仍送入精排階段，由精排決定去留

#### Scenario: 精排輸入帶標題

- **WHEN** 候選文件具備 `original_title`
- **THEN** 送往精排 API 的文本為「主題：{標題}\n內容：{內容}」，而回傳文件的 `page_content` 仍為原始 chunk 內容
