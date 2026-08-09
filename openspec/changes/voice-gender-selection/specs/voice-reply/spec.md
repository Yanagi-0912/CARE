## ADDED Requirements

### Requirement: 語音音色可由使用者選擇

系統 SHALL 提供 `settings.voice_gender`，值域為 `female`、`male`，預設 `female`。合成時 SHALL 依該設定於當前語言對應的音色中選用。使用者變更後 SHALL 於下一則回覆即生效。

每一個受支援的語言 SHALL 同時提供 female 與 male 兩種音色對應；語言未知時 SHALL fallback 至 `zh-TW`，性別值未知或缺漏時 SHALL fallback 至 `female`。

備援引擎不支援音色選擇時，SHALL 仍完成合成並忽略該設定，SHALL NOT 因此中斷回覆。

#### Scenario: 選擇男聲後音色改變

- **WHEN** 使用者將 `voice_gender` 設為 `male` 且語言為 `zh-TW`，並發送下一則訊息
- **THEN** 合成使用 `zh-TW` 的男聲音色，而非預設女聲

#### Scenario: 每種支援語言都有對應的兩種音色

- **WHEN** 語言為 `zh-TW`、`en`、`id`、`vi`、`th`、`ja` 之任一，且性別為 `female` 或 `male`
- **THEN** 皆可解析出一個有效的音色，不會發生查表失敗

#### Scenario: 缺欄位的舊資料使用女聲

- **WHEN** 使用者 profile 的 `settings` 不含 `voice_gender`
- **THEN** 讀取設定時回傳 `female`，且合成所用音色與本需求導入前完全相同

#### Scenario: 非法值被拒絕

- **WHEN** 以 PATCH 更新 `voice_gender` 為 `robot`
- **THEN** API 回應 422，且資料庫既有值不變

#### Scenario: 備援引擎忽略音色設定但仍送出語音

- **WHEN** 使用者選擇 `male`，主要引擎失敗而備援引擎成功
- **THEN** 使用者仍收到語音訊息（音色為備援引擎的預設），且文字訊息不受影響
