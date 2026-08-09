## ADDED Requirements

### Requirement: System prompt 依使用者語言組裝

系統 SHALL 以 `build_system_prompt(language)`（或同等）依 normalize 後的使用者語言組裝 system prompt。Prompt SHALL 指示模型以該語言回覆，且 SHALL NOT 再硬性要求「只能使用繁體中文」。RAG 前綴與參考來源標題的指示 SHALL 使用該語言對應字串。

#### Scenario: 英文使用者的 prompt 要求英文回覆

- **WHEN** `user_profile.settings.language` 為 `en` 且進入 `agent` 節點
- **THEN** 傳給模型的 system prompt 要求以英文回覆

#### Scenario: 缺省語言

- **WHEN** profile 無 language 或為未知代碼
- **THEN** system prompt 以 `zh-TW`（繁體中文）規則組裝
