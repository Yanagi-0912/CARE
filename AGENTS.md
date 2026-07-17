# CARE

工具中立的專案入口。所有規格與流程集中在 OpenSpec，任何編輯器或 AI（Cursor、VS Code、Claude Code、或不用 AI 的人）皆適用同一套。

- 規格（單一真相）：`openspec/specs/`
- 進行中的變更：`openspec/changes/`
- 專案設定與工作流程：`openspec/config.yaml`
- 環境安裝與測試：`./init.sh`（Windows：`.\init.ps1`）
- 人類操作說明（啟動、LINE webhook、部署）：`README.md`

工作流程：`openspec new <change>` → 寫 proposal/tasks → 實作 → `./init.sh` 全綠 → commit/PR → `openspec archive <change>`。

OpenSpec CLI 說明：https://github.com/Fission-AI/OpenSpec
