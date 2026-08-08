# 執行紀錄（subagent-driven development）

本 change 以 subagent-driven-development 執行：每個 task 一個實作 agent，
完成後派獨立審查者，findings 進修正迴圈。以下是當時的 ledger 原文，
保留下來是因為它記錄了幾件 git log 看不出來的事：Task 4 為何改了三輪、
哪些殘留是「量測後判定可接受」而非「忘了處理」、以及最終審查與規格稽核
各自抓到什麼。

---

# SDD ledger — plan: openspec/changes/facility-type-filter/tasks.md

Worktree: /Users/jamessu/Desktop/computersciencehomework/CARE/.worktrees/facility-type-filter
Branch: sdd/facility-type-filter (base 2e7d94e)
Baseline: pytest 856 unit passed before任何實作

Task 1: complete (commits 2e7d94e..6fe86d1, review clean) — facility_type_matcher.py，47 新測試，903 passed
Task 1: minor (deferred): FacilityTypeMatch.is_alias 是契約外的加值欄位，勿被後續任務當成正式契約
Task 1: minor (deferred): 17 個 type 正式值未逐一覆蓋直接解析測試
Task 2: complete (commits 6fe86d1..7694d45, review clean) — _combine_filters + facility_type 選填參數，9 新測試，912 passed
Task 2: minor (deferred): facility_type 解析區塊在兩個方法中重複約 9 行（回傳型別不同，抽取需額外設計）
Task 2: minor (deferred): find_nearby_facilities_by_department 成功路徑未顯式傳 facility_type_unresolved（預設 False 本就正確，僅可讀性）
控制者裁量：將 Task 5（i18n）提前至 Task 3 之前執行。Task 3 的 3.2/3.3 需要 location.type.* 文案，
  而 tasks.md 把 i18n 排在第 5 節，順序反了。純執行順序調整，不改任何範圍或需求。
Task 5: complete (commits 7694d45..dbeb22f, review clean) — location.type.* 三 key 六語言，40 新測試，952 passed
Task 5: minor (deferred): pharmacy_none 的「誠實揭露」語意斷言只覆蓋 zh-TW 與 en，未覆蓋 id/vi/th/ja
Task 3: complete (commits dbeb22f..1950124, review clean) — facility_type 參數 + 標題「附近的腸胃科（醫院）」+ 藥局專屬文案，10 新測試，962 passed
Task 3: minor (deferred): 一項測試建構了生產路徑不可達的組合（match=None 且 facility_type_unresolved=True）
Task 4: 實作完成 (commit 5dfb748)，review 判 Important：閘門對具名院所誤判
  - 「台大醫院在哪」→ 大醫院（「台大醫院」字串內含「大醫院」）
  - 「杏一診所在哪裡」→ 診所、「康是美藥局在哪」→ 藥局
  - 跨輪重現：先問具名診所、後問泛稱醫院 → 誤套 facility_type=診所
  控制者已獨立實跑確認為真。
Task 4: fix round 1/5 (1 addressed, 1 new — 具名院所誤判已解決；但連接詞白名單過窄，
  「評價不錯的診所」「24小時營業的藥局」等合法泛稱被誤擋; commits 5dfb748..a782575)
Task 4: fix round 2/5 (1 addressed, 0 open; commits a782575..df60d17) — 改用「緊鄰語法標記」判別取代連接詞白名單
Task 4: complete (commits 1950124..df60d17, review clean) — 27/27 邊界輸入通過，1027 passed
Task 4: minor (deferred): 「附近有什麼診所」等含疑問詞「什麼」的句型仍回 None（round 2 已淨改善，非新退化；加一個標記字即可）
Task 4: minor (deferred): 混合句「台大醫院附近的診所」整句回 None —— Task 1 extract_facility_type_intent 每句只回一個候選詞的既有特性
Task 4: minor (deferred): 品牌尾字恰為語法標記時仍可能誤判（如虛構「快看診所」），實務罕見
Task 6: complete (commit b7ef8cc, 驗證全通過) — scripts/verify_facility_type_filter.py，真實 DB 6.2/6.3/6.4 全數 assert 通過
Task 6: 控制者觀察（deferred，非實作缺陷）：台北車站查藥局，最近一筆在 18.4 公里外。
  台北車站周邊實際有數十家藥局，這證實藥局資料缺口（116 家 vs 全台數千家）不只影響「查無」情境。
  目前 location.type.pharmacy_none 只在 0 筆時觸發；「查到但荒謬地遠」這個更常見的情境
  會回傳看似正常的 Flex 卡片，使用者無從得知那是資料缺漏造成的。
  建議：查到的藥局距離超過某個門檻（例如 5 公里）時，也附上資料有限的說明。
Task 7: complete (commit 851621a) — tasks.md 勾選、openspec validate 通過、補記執行偏離。7.2 歸檔留待 merge 後。

=== 最終全 branch 審查（opus）===
判定：需修正後合併。5 項 Important，控制者已逐項實跑復現：
  I1 正式 type 值句型靜默失效：「附近有綜合醫院嗎」「附近有專科診所嗎」「我要找精神科醫院」→ None
  I2 規格 scenario「附近的牙醫診所」未實作且零測試（I1 的特例）
  I3 具名院所誤判殘留：真實 DB 掃出 35 家會誤觸（皇家/全家/我家/和睦家/美的診所…）
     → 推翻 ledger 先前「實務罕見」的判斷
  I4 facility_type="" 讓核心流程吐出「我不確定「」對應到哪一種院所類型」
  I5 藥局「查到但荒謬地遠」：台北車站最近藥局 18.4km 但 satisfied=True，
     走 expanded 文案「已擴大到 19 公里」，資料缺口完全未揭露
