## MODIFIED Requirements

### Requirement: 推播收件人由通知政策決定

系統 SHALL 以獨立於資料存取授權的通知政策表決定每一種推播的收件人角色。收到通知 SHALL NOT 改變收件人的任何資料存取權。

推播種類 SHALL 包含 `high_risk_drug_alert` 與 `otc_medication_added`。兩者的收件人角色同為 `GUARDIAN` 與 `CAREGIVER`。

`MEMBER` SHALL NOT 為任何推播種類的收件人。理由不僅是權限：MEMBER 依授權矩陣看不到 SENSITIVE 資料，其可收到的訊息不含用途與風險說明，缺乏行動價值；持續發送低價值訊息會導致收件人靜音整個帳號，連帶淹沒真正需要注意的警報。

政策表中未列出的推播種類 SHALL 拋出例外，SHALL NOT 回傳空集合或落回資料存取授權。`NotificationKind` 是 Literal 型別，型別檢查已擋得住拼錯，因此執行期查不到只可能是「新增了推播種類卻忘了加進政策表」——那該在開發期就暴露，而不是上線後安靜地不通知任何人。

#### Scenario: 非處方藥通知的收件人

- **WHEN** 系統就 `otc_medication_added` 判定收件人
- **THEN** 回傳該當事人族譜中角色為 GUARDIAN 或 CAREGIVER 的成員

#### Scenario: MEMBER 不在收件人內

- **WHEN** 某族譜成員的角色為 MEMBER
- **THEN** 該成員 SHALL NOT 出現在任何推播種類的收件人清單中

#### Scenario: 未知的推播種類

- **WHEN** 查詢一個不在政策表內的推播種類
- **THEN** 拋出例外，SHALL NOT 回傳空集合或落回資料存取授權
