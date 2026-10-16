# 藥局與藥妝門市的資料覆蓋

首次記錄：2026-09-30；補充：2026-10-16。

## 原因與原始狀況

原本 `medical_facilities_pharmacy` 有 7,970 筆資料，`type` 僅「藥師自營」與
「藥劑生自營」，收錄範圍主要是健保特約藥局。一般藥妝、醫材門市不在這份名錄。
因此未簽健保特約的店、同一品牌旗下未登記為藥局的店，都可能查不到。
「有品牌門市」不能直接等同「是藥局」或「有健保特約」。

2026-09-30 的問題是「基隆康是美」查無結果；只搜尋「康是美」時，最近結果在汐止。
2026-10-16 匯入前再次確認：全台名稱包含康是美的原始藥局有 107 筆，基隆 0 筆；
屈臣氏只有 1 筆。這是資料缺口，不能靠放寬地區條件解決。

## 2026-10-16 補充的來源

產出物為 `resources/pharmacy_branches.json`，保存來源網址、擷取時間、原始內容
SHA-256、各品牌覆蓋狀態。此次整理 3,218 筆來源資料：

| 品牌 | 來源筆數 | 主要來源與範圍 |
|---|---:|---|
| 康是美 | 619 | [官方門市查詢](https://www.cosmed.com.tw/shop.aspx)，含基隆 6 家 |
| 大樹 | 446 | [官方門市目錄](https://www.greattree.com.tw/stores)，其中南投中興店預告 10/23 開幕，尚不匯入 |
| 佑全 | 135 | [官方門市目錄](https://www.yourchance.com.tw/index.php?lang=cht&temp=store) |
| 丁丁 | 108 | [官方服務據點](https://www.norbelbaby.com.tw/nec/about?page=stronghold) |
| 躍獅 | 111 | [官方門市目錄](https://www.yeschain.com.tw/stores.php) |
| 啄木鳥 | 48 | [官方門市目錄](https://www.woodpecker.com.tw/branch/all/1)，取全部分頁 |
| 維康 | 203 | [官方門市資料](https://www.wellcare.com.tw/wellcare/tw/store/api/shop) |
| 杏一 | 313 | [官方門市目錄](https://www.medfirst.com.tw/v2/Shop/StoreList/41218)，依各縣市取完整資料 |
| 日藥本舖 | 81 | [官方門市目錄](https://www.jpmed.com.tw/v2/Shop/StoreList/41165) |
| 寶雅 | 499 | [官方門市目錄](https://www.poya.com.tw/store/)，取全部分頁 |
| Tomod's | 14 | [官方門市目錄](https://www.tomods.com.tw/pages/store)，支援三友藥妝、特美事別名 |
| 松本清 | 25 | [官方門市目錄](https://www.matsumotokiyoshi-tw.com/shopinfo)及各店詳情 |
| 屈臣氏 | 616 個地址 | 經濟部有效分公司登記，另以[官方活動門市表](https://www.watsons.com.tw/happyday-store)補部分店名、電話 |

屈臣氏官網完整門市 API 無法存取，因此改取總公司統編 **23224657** 的有效分公司
登記（狀態 `01`）：621 筆登記依同址不同樓層合併為 616 個地址，廢止、撤銷登記
均不收錄。這些地址保留 `operationalStatusVerified: false`，搜尋結果註記實際
營業狀態與服務需確認。活動表只代表該活動的參與門市，不當作全台營業名錄。
164 筆活動來源保存在 `resources/pharmacy_source_supplements/`，其中 135 個登記
地址可補官方店名及電話。未取得電話的地址不填猜測值。

另以[食藥署藥局基本資料](https://data.gov.tw/dataset/6134)的名稱、地址、電話
交叉比對執照與健保特約狀態，只保存公開機構欄位，不保存負責人的姓名或性別。
這次沒有將全台非連鎖藥局名錄全部重匯；該部分仍由原本的政府資料管線維護。

## 匯入與搜尋行為

`scripts/sync_pharmacy_branches.py import` 預設只預覽，`--apply` 才寫入資料庫。
舊版搜尋程式仍在服務時，可以先用 `--insert-only --apply` 補入新資料，完全不修改
既有文件；支援別名的新版本部署後，再執行完整匯入以更新官方名稱與來源。
依來源 ID、電話、地址與店名比對既有資料，不只靠同址合併，以免把醫院內不同
品牌的店誤併。既有藥局保留政府類型、營業時間、註記，原店名存為別名。

2026-10-16 已用新增模式匯入 **2,422 筆**：藥局 237 筆、藥妝／醫材門市
2,185 筆。原有 7,970 筆保留，藥局集合現在共 8,207 筆。另有 795 筆既有門市
可補官方名稱與來源，待支援別名的新搜尋版本部署後，再執行完整更新。

第一次寫入時，資料庫唯一索引擋下原始政府名錄中同店不同登記名稱的衝突，當次
僅新增 4 筆。修正為優先比對完全相同的店名與地址，並加上寫入前唯一性檢查後，
接續匯入 2,418 筆；沒有移除防重複索引，也沒有修改既有文件。兩次備份、
寫入計畫與正式結果保存在 repo 外的 `outputs/pharmacy-branches-20261016-import/`
及 `outputs/pharmacy-branches-20261016-import-resume/`。

搜尋程式目前位於 `codex/pharmacy-branch-coverage`，**尚未部署**。直接推送
`main` 會觸發正式部署，因此需先取得部署授權；已入庫的一般藥妝／醫材門市，
要等新搜尋程式上線後才會出現在品牌搜尋結果中。

正式資料庫唯讀稽核已確認：原有 7,970 筆公開機構欄位全數未變；相同來源再跑
新增模式，新增計畫為 0 筆。以待上線程式查詢正式資料時，「基隆康是美」可取到
基隆港、廟口、信二、新仁一、悅城、百福共 6 家；「基隆屈臣氏」取到 12 個
登記地址。無座標店名查詢、地址導航、零售門市卡片 ID 查詢均已驗證。

- `medical_facilities_pharmacy`：原有藥局與補充的藥局，供一般附近藥局搜尋。
- `medical_facilities_retail`：一般藥妝、醫材門市，供品牌／店名搜尋及卡片詳細資料。
- 名稱搜尋同時查兩個集合；地理搜尋合併後按距離排序，舊店名也可查。
- 沒有座標時，指定店名仍可回傳地址並產生地址導航，不填 `0,0`、不編造距離。
- 新增資料中有 895 筆無可信座標，含屈臣氏登記地址；這些店不參與附近距離排序。
- 指定縣市時比對地址開頭的行政區，避免「基隆康是美」誤收臺北市基隆路的門市。
- 官網標示暫停營業的 4 家康是美保留資料並標示暫停；未開幕的大樹門市暫不匯入。
- 健保特約、處方箋服務未知時保留 `null`，不能因品牌或未顯示圖示而推斷。

## 更新與復原

```bash
uv run scripts/sync_pharmacy_branches.py collect --cache-dir /tmp/care-branches-YYYYMMDD
uv run scripts/sync_pharmacy_branches.py import --report-dir ../outputs/pharmacy-preview-YYYYMMDD
uv run scripts/sync_pharmacy_branches.py import --apply --report-dir ../outputs/pharmacy-import-YYYYMMDD
```

更新請使用新的快取目錄；同一目錄會重用原始下載。匯入連線取環境變數或 repo
根目錄 `.env` 的 `MONGODB_URI`、`MONGODB_DB`，沒有自動排程。
部署不會自行匯入這份資料。匯入後需重新載入後端院所名稱索引。

套用前會保存受影響文件的完整 BSON 前像（`before-import.bson.json`），以及
確定為新增的 ID（`inserted-ids.json`）。復原時，以 BSON 前像還原既有文件，
新增文件只針對該次 ID 清單移除；先確認匯入後沒有其他人修改同一文件。
同一備份目錄禁止重複套用，第二次驗證匯入要選新的報告目錄。

## 仍未完成的來源

**博登**：本次無法取得可驗證的官方加盟門市目錄；官方 HTTPS 無法連線，HTTP
也未提供可驗證的完整門市資料。產出物明確標為 `incomplete`，未用過時的二手
列表推定加盟關係，不能宣稱全台各連鎖已全部補齊。

**屈臣氏與缺座標門市**：需取得完整官方營業門市目錄及可信座標，才能將這部分
升級為已確認營業並支援附近距離排序。目前有效公司登記地址已可供名稱查詢。
