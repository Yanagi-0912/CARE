# LINE 功能導覽與操作示範

新好友會收到歡迎輪播，六頁功能各有「看操作示範」按鈕。傳送「功能說明」可再次叫出同一份導覽。按下示範按鈕後，機器人依使用者的語言回覆單獨一張影片卡。

支援繁體中文、英文、印尼文、越南文、泰文與日文；選擇台語的使用者沿用繁體中文影片。六類功能各一支，共 36 支 MP4，並各附一張 JPG 預覽圖。

## 產生影片

從 CARE 根目錄執行。製作環境需另外安裝 Playwright、Chromium 與 FFmpeg；這些工具不加入後端執行期依賴。

```sh
python scripts/feature_videos/render_feature_videos.py
python scripts/feature_videos/render_feature_videos.py --lang zh-TW --section ai
```

使用已安裝的 Chromium 時，加上 `--chromium /absolute/path/to/chromium`。影片使用模擬聊天室與虛構資料；固定介面文字來自 `app/i18n/messages.py`，操作劇本在 `scenes.py`，畫面模板為 `chat.html`。修改介面文字或操作流程後，請重新產生對應影片。

產物位於 `resources/feature_videos/{language}/{section}.mp4` 與 `.jpg`。MP4 使用 H.264、yuv420p、720×1080，並將 moov 移至檔案開頭，供播放器分段下載。影片與預覽圖會隨 Dockerfile 的 `COPY resources` 放入後端映像。

## 設定與驗證

`PUBLIC_BASE_URL` 應設為 LINE 可公開存取的 HTTPS 來源，例如 `https://care.example.com`。未設定時，導覽會省略影片按鈕。媒體路徑為 `/api/feature-videos/{language}/{section}.mp4`，沿用 ingress 已導向後端的 `/api` 路徑。

先執行專案檢查：

```sh
./init.sh
```

影片專用測試會檢查所有語言及分類的檔案、大小、預覽尺寸、faststart、靜態路由、Range 回應、影片卡與按鈕分派。部署後可檢查實際公開來源的回應：

```sh
curl --fail --range 0-99 --output /dev/null --dump-header - \
  https://care.example.com/api/feature-videos/zh-TW/ai.mp4
```

應得到 `206 Partial Content` 與 `Content-Type: video/mp4`；JPG 路徑應回 `image/jpeg`。請再於手機 LINE 叫出「功能說明」，檢查輪播、相機／相簿快速回覆及六頁影片的播放。

依 [LINE 官方影片規格](https://developers.line.biz/en/docs/messaging-api/create-flex-message-including-video/)，影片只能放在獨立 bubble 的 hero，不能放在輪播內，因此採用「導覽按鈕 → 單張影片卡」的流程。自動播放由使用者的 LINE 設定決定，電腦版不支援自動播放；不支援影片元件的舊版會顯示可點擊的預覽圖。
