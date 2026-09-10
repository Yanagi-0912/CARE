## MODIFIED Requirements

### Requirement: 頻次代碼映射至時段

辨識出的頻次代碼 SHALL 依既有對照表映射為時段；提交時每個時段 SHALL 經 `find_or_create_reminder` 取得或建立該用藥者的唯一規則，新建規則 SHALL 只含一個 `none` 條目，時刻為該時段的預設時間。藥品 SHALL 連結至該規則的 `none` 條目（缺席時建立），SHALL NOT 改動規則既有的飯前、飯後條目，亦 SHALL NOT 嘗試從藥袋判定飯前飯後。

其餘條文維持不變。

#### Scenario: 提交到已有飯前飯後的時段

- **WHEN** 長輩的「早」規則已設定飯前與飯後條目，家屬提交一張辨識為 BID 的藥袋
- **THEN** 該藥 SHALL 掛在「早」與「晚」規則的 `none` 條目，既有的飯前、飯後條目與時刻 SHALL NOT 改變
