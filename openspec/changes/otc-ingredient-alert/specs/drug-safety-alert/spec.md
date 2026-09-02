## ADDED Requirements

### Requirement: 非處方藥的成分重複偵測

當事人將**非處方藥**（藥品類別歸為指示藥或成藥）加入用藥提醒時，系統 SHALL 比對該藥與當事人現有有效用藥的主成分，判定是否有列於監測白名單的成分重複。

比對 SHALL 以主成分的英文學名進行，SHALL NOT 以中文品名判斷——同一成分在不同商品名下完全看不出關聯（普拿疼、斯斯、明通治痛丹的主成分同為 ACETAMINOPHEN）。

比對 SHALL 僅涵蓋白名單成分。全成分比對 SHALL NOT 使用：維生素等成分在綜合感冒藥中重複是常態且無臨床意義，報出來會讓警報淪為背景雜訊。

處方藥 SHALL NOT 觸發本偵測——它已經過醫師診斷與藥師調劑。

#### Scenario: 兩種非處方藥含相同白名單成分

- **WHEN** 當事人已在服用主成分含 ACETAMINOPHEN 的藥品，並將另一個同樣含 ACETAMINOPHEN 的非處方藥加入提醒
- **THEN** 系統判定為成分重複，並通知當事人本人與其 GUARDIAN／CAREGIVER

#### Scenario: 重複的成分不在白名單內

- **WHEN** 兩種藥共同含有的成分僅為維生素等未列入白名單者
- **THEN** SHALL NOT 判定為成分重複，SHALL NOT 發出重複警示

#### Scenario: 處方藥不觸發偵測

- **WHEN** 加入的藥品類別歸為處方藥
- **THEN** SHALL NOT 執行成分重複偵測，亦 SHALL NOT 因此發出任何通知

### Requirement: 成分重複的通知內容與收件人

通知 SHALL 同時送給當事人本人與 `otc_medication_added` 這個推播種類的收件人。當事人本人恆為收件人，不經通知政策表。

送給當事人的訊息 SHALL 說明哪兩種藥含有相同成分，並 SHALL 引導其詢問藥師。訊息 SHALL NOT 給出劑量建議或指示停藥——系統不取代藥事人員的專業判斷。

未偵測到成分重複時，非處方藥仍 SHALL 通知該推播種類的收件人，但 SHALL NOT 通知當事人本人（他剛完成加入動作，不需要再被打擾一次）。

#### Scenario: 重複時雙向通知

- **WHEN** 偵測到成分重複
- **THEN** 當事人收到含詢問藥師引導的提示，GUARDIAN／CAREGIVER 收到含重複成分說明的通知

#### Scenario: 無重複時只通知家人

- **WHEN** 加入非處方藥但未偵測到白名單成分重複
- **THEN** GUARDIAN／CAREGIVER 收到「新增了什麼藥、用途為何」的通知，當事人本人不另收訊息

### Requirement: 偵測失敗對主流程 fail-open

藥證庫查無、成分欄位為空、比對過程拋出例外——任一情況下系統 SHALL 記錄 log 後靜默結束，SHALL NOT 通知任何人，且 SHALL NOT 影響掃描與加入提醒的主流程。

log SHALL NOT 包含藥名、成分、當事人姓名或機構名稱。用藥組合本身即為病史的強烈線索。

#### Scenario: 藥證庫沒有該藥的成分資料

- **WHEN** 加入的藥品在藥證庫中查無成分欄位
- **THEN** 靜默跳過偵測，加入提醒的流程照常完成

#### Scenario: 舊版藥證庫沒有新欄位

- **WHEN** 執行期載入的藥證庫是尚未含成分欄位的舊版
- **THEN** 系統視為無成分資料而跳過偵測，SHALL NOT 拋出例外
