# Rich Menu Spec

## Purpose

定義 CARE LINE Rich Menu 六格版面、多語圖檔／label、LIFF deep link／location／語音 toggle 熱區契約、語音一鍵切換，以及使用者改語言時 link 對應選單。實作位於 `app/services/line_messaging/rich_menu_layout.py`、`rich_menu_service.py`、`scripts/setup_rich_menu.py`、`user_profile_service.py` 與 `dispatcher.py`。

## Requirements

### Requirement: Six-grid Rich Menu layout and asset

The setup script SHALL create a LINE Rich Menu of size 1200×810 with six equal areas of 400×405, and SHALL upload the image at `resources/rich_menu_zh-TW.png`.

#### Scenario: Area bounds cover the full canvas
- **WHEN** the Rich Menu object is created by the setup script
- **THEN** the six areas MUST use bounds  
  `(0,0)`, `(400,0)`, `(800,0)`, `(0,405)`, `(400,405)`, `(800,405)`  
  each with width 400 and height 405

#### Scenario: Image path is language-suffixed PNG
- **WHEN** the setup script uploads Rich Menu content
- **THEN** it MUST read `resources/rich_menu_zh-TW.png` and send Content-Type `image/png`

### Requirement: Rich Menu actions map to product entry points

Each of the six areas SHALL trigger the corresponding action below (labels are for documentation; LINE action labels MAY match Traditional Chinese product copy).

#### Scenario: Family center opens LIFF home
- **WHEN** the user taps the top-left area
- **THEN** the action MUST be a URI to `{LIFF_URL}/` (or equivalent home path under `LIFF_URL`)

#### Scenario: Medication reminder opens the medications LIFF page
- **WHEN** the user taps the top-middle area
- **THEN** the action MUST be a URI to `{LIFF_URL}/medications`

#### Scenario: Nearby hospitals requests location
- **WHEN** the user taps the top-right area
- **THEN** the action MUST be type `location`

#### Scenario: My family opens family LIFF page
- **WHEN** the user taps the bottom-left area
- **THEN** the action MUST be a URI to `{LIFF_URL}/family`

#### Scenario: Voice reply uses toggle postback without enabled flag
- **WHEN** the user taps the bottom-middle area
- **THEN** the action MUST be postback data `action=toggle_voice_reply` without an `enabled` query parameter

#### Scenario: Settings opens settings LIFF page
- **WHEN** the user taps the bottom-right area
- **THEN** the action MUST be a URI to `{LIFF_URL}/settings`

### Requirement: Voice reply one-tap toggle

When handling postback `action=toggle_voice_reply`, the dispatcher SHALL support flipping the user's current voice-reply setting when `enabled` is omitted, and SHALL still honor explicit `enabled=true|false`.

#### Scenario: Omit enabled flips current setting from false to true
- **WHEN** postback data is `action=toggle_voice_reply` and the user's current `voice_reply_enabled` is false
- **THEN** the system MUST persist `voice_reply_enabled=true` and reply that voice reply was turned on

#### Scenario: Omit enabled flips current setting from true to false
- **WHEN** postback data is `action=toggle_voice_reply` and the user's current `voice_reply_enabled` is true
- **THEN** the system MUST persist `voice_reply_enabled=false` and reply that voice reply was turned off

#### Scenario: Explicit enabled remains supported
- **WHEN** postback data is `action=toggle_voice_reply&enabled=true` (or `false`)
- **THEN** the system MUST set `voice_reply_enabled` to that boolean value regardless of the previous value

### Requirement: Localized Rich Menu area labels

`build_rich_menu_areas` SHALL accept a language code and set each area action `label` (and setup SHALL use localized chat bar text) for supported languages `zh-TW`, `en`, `id`, `vi`, `th`, `ja`. Unsupported language SHALL fall back to `zh-TW` labels.

#### Scenario: English labels for en
- **WHEN** `build_rich_menu_areas(liff_url, language="en")` is called
- **THEN** the six area labels MUST be Family, Meds, Hospitals, Relatives, Voice, Settings (in that cell order)

#### Scenario: Unknown language falls back to zh-TW labels
- **WHEN** language is not in the supported set
- **THEN** area labels MUST match the zh-TW set

### Requirement: Multi-language Rich Menu provisioning

The setup script SHALL create and upload one Rich Menu per supported language using `resources/rich_menu_{language}.png`, persist a language→richMenuId map, and set the zh-TW menu as the default for all users.

#### Scenario: Setup writes id map for all languages
- **WHEN** setup completes successfully for all languages
- **THEN** `resources/rich_menu_ids.json` MUST contain keys for zh-TW, en, id, vi, th, ja with non-empty richMenuId values

### Requirement: Setup removes the Rich Menus it superseded

The setup script SHALL delete the Rich Menus recorded by its previous run, so repeated runs do not accumulate orphaned menus on the LINE channel. Deletion SHALL happen only after the new menus are created, uploaded, and the default is set, so a failure mid-run never leaves users without a menu. Deletion SHALL be limited to ids read from `resources/rich_menu_ids.json` — menus the script did not create MUST NOT be touched.

#### Scenario: Previous run's menus are deleted
- **WHEN** setup completes and `resources/rich_menu_ids.json` held ids from an earlier run
- **THEN** each of those richMenuIds MUST be deleted via `DELETE /v2/bot/richmenu/{richMenuId}`

#### Scenario: Ids reused by the current run are never deleted
- **WHEN** an id present in the previous map is also among the newly created menus
- **THEN** that id MUST NOT be deleted

#### Scenario: Missing or unreadable id map skips cleanup
- **WHEN** `resources/rich_menu_ids.json` is absent, is not a JSON object, or cannot be parsed
- **THEN** setup MUST warn and continue without deleting anything

#### Scenario: Cleanup failure does not fail the run
- **WHEN** a delete call returns a non-200, non-404 status
- **THEN** setup MUST report the leftover richMenuId and still exit successfully, because the new menus are already live
- **AND** a 404 MUST be treated as already deleted

### Requirement: Link Rich Menu when user language changes

When a user's `settings.language` is updated via the profile settings API, the system SHALL attempt to link the corresponding Rich Menu to that LINE user. Link failure MUST NOT fail the settings update response.

#### Scenario: Language change links matching menu
- **WHEN** `PATCH /api/profiles/me/settings` includes `language` set to a supported code and a richMenuId is configured for that code
- **THEN** the system MUST call LINE link-user-richmenu for that user with the mapped richMenuId

#### Scenario: Missing menu id is non-fatal
- **WHEN** language changes but no richMenuId is configured for that language (after fallback)
- **THEN** settings MUST still be saved and the API MUST return success without raising
