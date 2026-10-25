# CARE: Clinical Assistance & Resource Engine

CARE 是一個在 LINE 上使用的健康醫療資訊 AI 助手。它有三個核心目標：

- **高齡友善**：設計讓長輩容易使用。
- **資料準確**：保障提供的資訊正確。
- **整合 AI 技術**。

CARE 是「適地性」服務：它會依照使用者所在的位置提供資訊，例如附近的醫療院所。

[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=Yanagi-0912_CARE&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=Yanagi-0912_CARE) [![SonarQube Cloud](https://sonarcloud.io/images/project_badges/sonarcloud-dark.svg)](https://sonarcloud.io/summary/new_code?id=Yanagi-0912_CARE)

這份文件說明三件事：怎麼在自己的電腦上執行 CARE、怎麼更新資料檔，以及主要功能怎麼運作。

## 名詞表

| 名詞 | 說明 |
| --- | --- |
| 後端 | CARE 的主程式，也就是這個 repo。它接收 LINE 訊息、呼叫 AI、回覆使用者。 |
| repo | 用 Git 管理的程式碼專案。除了本 repo（後端），CARE 還有：`CARE-LIFF`（LIFF 網頁）、`CARE-infra`（部署設定）、`CARE-n8n`（多媒體處理流程）。 |
| LIFF | 在 LINE 裡開啟的網頁（LINE Front-end Framework）。CARE 的設定、個人健康與家庭管理畫面都是 LIFF 網頁。 |
| Webhook 網址 | LINE 收到使用者訊息後，把訊息轉送過去的網址，也稱 callback 網址。LINE 管理頁面上的欄位名稱是「Webhook URL」。 |
| 代理（agent） | 由 AI 模型決定要呼叫哪些工具、再組成回覆的程式。 |
| 工具 | 代理可以呼叫的功能，例如搜尋附近院所、查知識庫。 |
| guardrail | 訊息進入代理之前的檢查。它判斷兩件事：可不可以查知識庫，以及狀況緊不緊急。 |
| 知識庫 | CARE 收錄的健康資訊文章。 |
| RAG | 先從知識庫找出相關文章，再讓 AI 根據這些文章回答的方法。 |
| uv | Python 的套件與版本管理工具。CARE 用它安裝套件、執行程式。 |
| `.venv` | uv 建立的 Python 虛擬環境資料夾。CARE 需要的套件裝在這裡，不影響電腦上其他的 Python。 |
| `uv.lock` | 記錄每個套件確切版本的檔案。大家照它安裝，環境才會一樣。 |
| pytest | Python 的測試工具。 |
| MongoDB | CARE 存放資料的資料庫。資料分成多個「集合」（collection），類似資料表。 |
| TTL | 資料的保存期限。期限一到，資料庫會自動刪除那筆資料。 |
| 產出物 | 由腳本產生、再提交進 repo 的檔案，例如藥證庫與藥品外觀縮圖。 |
| 映像 | 部署用的 Docker 映像。它包含程式，以及 `resources/` 裡的檔案。 |
| CI | GitHub 上自動執行測試的流程。 |
| 藥品許可證（藥證） | 食藥署核發給每個藥品的許可證。每張許可證有一個字號。 |
| 藥證庫 | `resources/drug_catalog.json`。由食藥署開放資料整理成的藥品查表。藥袋辨識用它比對藥名。 |
| 藥品外觀縮圖 | `resources/drug_appearance/` 裡的藥丸照片縮圖。 |
| STT | 語音轉文字（speech-to-text）。 |

## 在自己的電腦上執行後端

開始前，請先知道：

- 後端從 `.env` 檔讀取設定。範本是 `.env.example`。
- **注意**：`.env` 裡的 `APP_ROLE` 預設是 `all`。`all` 會同時啟動排程器，排程器負責用藥提醒與每日諮詢摘要。如果你的 `.env` 用的是正式環境的資料庫與 LINE 金鑰，本機的後端可能會推播給真實使用者。

### 用初始化腳本安裝

1. 安裝 uv。uv 會管理套件與 Python 版本。

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh    # 或 brew install uv
   ```

2. 在 CARE 資料夾執行初始化腳本。腳本會建立 `.venv`、照 `uv.lock` 安裝套件，然後執行測試。

   ```bash
   ./init.sh          # macOS / Linux
   ```

   ```powershell
   .\init.ps1         # Windows PowerShell
   ```

3. 啟動後端：

   ```bash
   uv run uvicorn app.main:app --port 8000 --reload --reload-exclude .venv
   ```

   後端會在 port 8000 接收連線。`--reload` 讓後端在程式碼改變時自動重新啟動。`--reload-exclude .venv` 讓 `.venv` 裡的變動不會觸發重新啟動。

### 手動安裝（可選）

不使用初始化腳本時，執行：

```bash
uv sync                                              # 建立 .venv，並照 uv.lock 安裝套件
uv run uvicorn app.main:app --reload --port 8000
```

`uv run` 執行前，會自動把 `.venv` 更新到 `uv.lock` 的狀態，所以不需要先啟用（activate）虛擬環境。如果你想進入虛擬環境的 shell，執行 `source .venv/bin/activate`（Windows：`.venv\Scripts\activate`）。

## Agent 流程追蹤實驗

所有相關實驗的時間線、題庫、前期試跑、兩批 600 筆結果、診斷與執行 journal，見 [實驗總交接](evals/experiment_handoff/README.md)。

本地 CARE／LangSmith 的重跑程式、測試 JSON、歷史結果與交接說明，見 [實驗交接文件](evals/agent_trace/README.md)。

之前的 Healthnews 100 題測試、600 筆執行結果、異常與待審查事項，以及當時的程式／設定快照，見 [100 題測試交接](evals/healthnews_100/README.md)。

## 執行測試

開始前，請先知道：測試要用 `uv run` 執行。原因是用 `uv run`，`pytest-asyncio` 才會載入，非同步的測試（`async def test_xxx`）才能正常執行。

```bash
uv run pytest       # 或 uv run pytest tests/ -v
```

`uv run` 同樣會先把 `.venv` 更新到 `uv.lock` 的狀態，不需要先啟用虛擬環境。

Windows 範例：

```powershell
cd C:\你的路徑\CARE
uv run pytest tests/ -v
```

## 前端（LIFF 網頁）

前端程式不在這個 repo，而在另一個 repo：`CARE-LIFF`。

開始前，請依 `CARE-LIFF/README.md` 的說明，把 `.env.example` 複製成 `.env`，並設定 `VITE_LIFF_ID` 與 `VITE_API_BASE_URL`。

在 `CARE-LIFF` 資料夾執行：

```bash
npm install    # 第一次執行，或套件有變更時
npm run dev    # 啟動開發伺服器，預設網址 http://localhost:5173
npm run test   # 執行前端測試
```

## 讓 LINE 訊息送到後端

### Webhook 網址

正式環境的 Webhook 網址是：

```
https://care.jamessu2016.com/line/callback
```

在 [LINE Developers 管理頁面](https://developers.line.biz/console/channel/2008834990/messaging-api) 的「Webhook URL」欄位，填入上面的網址。

**注意**：一個 LINE channel 只有一個 Webhook 網址。改掉這個網址後，所有使用者的訊息都會改送到新網址。

### 用本機後端測試

Cloudflare Tunnel 與 ngrok 都能把本機的後端（port 8000）暫時開放成公開網址。開始前，請先啟動本機後端。

#### Cloudflare Tunnel

開始前：你要先登入 Cloudflare Tunnel 帳號。

```bash
cloudflared tunnel run care-api
```

這個指令會把 tunnel 接到本機正在執行的後端（預設是 `uvicorn` 的 port 8000）。

> **待確認**：這一段可能已經過時。2026-10-24 檢查時，`care-api` 這條 tunnel 沒有任何連線。正式網址 `care.jamessu2016.com` 現在由 GCP 上的 care-vm 提供服務。使用前，請先確認這條 tunnel 對外的網址是什麼。

#### ngrok（其他組使用的方式，保留）

**注意**：第 2 步會改掉 LINE channel 的 Webhook 網址。如果你改的是上面連結的 channel，正式環境的使用者訊息會改送到你的電腦。測試完，請改回正式網址。

1. 執行：

   ```bash
   ngrok http 8000
   ```

2. 在 [LINE Developers 管理頁面](https://developers.line.biz/console/channel/2008834990/messaging-api) 把 Webhook URL 改成 `<ngrok 網址>/line/callback`。

## 部署

**注意**：程式推送到 `main` 分支後，`.github/workflows/trigger-deploy.yml` 會通知 `CARE-infra` 重新建置並部署前後端。

部署設定（Helm chart）放在另一個 repo：`CARE-infra`（與本 repo 同一層的 `CARE-infra/` 資料夾）。`kubectl` 與 `helm` 的操作方式見 `CARE-infra/README.md`。

- **Kubernetes（K8s）**：管理伺服器上多個服務的系統。CARE 用的是 K3s，一種輕量版的 Kubernetes。
- **Ingress**：把外部連線導到對應服務的設定。`CARE-infra` 目前使用 K3s 內建的 Traefik。

## 代理怎麼處理一則訊息

LINE 傳來的訊息經 `app/services/line_messaging/` 進入代理。代理用 LangGraph（定義流程步驟的函式庫）編排，程式在 `app/services/agent/agent.py`。回覆由 `app/services/line_messaging/reply/reply.py` 送出。

```
START → guardrail ─┬─ 緊急 ───→ emergency → END
                   └─ 不緊急 ─→ agent ─┬─ 不用工具 → END
                                       └─ tools ─┬─ 直接回覆 → END
                                                 └─ 回到 agent
```

1. guardrail 先檢查訊息。它判斷兩件事：可不可以查知識庫，以及狀況緊不緊急。
2. 判定緊急時，系統直接產生緊急卡片，不進入代理。
3. 不緊急時，代理決定要不要呼叫工具。不需要工具時，代理直接回覆。
4. 工具執行完後，有兩種走法：
   - 部分工具的結果直接組成回覆，流程結束。這些回覆是：知識庫回答（`rag_direct`）、知識庫查詢失敗（`rag_fail_direct`）、服藥狀況（`medication_direct`）、家人名單（`family_directory_direct`）、用藥問題（`medication_question_direct`）、回報服藥（`medication_report_direct`）。
   - 其他結果回到代理，由代理決定下一步。

### 工具

工具清單在 `app/tools/registry.py`。

下列工具一律提供給代理：

| 工具 | 用途 |
| --- | --- |
| `find_nearby_hospitals` | 依座標搜尋附近的醫療院所 |
| `find_nearby_facilities_by_department` | 依科別搜尋附近的院所 |
| `lookup_medical_facility` | 用院所名稱查電話、營業時間、科別、地址或地圖連結 |
| `request_location_quick_reply` | 請使用者分享 LINE 位置 |
| `submit_knowledge_report` | 使用者指出知識庫內容過時、缺失或有誤時，回報給營運團隊審核 |
| `open_official_site` | 使用者要開官網或 LIFF 入口時使用 |
| `share_care` | 分享 CARE 給朋友、邀請家人 |
| `suggest_department_for_symptom` | 依症狀建議要掛哪一科 |
| `get_medication_status` | 查本人或家人的服藥狀況 |
| `get_family_directory` | 查使用者自己的家庭名單與稱謂 |
| `record_medication_taken` | 使用者在聊天中回報「我吃過了」 |

下列工具只在 guardrail 允許查知識庫時提供：

| 工具 | 用途 |
| --- | --- |
| `get_rag_answer` | 用 RAG 回答知識庫問題 |
| `answer_from_uploaded_document` | 回答使用者先前上傳的 PDF 或檔案內容。只查這位使用者還沒過期的檔案，不查知識庫。 |
| `ask_about_my_medications` | 回答跟自己用藥有關的問題。內部也是跑 RAG，只是把使用者的藥名附在問題後面。 |
| `verify_claim` | 查核健康謠言。另外還要查核服務已設定（`CLAIM_VERIFICATION_ENABLED`）才提供。 |
| `verify_tv_news`、`ask_tv_news_channel` | 查核電視新聞畫面，或詢問是哪一台。開關與 `verify_claim` 相同。 |

## 連鎖藥局與藥妝門市

`resources/pharmacy_branches.json` 存放連鎖藥局與藥妝店的門市資料。資料來自各品牌官網，每筆都記錄來源網址與內容雜湊（由內容算出的檢查碼）。

目前的涵蓋範圍：

- 12 個品牌：有官方門市目錄。
- 屈臣氏：另外收錄有效的分公司登記地址。這些門市實際有沒有在營業，還沒確認。
- 博登：還沒有可驗證的官方名錄。

完整範圍見 [資料缺口與補齊紀錄](docs/pharmacy-coverage-gap.md)。

### 更新門市資料

開始前，請先知道：

- 部署不會自動匯入門市資料。要更新，必須自己執行下面的匯入指令。
- 每次更新都使用新的快取目錄。使用同一個快取目錄時，腳本會重用已經下載的原始資料。
- 加上 `--apply` 時，腳本會先備份，再寫入 `.env` 指定的 MongoDB。
- 同一個備份目錄不能重複套用。

步驟：

1. 下載各品牌官網的資料（`YYYYMMDD` 換成當天日期）：

   ```bash
   uv run scripts/sync_pharmacy_branches.py collect --cache-dir /tmp/care-branches-YYYYMMDD
   ```

2. 預覽匯入內容。這一步只產生預覽，不寫入資料庫：

   ```bash
   uv run scripts/sync_pharmacy_branches.py import
   ```

3. 寫入資料庫。先確認舊版服務是不是還在執行：

   - 舊版服務還在執行：先只新增門市。

     ```bash
     uv run scripts/sync_pharmacy_branches.py import --insert-only --apply
     ```

   - 舊版服務沒有在執行：

     ```bash
     uv run scripts/sync_pharmacy_branches.py import --apply
     ```

4. 重新載入後端的院所名稱索引。後端在啟動時載入這個索引（`app/main.py`），所以重新啟動後端就會重新載入。

匯入報告預設放在 repo 外面的 `../outputs/pharmacy-branches-<時間>/`。報告包含：

- 修改前的 BSON 備份（BSON 是 MongoDB 的資料格式）
- 新增的 ID
- 匯入計畫

### 搜尋時查哪個集合

- 使用者問「附近藥局」時，後端查 `medical_facilities_pharmacy`。
- 使用者指定品牌或店名時，後端另外查 `medical_facilities_retail`（藥妝、醫材門市）。
- 健保特約狀態或座標不明時，資料保持「未知」。
- 沒有座標的門市可以用名稱搜尋、用地址導航，但不參加距離排序。

## 藥證庫（藥袋辨識用）

`resources/drug_catalog.json`（藥證庫）是藥袋辨識比對藥名用的離線查表。它由食藥署開放資料建立，已經提交進 repo。後端執行時不對外連線。

**一般開發不需要做任何事。**

### 什麼時候要重新產生

只有一種情況：食藥署更新了資料集（每 7 日同步一次），而你想跟上最新資料。

```bash
python -m scripts.build_drug_catalog        # 覆寫 resources/drug_catalog.json
pytest tests/unit/resources/test_drug_catalog_artifact.py
git add resources/drug_catalog.json         # 讓這次更新自成一個可審查的 commit
```

腳本會下載兩個資料集：「全部藥品許可證資料集」與「藥品外觀資料集」。這兩個端點的路徑叫 `/json`，但實際回傳的是 ZIP 檔。

2026-10-24 檢查時，藥證庫有 66,523 筆條目，大小約 24.7 MB（25,937,438 bytes）。

### 背景說明

**為什麼把產出物提交進 repo，而不是在建置時產生**：

- `Dockerfile` 已經把 `resources` 複製進映像（`COPY resources ./resources`），所以提交的檔案會直接進入映像。
- 如果改成建置時下載，每次部署都要依賴政府網站正常運作。網站維護時，部署就會失敗。
- `resources/` 本來就放已提交的產出物，例如 rich menu（LINE 聊天室下方的選單）各語系的 PNG 圖片。
- 取捨與量測見 `openspec/changes/prescription-bag-scan/design.md` 決策 3。

**藥證庫不存在時會怎樣**：

- 後端照常啟動。
- 但所有藥名都比對不到，信心會降為低。每份辨識草稿都會被判為需要人工逐筆核對。
- 這個降級方向是安全的，但功能等於壞了一半。
- 所以 `tests/unit/resources/test_drug_catalog_artifact.py` 會在藥證庫遺失或損壞時讓測試失敗，不讓功能在沒人發現的情況下變差。

## 藥品外觀縮圖

`resources/drug_appearance/` 存放藥丸照片的縮圖：

- 規格：160×160 JPEG。
- 檔名：藥品許可證字號經 SHA-256 雜湊後的前 16 個字元。
- 用途：推播與 LIFF 網頁顯示縮圖，讓長輩除了看藥名，也能看外觀確認「該吃哪一顆」。

縮圖跟藥證庫用同一套原則：建置期產生、提交進 repo，執行時不對外連線（不直接連 `mcp.fda.gov.tw`）。

**一般開發不需要做任何事。**

### 什麼時候要重新產生

只有一種情況：食藥署更新了藥品外觀資料集（每 7 日同步一次），你要補上新藥證的照片。

開始前，請先知道：

- 抓圖前要先安裝 [ImageMagick](https://imagemagick.org/)（`magick` 指令）。
- 全量抓取會對 `mcp.fda.gov.tw` 發出六千多次請求，下載約 20 GB 原圖（原圖縮小後就丟掉），實測約 86 分鐘。這對政府主機是不小的負載。
- 所以這一步刻意設計成預設關閉，要明確加上 `--fetch-images` 才會執行。它不屬於 `build_drug_catalog` 的一般流程，也不在 CI 或部署流程裡。
- 可以中斷再續跑：已經存在的縮圖會跳過，不會重新下載。重新執行時只會抓缺少的部分。資料集小幅更新時，也只需補抓新增的藥證。

```bash
python -m scripts.build_drug_catalog --fetch-images
pytest tests/unit/resources/test_drug_appearance_images.py
git add resources/drug_appearance/
```

### 背景說明

**縮圖怎麼做**：腳本把原圖縮成正方形，置中、補白邊、保留尺規、不裁切。原因如下：

- 藥丸上的刻痕與標註，縮成任何尺寸都看不清楚。這部分本來就交給外觀資料的文字欄位。
- 但同名的候選藥常常同色同形。尺規顯示的長度差，是唯一能分辨它們的線索，所以不裁切。
- 詳見 `openspec/changes/drug-appearance-photo/design.md` 決策 6。

**為什麼把產出物提交進 repo，而不是在建置時產生**：理由跟藥證庫相同：部署不依賴政府網站正常運作，建置映像時也不必每次對外抓 20 GB。取捨與量測見 `openspec/changes/drug-appearance-photo/design.md` 決策 2、3。

**縮圖不存在或損壞時會怎樣**：

- 如果 `license_number`（許可證字號）還沒確定，或對應的縮圖不存在，介面與推播一律退回原本的純文字版面，不會出現空白的圖片區塊。
- 外觀照片是加分項，不是建立藥品或顯示提醒的必要條件。
- `tests/unit/resources/test_drug_appearance_images.py` 會在下列情況讓測試失敗：縮圖目錄不見、內容損壞，或跟藥證庫對不上。理由跟藥證庫的測試相同：這類問題不會讓任何程式報錯，只會讓縮圖悄悄全部消失。

## 看診錄音（陪診逐字稿）

長輩看診時可以錄音。CARE 把錄音轉成文字與摘要，再把摘要傳到本人與有權限的家人的 LINE 聊天室。

2026-09-22 起，錄音改在 LINE 聊天室進行，不經過 LIFF 網頁（程式在 `app/services/clinic_transcript/line_flow.py`）。

### 使用前必須知道

- **一定要先徵得醫師同意。** 衛福部《醫療機構醫療隱私維護規範》第二點要求：診療過程錄音前，必須先徵得對方同意。系統的同意欄位（`consent`）沒有預設值。
- 目前只能替自己錄。家人陪診時「替長輩錄」的功能，還沒從 LIFF 搬過來。
- 系統只整理錄音的前 30 分鐘。

### 操作流程

1. 開始錄音。有兩種方式：
   - 看診前 1 小時，CARE 會送出掛號提醒。在提醒的「我已出發」卡片上，按「看診時錄音」。只有本人收到的卡片有這個按鈕。
   - 直接傳「看診錄音」。
2. 回答是否同意錄音。系統會顯示三個快速回覆：「醫師同意錄音」「我出來自己講」「不錄了」。
   - 醫師不同意時，選「我出來自己講」（`self_recap`）：走出診間後，自己口述看診內容。摘要卡會標明這是「就診者記得的版本」。
3. 錄音。你接著傳的下一則語音，就是看診錄音。你也可以用手機的錄音程式錄完，再把音檔分享進聊天室。
4. 系統在背景整理完後，推播摘要。

例外：如果你沒有先開始錄音，就直接傳了很長的錄音（語音 3 分鐘以上、音檔 1 MB 以上），系統會先問你要不要整理，不會把它當成一般問題交給代理。

「等錄音」的狀態存在 MongoDB 的 `clinic_recording_sessions`，保存 3 小時（TTL）。後端重新啟動時，這個狀態不會不見。

### 運作方式

**華語用 Gemini，台語用 Taigi AI Labs 的台語 STT**（`app/services/speech/clinic_transcribe.py`）：

1. 系統把錄音切成每段 5 分鐘，平行送給 `gemini-3.5-transcribe`。
2. 有聲音、但 Gemini 沒轉出文字的時段，系統改送台語 STT 補上。原因是這個 Gemini 模型碰到台語，幾乎轉不出字。

- 系統不需要先判斷整份錄音是什麼語言。醫師講華語、長輩回台語時，兩種語言各自處理。
- 台語 STT 跟聊天室的語音訊息共用同一把金鑰，上限是每分鐘 10 次。看診錄音每 10 秒送一段，把額度留給聊天室。

**系統不會標示哪一句是醫師講的。** 語者分離（判斷錄音裡哪裡換了說話的人）只用來決定在哪裡分段，段落不帶講者身分。摘要的提示詞也明確禁止寫「醫師說」。

**藥名用使用者自己的用藥清單標示，不改寫逐字稿。**

- 比對門檻是 0.80，由 `scripts/measure_clinic_drug_hints.py` 量測得出。完整結果寫在 `drug_hints.MATCH_THRESHOLD` 的註解裡。
- 劑量數字一律不比對，也不更正。

**逐字稿統一使用台灣正體字。**

- 原因：華語的辨識結果是簡體字，台語的辨識結果是正體字。
- 所以系統先判斷整份逐字稿是簡體還是正體，再用 OpenCC（簡繁轉換工具）的 `s2tw` 轉換。`s2tw` 只換字形，不換用語。
- 已經是正體的文字不能再轉一次，否則「干擾」會變成「幹擾」。

**系統不保存音檔。**

- 流程是：寫進暫存檔 → 背景轉錄讀取暫存檔 → 不論成功或失敗，都刪除暫存檔。
- 資料庫只存文字，保存 30 天，由 MongoDB 的 TTL 自動刪除。這跟對話原文的保存規則相同。

**摘要直接傳到聊天室**（2026-09-22 James 決定，本人與家人相同）。

- 完整逐字稿要按按鈕，在 LIFF 檢視頁開啟。
- 權限等級是 `SENSITIVE`。

### 實測結果

2026-09-22 用一集 30 分鐘的 podcast 測試：

| 做法 | 華語 | 全台語 |
| --- | --- | --- |
| 目前做法（切段平行送出） | 25 秒 | 19 分鐘（修正節流後推估 16 分鐘），補回 8,574 字 |
| 舊做法（整個檔案送 Gemini） | 96 秒 | 輸出超過長度上限，被 `MAX_TOKENS` 截斷 |

### 設定

| 設定 | 說明 | 預設 |
| --- | --- | --- |
| `CLINIC_RECORDING_MAX_BYTES` | 錄音檔上限（LIFF 上傳與聊天室下載共用），只擋明顯誤用 | 40 MB |

### 還沒驗證

- 真實 LINE 上的整條流程：錄音鍵能錄多長、鎖螢幕會不會中斷、分享進來的音檔類型（MIME）。
- 實際診間的收音品質。
- 醫師講華語、長輩講台語混在一起時的效果。

## n8n 多媒體處理

n8n 是流程自動化工具。CARE 用它處理使用者傳來的多媒體檔案。n8n 與兩個本機服務的設定在另一個 repo：`CARE-n8n`。

開始前：請先安裝 Docker Desktop。

1. 在 `CARE-n8n` 資料夾啟動服務：

   ```bash
   docker compose up -d
   ```

   啟動後會有三個服務：

   - n8n：`http://localhost:5678/`
   - local-asr（本機語音轉文字）：port `8200`
   - local-parser（本機檔案解析）：port `8100`

2. 在 n8n 匯入 `CARE-n8n/resources/workflows/mutimedia process.json`，填入 API key，然後 publish（啟用）。
3. 對 webhook `http://localhost:5678/webhook/multimedia-process` 送出 POST 請求，body 帶上要解析的檔案。

## direnv：自動啟用虛擬環境（可選）

direnv 是進入資料夾時自動設定環境變數的工具。專案有 `.envrc`。執行 `direnv allow` 後，每次進入資料夾，direnv 都會自動啟用虛擬環境，並載入 `.env` 的設定。

> **注意**：目前 `.envrc` 啟用的是 `venv` 資料夾，但 uv 建立的是 `.venv`。如果你用 uv 安裝，direnv 啟用的就不是 uv 建立的那個環境。

- 關掉目前 shell 的虛擬環境：`deactivate`
- 停止 direnv 自動啟用：`direnv deny`

## 聲明

目前沒有。
