## ADDED Requirements

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

#### Scenario: Medication reminder opens family LIFF page as interim target
- **WHEN** the user taps the top-middle area
- **THEN** the action MUST be a URI to `{LIFF_URL}/family`

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
