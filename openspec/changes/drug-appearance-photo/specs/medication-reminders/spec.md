## ADDED Requirements

### Requirement: 推播的藥品清單得帶出藥丸縮圖

給用藥者的服藥提醒與二次催促，其藥品清單的每一列 SHALL 在該藥品的 `license_number` 已確定且有可用照片時，於藥名旁呈現藥丸縮圖。

沒有照片的藥品 SHALL 維持純文字列，且同一清單中圖文混排 SHALL NOT 使版面破損。`medication_ids` 為空時的版面 SHALL 與本變更前完全相同。

藥品數量仍 SHALL 受既有顯示上限收斂為單行計數。

家屬的逾時警報與錯過時段彙整通知 SHALL NOT 呈現藥丸縮圖——其收件人的問題是「有沒有吃」，不是「該吃哪一顆」，加入藥品外觀只會擴大病情資訊在通知列的暴露面。

#### Scenario: 有照片的藥品

- **WHEN** 某時段的藥品證號已確定且有可用照片
- **THEN** 該列 SHALL 呈現縮圖與藥名

#### Scenario: 同時段圖文混排

- **WHEN** 某時段同時有帶照片與無照片的藥品
- **THEN** 無照片者 SHALL 呈現為純文字列，版面 SHALL NOT 破損

#### Scenario: 家屬卡片不含縮圖

- **WHEN** 逾時警報推播給家屬
- **THEN** 訊息 SHALL NOT 含任何藥丸縮圖
