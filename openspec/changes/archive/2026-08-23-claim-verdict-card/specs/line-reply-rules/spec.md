## RENAMED Requirements

- FROM: `### Requirement: 官網入口 Flex 原樣輸出`
- TO: `### Requirement: 工具 Flex 原樣輸出`

## MODIFIED Requirements

### Requirement: 工具 Flex 原樣輸出

當本輪呼叫 `open_official_site` 或 `verify_claim` 且工具回傳 LINE Flex Message JSON 時，代理最終回覆 SHALL 原樣輸出該 JSON，嚴禁修改、重寫、摘要或另加問候語／Markdown。

#### Scenario: 官網入口 tool 回傳 Flex 時原樣輸出

- **WHEN** `open_official_site` 回傳合法 Flex JSON 字串
- **THEN** 最終送往 LINE 的內容為該 Flex（經既有 reply 解析路徑），代理不得改寫為純文字網址列表

#### Scenario: 查核判定卡 tool 回傳 Flex 時原樣輸出

- **WHEN** `verify_claim` 回傳合法 Flex JSON 字串（查核判定卡）
- **THEN** 最終送往 LINE 的內容為該 Flex（經既有 reply 解析路徑），代理不得改寫為純文字判定摘要或加上額外評論