審查另指出：整個 branch 沒有任何整合層測試，I1/I2/I4 全住在各層 mock 遮住的接縫上。

=== 最終修正波（單一 fix agent，opus）===
commits 851621a..8303d5e（4 個），1098 passed（基準 1030，+68），驗證腳本仍全通過
scoped re-review（opus）判定：5 項全部 ADDRESSED，無新破壞，可以合併
  I3 獨立復現：真實 DB 全量掃描，誤觸 35 家 → 2 家（一家牙醫診所、遠東聯想牙醫診所）
  整合測試經「還原修正」反證有效：還原 I1 → 2 案例失敗；還原 I4 → 1 案例失敗
parked（可接受殘留，失效方向皆為「漏判退回現況」而非「誤判套錯過濾」）：
  P1 類型詞前綴剛好 2 字時失效，範圍比報告揭露的大（控制者已實跑確認）：
     「好的診所」「新的診所」「舊的診所」「近的藥局」「小的診所」
     「多家診所」「多間診所」「另家診所」皆回 None
     對照：「附近有新的診所嗎」「我要找好的診所」「好幾家診所」「五間診所」皆正常
     → 下一輪若要根治，應改走「查院所名稱資料庫確認是否為專名」，不要再加第 4 輪字元規則
  P2 「附近有中醫診所嗎」排除綜合醫院中醫部：實測僅排除 145/3522（4.1%），
     且裸詞「附近有中醫嗎」仍回全部 3,522 家 —— design.md 決策 2 的顧慮未被違反，判定合理
  P3 department="" 降級為一般搜尋，僅靠 log 可觀測
  P4 真實 DB 殘留 2 家誤判（0.010%）

=== 後續：根治 I3（commit 3413634）===
依最終審查與修正者的建議，把專名判定從字元規則改為查院所名稱資料庫。
新增 app/services/medical/facility_name_index.py（啟動時由 dependencies.py 預載 19,528 筆）。
真實 DB 全量掃描 19,105 筆：誤判 35 → 2 → 0，每次判定 0.006 ms。
連帶關閉的 parked 項目：
  P1 前綴剛好 2 字失效 → 已修（好的診所/多家診所/新的診所…全部觸發）
  P4 殘留 2 家誤判 → 已修
  「有什麼診所」「附近有哪些藥局」等疑問詞句型 → 一併修好
仍 parked：
  P2 「附近有中醫診所嗎」排除綜合醫院中醫部 → 【已用量測關閉，非 parked】
     300 個真實座標抽樣：中醫 300/300 結果相同、牙醫 300/300 相同，差異為零。
     被排除的 143 家中醫醫院與 84 家牙醫醫院進不了最近 5 名。
     決定不改行為（改了會犧牲「明說診所」的正確性，換不到差異），
     已補回歸測試鎖住雙維度拆解 + design.md 記錄量測結果。
  P3 department="" 降級為一般搜尋，僅靠 log 可觀測
實作過程自己抓到的兩個 bug（皆已加測試守住）：
  - 索引存原始名稱但查詢端已正規化 → 全量掃描出 16 筆誤判 → 改為寫入端正規化
  - 只檢查「前綴以名稱結尾」漏掉類型詞在名稱中段的情形 → 改為「包住」判定
pytest 1123 passed

=== 規格符合度稽核（opus，取代 Cursor 的 /opsx-verify）===
6 個 Requirement / 14 個 Scenario 的功能行為全部實作正確且有測試覆蓋。
發現 5 項落差，控制者逐項獨立驗證：

G1 archive 會 abort —— 【確認為真，已修】
   agent-architecture 的 MODIFIED 標的不在主 spec，因為它由前置 change
   department-aware-nearby-search 以 ADDED 引入，而該 change 也 archive 不了
   （location-search 內容早已被手動併入 → already exists）。
   根因是先前直接編輯 openspec/specs/ 那次流程違規：手動同步只做了 location-search，
   漏掉 agent-architecture，且 location-search 也漏了「科別搜尋意圖觸發位置請求」。
   修法：補齊主 spec 三塊（1 條 location-search ADDED、1 條 agent-architecture ADDED、
   2 條 agent-architecture MODIFIED），沙盒驗證歸檔順序可行並寫進 tasks.md。

G2/G3 測試覆蓋洞 —— 【不成立，稽核者方法有誤】
   稽核者宣稱突變「診所」「藥局」分類的 type 值集合後 1125 全綠。
   控制者用直接改原始碼的方式重做他指名的三種突變：
     診所類「西醫診所(醫務室)」→「綜合醫院」：2 failed
     藥局類 →（綜合醫院, 醫院）：4 failed
     藥局類刪掉「藥劑生自營」：2 failed
   全部被抓到。稽核者用 runtime plugin 注入，很可能在 test module 匯入常數之後
   才替換，製造假象。判定：無此落差，不需處理。

G4/G5 文件層級（空字串例外未寫進 spec、工具 docstring 暗示了不存在的精度）—— parked
