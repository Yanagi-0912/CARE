# Rich Menu Six-Grid Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓六格 Rich Menu（熱區／LIFF deep link／語音一鍵 toggle）有可測的純函式契約，並與 OpenSpec `rich-menu-six-grid` 對齊。

**Architecture:** 將六格 bounds 與 action 組裝抽到 `app/services/line_messaging/rich_menu_layout.py`（純函式、無 I/O）；`scripts/setup_rich_menu.py` 只負責 LINE API。語音 toggle 邏輯留在 `LineEventDispatcher`，以省略 `enabled` 時讀 profile 反轉。

**Tech Stack:** Python 3.12、pytest、LINE Messaging API、FastAPI CARE backend

**OpenSpec change:** `openspec/changes/rich-menu-six-grid/`（proposal / design / specs / tasks）

## Global Constraints

- 圖檔路徑必須為 `resources/rich_menu_zh-TW.png`；上傳 Content-Type `image/png`
- Rich Menu 尺寸必須為 width=1200、height=810；每格 width=400、height=405
- 六格 bounds 必須為 `(0,0)` `(400,0)` `(800,0)` `(0,405)` `(400,405)` `(800,405)`
- 語音 postback data 必須精確為 `action=toggle_voice_reply`（無 `enabled`）
- 測試禁止 monkey-patch 全域；使用 DI／直接呼叫純函式
- 本 plan 只 commit Rich Menu 相關檔案，勿納入其他 RAG／light-crag 改動
- 每完成一個 plan task，同步勾選 `openspec/changes/rich-menu-six-grid/tasks.md` 對應項（若已有則確認仍為 `[x]`）

---

### Task 1: Rich Menu layout 純函式 + 單元測試（TDD）

**Files:**
- Create: `app/services/line_messaging/rich_menu_layout.py`
- Create: `tests/unit/services/line_messaging/test_rich_menu_layout.py`
- Modify: `scripts/setup_rich_menu.py`（改為 import layout helpers，刪除重複實作）

**Interfaces:**
- Consumes: 無
- Produces:
  - `CELL_W: int = 400`
  - `CELL_H: int = 405`
  - `RICH_MENU_WIDTH: int = 1200`
  - `RICH_MENU_HEIGHT: int = 810`
  - `IMAGE_PATH: str = "resources/rich_menu_zh-TW.png"`
  - `def liff_uri(base: str, path: str) -> str`
  - `def build_rich_menu_areas(liff_url: str) -> list[dict]`

- [ ] **Step 1: Write the failing tests**

建立 `tests/unit/services/line_messaging/test_rich_menu_layout.py`：

```python
from app.services.line_messaging.rich_menu_layout import (
    CELL_H,
    CELL_W,
    IMAGE_PATH,
    RICH_MENU_HEIGHT,
    RICH_MENU_WIDTH,
    build_rich_menu_areas,
    liff_uri,
)


def test_liff_uri_strips_trailing_slash_and_joins_path():
    assert liff_uri("https://liff.line.me/abc/", "/family") == (
        "https://liff.line.me/abc/family"
    )


def test_liff_uri_adds_leading_slash_when_missing():
    assert liff_uri("https://liff.line.me/abc", "settings") == (
        "https://liff.line.me/abc/settings"
    )


def test_canvas_constants():
    assert RICH_MENU_WIDTH == 1200
    assert RICH_MENU_HEIGHT == 810
    assert CELL_W == 400
    assert CELL_H == 405
    assert IMAGE_PATH == "resources/rich_menu_zh-TW.png"


def test_build_rich_menu_areas_six_cells_and_actions():
    areas = build_rich_menu_areas("https://liff.line.me/abc")
    assert len(areas) == 6
    bounds = [(a["bounds"]["x"], a["bounds"]["y"]) for a in areas]
    assert bounds == [(0, 0), (400, 0), (800, 0), (0, 405), (400, 405), (800, 405)]
    for a in areas:
        assert a["bounds"]["width"] == 400
        assert a["bounds"]["height"] == 405

    assert areas[0]["action"] == {
        "type": "uri",
        "label": "家庭中心",
        "uri": "https://liff.line.me/abc/",
    }
    assert areas[1]["action"]["uri"] == "https://liff.line.me/abc/family"
    assert areas[2]["action"]["type"] == "location"
    assert areas[3]["action"]["uri"] == "https://liff.line.me/abc/family"
    assert areas[4]["action"] == {
        "type": "postback",
        "label": "語音回覆",
        "data": "action=toggle_voice_reply",
        "displayText": "切換語音回覆",
    }
    assert "enabled" not in areas[4]["action"]["data"]
    assert areas[5]["action"]["uri"] == "https://liff.line.me/abc/settings"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/services/line_messaging/test_rich_menu_layout.py -v`  
Expected: FAIL（module not found 或 import error）

- [ ] **Step 3: Write minimal implementation**

`app/services/line_messaging/rich_menu_layout.py`：實作上述 constants + `liff_uri` + `build_rich_menu_areas`（行為對齊現有 `scripts/setup_rich_menu.py` 內 `_liff_uri` / `_build_areas`）。

更新 `scripts/setup_rich_menu.py`：刪除本地 `_liff_uri` / `_build_areas` 與重複常數，改為：

```python
from app.services.line_messaging.rich_menu_layout import (
    IMAGE_PATH,
    RICH_MENU_HEIGHT,
    RICH_MENU_WIDTH,
    build_rich_menu_areas,
)
```

