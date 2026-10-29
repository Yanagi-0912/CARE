# 管理員盲標題組

- `claim_pairs.json`：340 對主張，原始來源 `evals/claim_identity/dataset.jsonl`。
- `health.json`：1,720 則 Cofacts 訊息，原始來源為
  `paper1_claim_matching/data/samples/{natural,guardrail_random}.jsonl`。

兩份檔案只保留原本離線標註頁的盲標 payload：題目 id 與文字，以及原始資料雜湊。
不放原標籤、分類、資料分組或模型判斷。來源離線頁位於工作區的
`paper1_claim_matching/annotation/{claim_pairs_annotator,health_annotator}.html`。
不修改題目 id 或雜湊，讓新頁匯出可供既有的一致性分析腳本使用。

本編輯資料取自「Cofacts 真的假的」訊息回報機器人與查證協作社群，
採 CC BY-SA 4.0 授權提供。來源：https://cofacts.tw 。

資料隨後端 Docker 映像的 `resources/` 一起部署，沒有公開靜態路徑；
讀取題目、儲存答案與匯出都經過 `require_admin_user`。
