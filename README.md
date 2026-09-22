# CARE: Clinical Assistance & Resource Engine

一個以高齡友善設計、資料準確性保障與 AI 科技整合為核心目標的適地性健康醫療資訊 AI 助手。

[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=Yanagi-0912_CARE&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=Yanagi-0912_CARE) [![SonarQube Cloud](https://sonarcloud.io/images/project_badges/sonarcloud-dark.svg)](https://sonarcloud.io/summary/new_code?id=Yanagi-0912_CARE)

## 聲明

暫無

## 快速開始

先安裝 [uv](https://docs.astral.sh/uv/)（相依與 Python 版本都由它管理）：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh    # 或 brew install uv
```

```bash
./init.sh          # macOS / Linux：建立 .venv、依 uv.lock 安裝依賴、跑 pytest
```

```powershell
.\init.ps1         # Windows PowerShell
```

啟動後端：

```bash
uv run uvicorn app.main:app --port 8000 --reload --reload-exclude .venv
```

### 手動安裝（可選）

```bash
uv sync                                              # 建 .venv 並照 uv.lock 安裝
uv run uvicorn app.main:app --reload --port 8000
```

`uv run` 會自己確保環境是最新的，不需要手動 activate。真的想進 shell 也可以
`source .venv/bin/activate`（Windows：`.venv\Scripts\activate`）。

### 前端（LIFF App）

```bash
cd frontend/liff-app
npm install    # 首次或 package 有變更時
npm run dev    # 預設 http://localhost:5173
npm run test   # 前端測試
```

## 執行測試

```bash
uv run pytest       # 或 uv run pytest tests/ -v
```

> 用 `uv run` 執行，`pytest-asyncio` 才會載入，非同步的測試（`async def test_xxx`）才會正常運作。
> 它會在跑之前自動把 `.venv` 同步到 `uv.lock` 的狀態，不必先手動 activate。

Windows 範例：

```powershell
cd C:\你的路徑\CARE
uv run pytest tests/ -v
```

## 藥證庫（藥袋辨識用）

`resources/drug_catalog.json` 是藥袋辨識比對藥名用的離線查表，由食藥署開放資料
建置後**提交進 repo**，執行期不對外連線。它已經在版本控制裡，**一般開發不需要
做任何事**。

需要重新產生的時機只有一個：食藥署資料集更新（每 7 日同步一次）而你想跟上。

```bash
python -m scripts.build_drug_catalog        # 覆寫 resources/drug_catalog.json
pytest tests/unit/resources/test_drug_catalog_artifact.py
git add resources/drug_catalog.json         # 更新內容自己成為一個可審查的 commit
```

腳本會抓「全部藥品許可證資料集」與「藥品外觀資料集」（兩個端點路徑叫 `/json`
但實際回傳 ZIP），輸出目前 66,478 筆條目、15.9 MB（16,660,025 bytes）。

**為什麼提交產出物而不是在 build 時產生**：`Dockerfile` 已經 `COPY resources
./resources`，提交進去就直接進映像；改成建置時下載會讓每次部署都依賴政府站台
活著，站台維護就等於部署失敗。`resources/` 本來就放已提交的產出物（rich menu
的各語系 PNG）。取捨與量測見
`openspec/changes/prescription-bag-scan/design.md` 決策 3。

**檔案缺席時的行為**：應用照常啟動，但所有藥名比對不到而降為低信心，每份辨識
草稿都會被判為需要人工逐筆核對。這個降級方向是安全的，但代表功能等於半殘，
所以 `tests/unit/resources/test_drug_catalog_artifact.py` 會在產出物遺失或損壞
時直接讓測試失敗，而不是讓它無聲地退化。

## 藥品外觀縮圖

`resources/drug_appearance/` 是藥丸照片的縮圖（160×160 JPEG，檔名為許可證
字號 SHA-256 前 16 字元），推播與 LIFF 用它讓長輩用外觀（而不只是藥名）確認
「該吃哪一顆」。跟藥證庫同一套原則：建置期產出、**提交進 repo**，執行期不對
外連線（不直連 `mcp.fda.gov.tw`）。它已經在版本控制裡，**一般開發不需要做
任何事**。

需要重新產生的時機只有一個：跟著食藥署藥品外觀資料集的更新（每 7 日同步
一次）補上新藥證的照片。

```bash
python -m scripts.build_drug_catalog --fetch-images
pytest tests/unit/resources/test_drug_appearance_images.py
git add resources/drug_appearance/
```

**這一步刻意設計成明確選用（`--fetch-images`，預設關閉），不是 `build_drug_catalog`
的一般流程，更不在 CI 或部署路徑上**：全量抓取要對 `mcp.fda.gov.tw` 發出六千
多次請求，下載約 20 GB 原圖（縮圖前即捨棄），實測要跑約 86 分鐘，對政府主機
是不小的負載。抓圖需要 [ImageMagick](https://imagemagick.org/)（`magick` 指令）
把原圖縮成置中補白、保留尺規、不裁切的正方形縮圖——刻痕與標註在任何縮圖尺寸
下都不可辨讀，那部分本來就交給外觀資料的文字欄位；但同名候選常常同色同形，
尺規顯示的長度差是唯一能分辨的線索，因此不裁切見
`openspec/changes/drug-appearance-photo/design.md` 決策 6。

**可中斷續跑**：已存在的縮圖一律跳過、不重新下載，所以可以隨時中斷，重新
執行只會抓缺的那一部分，資料集小幅更新時也只需補抓新增的藥證。

**為什麼提交產出物而不是在 build 時產生**：跟藥證庫同一個理由——部署不依賴
政府站台活著，映像建置不必每次都對外抓 20 GB。取捨與量測見
`openspec/changes/drug-appearance-photo/design.md` 決策 2、3。

**檔案缺席或損毀時的行為**：`license_number` 未確定或對應縮圖不存在時，介面
與推播一律退回既有的純文字版面，不會出現空的圖片區塊——外觀是加分項，不是
建立藥品或呈現提醒的必要條件。`tests/unit/resources/test_drug_appearance_images.py`
會在縮圖目錄消失、內容損壞、或跟藥證庫對不上時讓測試失敗，理由與
`test_drug_catalog_artifact.py` 相同：這類壞掉不會讓任何東西報錯，只會讓照片
悄悄地全部消失。

## 看診錄音（陪診逐字稿）

長輩看診時錄音，轉成文字與摘要，本人與有權限的家人在 LINE 聊天室直接收到摘要。2026-09-22 起**在 LINE 聊天室錄**，不經過 LIFF（`app/services/clinic_transcript/line_flow.py`）：

1. 開始：掛號提醒 T-1h「我已出發」卡片上的「看診時錄音」（只有本人版），或直接傳「看診錄音」。
2. 徵詢：快速回覆「醫師同意錄音／我出來自己講／不錄了」。
3. 下一則語音（或用手機錄音程式錄完分享進來的音檔）就是看診錄音；背景整理好推播摘要。
4. 沒先按就傳了很長的錄音（語音 3 分鐘以上、音檔 1 MB 以上）會先問要不要整理，不會被當成問題丟給 agent。

「等錄音」的狀態存在 `clinic_recording_sessions`（3 小時 TTL），backend 重啟不會掉。目前只替自己錄；家人陪診「替長輩錄」還沒從 LIFF 搬過來。

- **一定要先徵詢醫師同意**。衛福部《醫療機構醫療隱私維護規範》第二點要求診療過程錄音須先徵得對方同意；`consent` 沒有預設值。醫師不同意時走 `self_recap`（出診間後自己複述），摘要卡會標明那是就診者記得的版本。
- **華語走 Gemini、台語走台語 STT**（`app/services/speech/clinic_transcribe.py`）。切 5 分鐘平行送 `gemini-3.5-transcribe`，再把「有聲音但 Gemini 沒轉出字」的時段送 Taigi AI Labs 台語 STT 補（這個模型碰到台語幾乎不出字）。不用先判整份是什麼語言，醫師講華語、長輩回台語也各走各的路。台語 STT 與聊天室語音共用金鑰（每分鐘 10 次），看診錄音每 10 秒送一段、留額度給聊天室。
- **系統不會說哪一句是醫師講的**。語者分離只拿來決定哪裡換段，段落不帶講者身分。摘要的提示詞也明著禁止寫「醫師說」。
- **藥名靠使用者自己的用藥清單標示，不改寫逐字稿**。門檻 0.80 由 `scripts/measure_clinic_drug_hints.py` 量出，完整結果在 `drug_hints.MATCH_THRESHOLD` 的註解。劑量數字一律不比對也不更正。
- **逐字稿統一是台灣正體**。華語回簡體、台語回正體，所以整份先判斷再用 OpenCC `s2tw` 轉（只換字形不換用語）；正體再轉一次會把「干擾」變「幹擾」。
- **音檔不存**。寫進暫存檔 → 背景轉錄讀它 → 不論成功失敗都刪掉。資料庫裡只有文字，保存 30 天由 Mongo TTL 執行，與對話原文同一條線。只整理前 30 分鐘。
- **摘要直接進聊天室**（2026-09-22 James 決定，本人與家人都一樣），完整逐字稿用按鈕開 LIFF 檢視頁。權限走 `SENSITIVE`。

2026-09-22 用 30 分鐘 podcast 實測：華語 25 秒；全台語 19 分鐘（修正節流後推估 16 分鐘），補回 8,574 字。整檔送 Gemini 的舊做法華語 96 秒、台語被 `MAX_TOKENS` 截斷。

| 設定 | 說明 | 預設 |
| --- | --- | --- |
| `CLINIC_RECORDING_MAX_BYTES` | 錄音檔上限（LIFF 上傳與聊天室下載共用），只擋明顯誤用 | 40 MB |

還沒驗證：真 LINE 上的整條流程（錄音鍵能錄多長、鎖螢幕會不會斷、分享的音檔 MIME）、實際診間的收音品質、醫師華語＋長輩台語混講的效果。

## LINE Webhook（callback 網址）

- Callback URL：`https://care.jamessu2016.com/line/callback`

在 [LINE Developers 管理頁面](https://developers.line.biz/console/channel/2008834990/messaging-api) 的 webhook URL 請填入上面網址。

## Cloudflare Tunnel（已登入帳號可直接使用）

若你已登入 Cloudflare Tunnel 帳號，可直接執行：

```bash
cloudflared tunnel run care-api
```

這會直接開始監聽你本機啟動中的 Python 後端（預設是 `uvicorn` 跑在 `8000`）。

## ngrok（其他組使用方式，保留）

```bash
ngrok http 8000
```

在 [LINE Developers 管理頁面](https://developers.line.biz/console/channel/2008834990/messaging-api) 的 webhook URL 改為 `"ngrok url"/line/callback`。

## Kubernetes 與 Kong（Ingress）

後端的 K8s manifest 與 Kong Helm values 已獨立放在 **`CARE-infra`** 專案（與本 repo 同層目錄的 `CARE-infra/`），請到該目錄依 `CARE-infra/README.md` 操作 `kubectl` 與 `helm`。

## 代理與工具流程（摘要）

目前代理以 **LangGraph** 編排（`app/services/agent/agent.py`）：

```
START → guardrail → agent →（tools → agent）→ END
```

可用工具（`app/tools/registry.py`）：

- `get_rag_answer`：RAG 知識庫問答（guardrail 判定健康相關才啟用）
- `request_location_quick_reply`：引導使用者分享 LINE 位置
- `find_nearby_hospitals`：依座標搜尋鄰近醫療院所

LINE 訊息經 `app/services/line_messaging/` 進入代理，回覆由 `LineMessageService` 送出。

## n8n workflow 多媒體處理功能

1. 使用 docker 啟動 n8n，預設運行在 `http://localhost:5678/`；local ASR 與 file parser 兩服務分別運行在 port `8200` 和 `8100`。
2. 將 `resources/mutimedia process.json` import 至 n8n，填寫 api key 並 publish。
3. 向 webhook `http://localhost:5678/webhook/multimedia-process` 傳送 POST Request，body 中帶有要解析的檔案。

## direnv 虛擬環境提示字元（可選）

專案有 `.envrc`，`direnv allow` 後進目錄會自動啟用 `venv`。

- 關掉目前 shell 的虛擬環境：`deactivate`
- 關閉 direnv 自動啟動：`direnv deny`