並在 `rich_menu_data["areas"]` 使用 `build_rich_menu_areas(liff_url)`。

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/services/line_messaging/test_rich_menu_layout.py -v`  
Expected: PASS

- [ ] **Step 5: Commit（僅本 task 檔案）**

```bash
git add app/services/line_messaging/rich_menu_layout.py \
  tests/unit/services/line_messaging/test_rich_menu_layout.py \
  scripts/setup_rich_menu.py
git commit -m "$(cat <<'EOF'
refactor(rich-menu): 抽出六格 layout 純函式並補單元測試

讓熱區與 LIFF／postback 契約可在 pytest 驗證，setup 腳本只負責 LINE API。
EOF
)"
```

---

### Task 2: 語音一鍵 toggle — 確認／補齊 TDD 覆蓋

**Files:**
- Modify (only if gaps): `app/services/line_messaging/dispatcher/dispatcher.py`
- Modify (only if gaps): `tests/unit/services/line_messaging/test_event_handler.py`

**Interfaces:**
- Consumes: `UserProfileService.get_user_profile` / `update_voice_reply_enabled`（既有 DI）
- Produces: postback `action=toggle_voice_reply` 無 `enabled` 時反轉；有 `enabled` 時強制設定

**Note:** 工作區可能已有實作與測試。本 task 必須先跑現有測試；缺哪個 scenario 就用 TDD 補哪個，禁止重寫無關邏輯。

- [ ] **Step 1: Run existing toggle tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/unit/services/line_messaging/test_event_handler.py \
  -k "toggle_voice_reply" -v
```

Expected: 至少包含  
`test_handle_postback_event_toggle_voice_reply_enabled`  
`test_handle_postback_event_toggle_voice_reply_disabled`  
`test_handle_postback_event_toggle_voice_reply_omitted_enabled_flips_on`  
`test_handle_postback_event_toggle_voice_reply_omitted_enabled_flips_off`  
全部 PASS。

若四個都 PASS → Step 2–4 標記為已滿足，直接 Step 5（若無新 diff 則跳過 commit，report DONE 並說明 no commit）。

- [ ] **Step 2: If any missing — write failing test first**

省略 `enabled`、profile `settings.voice_reply_enabled` 為 False → 應呼叫 `update_voice_reply_enabled(user_id, True)` 且回覆「已開啟語音回覆」。對稱案例 True→False。使用既有 `handler` / `mock_user_profile_service` fixture，禁止 `unittest.mock.patch` 改 dispatcher 內部。

- [ ] **Step 3: If failing — minimal dispatcher fix**

`toggle_voice_reply` 分支：`"enabled" in params` 時用布林；否則 `get_user_profile` + 解析後 `enabled = not current`。解析優先 `settings.voice_reply_enabled`，缺省 `False`。

- [ ] **Step 4: Re-run toggle tests — all PASS**

- [ ] **Step 5: Commit only if this task produced a diff**

```bash
git add app/services/line_messaging/dispatcher/dispatcher.py \
  tests/unit/services/line_messaging/test_event_handler.py
git commit -m "$(cat <<'EOF'
fix(line): 語音回覆 postback 支援一鍵反轉

省略 enabled 時讀取使用者設定並切換，並保留顯式 enabled 相容。
EOF
)"
```

---

### Task 3: 資產確認 + OpenSpec tasks 勾選 + 聚焦測試全綠

**Files:**
- Verify: `resources/rich_menu_zh-TW.png` 存在（1200×810）
- Modify: `openspec/changes/rich-menu-six-grid/tasks.md`（確認相關項為 `[x]`）
- Optional commit: 若 `resources/rich_menu_zh-TW.png` 尚未入 git，單獨 commit

- [ ] **Step 1: Verify asset**

```bash
test -f resources/rich_menu_zh-TW.png
sips -g pixelWidth -g pixelHeight resources/rich_menu_zh-TW.png
```

Expected: width 1200、height 810

- [ ] **Step 2: Run focused suites**

```bash
.venv/bin/python -m pytest \
  tests/unit/services/line_messaging/test_rich_menu_layout.py \
  tests/unit/services/line_messaging/test_event_handler.py \
  -q
```

Expected: all passed

- [ ] **Step 3: Ensure OpenSpec tasks checkboxes**

`openspec/changes/rich-menu-six-grid/tasks.md` 中 1.1、2.1、2.2、3.1、4.1 皆為 `- [x]`。若需新增「layout 單元測試」項可加在 tasks 末並勾選。

- [ ] **Step 4: Commit asset + openspec if untracked/modified**

```bash
git add resources/rich_menu_zh-TW.png openspec/changes/rich-menu-six-grid/
git commit -m "$(cat <<'EOF'
feat(rich-menu): 納入六格 zh-TW 圖與 OpenSpec change

提供 1200x810 選單資產與 rich-menu-six-grid 規格／tasks。
EOF
)"
```

若已 commit 則 skip。

---

## Spec coverage (self-review)

| Spec requirement | Task |
| --- | --- |
| Six-grid bounds + IMAGE_PATH png | Task 1, Task 3 |
| Actions: home/family/location/family/toggle/settings | Task 1 |
| Voice toggle omit/explicit enabled | Task 2 |

## Out of scope

- 實際上傳 LINE（需本機 `.env` 手動 `python scripts/setup_rich_menu.py`）
- 多語 Rich Menu 自動切換
- LIFF 用藥獨立頁
