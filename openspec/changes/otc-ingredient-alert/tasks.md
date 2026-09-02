## 1. 先建白名單，它是整個功能的判準

- [x] 1.1 `resources/otc_watch_ingredients.json`：監測成分白名單，每一項附 `reason`（為什麼會累加致害）與 `source`（依據出處）
- [x] 1.2 初版以「實測出現頻率高 × 已知累加致害」選出，至少涵蓋 ACETAMINOPHEN、CHLORPHENIRAMINE MALEATE、DL-METHYLEPHEDRINE HCL
- [x] 1.3 **明確排除**維生素類（RIBOFLAVIN／ASCORBIC ACID／NIACINAMIDE 等），並在檔案註解寫下排除理由——它們在綜合感冒藥中重複是常態且無臨床意義
- [x] 1.4 白名單檔要能被單獨審視：不寫進程式碼，讓非工程角色也讀得懂

## 2. 建表腳本多取兩欄

- [x] 2.1 `scripts/build_drug_catalog.py` 新增 `_DRUG_CLASS_BY_CATEGORY`，逐一列舉 24 種「藥品類別」寫法（**不用關鍵字比對**，理由見 spec）
- [x] 2.2 `classify_drug()`：認不得的值回空字串，不猜
- [x] 2.3 主成分以 `;;` 切分並正規化（去括號補述、統一大寫、收斂空白），輸出為 `ingredients` 陣列
- [x] 2.4 條目新增 `drug_class` 與 `ingredients` 兩欄
- [x] 2.5 `tests/unit/scripts/test_build_drug_catalog.py`：釘住「須經醫師指示使用 → otc_guided」這一格（關鍵字比對會歸錯的地方，實測 5,842 筆）
- [x] 2.6 既有斷言整個 entry dict 的測試要補上新欄位——那些失敗是輸出形狀改變的忠實反映，不是要繞過

## 3. 藥證庫服務暴露新欄位

- [x] 3.1 `DrugCatalogService` 提供 `drug_class` 與 `ingredients` 查詢
- [x] 3.2 舊版 catalog 缺欄位時視為「無成分資料」，**不得拋錯**（會讓整個掃描流程掛掉）
- [x] 3.3 對應測試

## 4. 成分重複偵測

- [x] 4.1 `app/services/safety/ingredient_overlap.py`：純函式，輸入兩組成分與白名單，輸出重複的成分
- [x] 4.2 判定門檻留在純函式裡，比照 `risk_rules` 的既有分工
- [x] 4.3 測試：命中、白名單外的重複不算、空成分、正規化差異（`ACETAMINOPHEN (PARACETAMOL)` 與 `ACETAMINOPHEN`）

## 5. 通知政策

- [x] 5.1 `NotificationKind` 新增 `otc_medication_added`
- [x] 5.2 `NOTIFICATION_POLICY` 加入該 kind，收件人沿用 `{GUARDIAN, CAREGIVER}`——**政策表的既有格子不動**
- [x] 5.3 ~~未知 kind 回空集合~~ **維持既有的拋錯行為**：既有測試明文要求「查不到 SHALL 直接爆，SHALL NOT 悄悄落回讀取權」，且 NotificationKind 是 Literal，型別檢查已擋得住拼錯
- [x] 5.4 `tests/unit/models/test_family_authorization.py`：MEMBER 不在任何 kind 的收件人內

## 6. 接進掃描流程

- [ ] 6.1 非處方藥加入提醒後觸發偵測；處方藥完全略過
- [ ] 6.2 重複時：當事人本人 + 收件人都通知；無重複時：只通知收件人
- [ ] 6.3 對主流程 fail-open——任一步失敗記 log 後靜默結束
- [ ] 6.4 log 不得帶藥名、成分、姓名、機構（用藥組合即病史線索）
- [ ] 6.5 測試涵蓋四種組合：處方藥／非處方藥無重複／非處方藥有重複／偵測拋例外

## 7. 訊息與卡片

- [ ] 7.1 當事人的提示 SHALL 引導詢問藥師，SHALL NOT 給劑量建議或指示停藥
- [ ] 7.2 家人的通知含藥名、用途、重複成分；語言與字級取**收件人本人**的設定（比照 `build_family_alert_flex`）
- [ ] 7.3 用語以「讓家人幫你看一下」為框架，不是「已回報」——避免長輩覺得被監控而抗拒掃描
- [ ] 7.4 純文字不得含 Markdown（`openspec/specs/line-reply-rules`）
- [ ] 7.5 卡片大小走既有的 `size_guard`，超過上限退回純文字

## 8. 收尾

- [x] 8.1 重跑建表腳本（**不帶** `--fetch-images`／`--fetch-indications`）並確認 `drug_catalog.json` 兩個新欄位齊全
- [ ] 8.2 `./init.sh` 全綠
- [x] 8.3 以真實資料抽驗：找幾組已知含相同成分的非處方藥，確認偵測得到
- [ ] 8.4 清楚的 git commit 與 PR
