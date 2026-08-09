## MODIFIED Requirements

### Requirement: Voice reply one-tap toggle

When handling postback `action=toggle_voice_reply`, the dispatcher SHALL support flipping the user's current voice-reply setting when `enabled` is omitted, and SHALL still honor explicit `enabled=true|false`. The Rich Menu toggle and the LIFF settings page SHALL act on the same stored user setting, so a change made through either entry point is visible from the other.

#### Scenario: Omit enabled flips current setting from false to true
- **WHEN** postback data is `action=toggle_voice_reply` and the user's current `voice_reply_enabled` is false
- **THEN** the system MUST persist `voice_reply_enabled=true` and reply that voice reply was turned on

#### Scenario: Omit enabled flips current setting from true to false
- **WHEN** postback data is `action=toggle_voice_reply` and the user's current `voice_reply_enabled` is true
- **THEN** the system MUST persist `voice_reply_enabled=false` and reply that voice reply was turned off

#### Scenario: Explicit enabled remains supported
- **WHEN** postback data is `action=toggle_voice_reply&enabled=true` (or `false`)
- **THEN** the system MUST set `voice_reply_enabled` to that boolean value regardless of the previous value

#### Scenario: Rich Menu toggle is reflected in the LIFF settings page
- **WHEN** the user toggles voice reply from the Rich Menu and then opens the LIFF settings page
- **THEN** the settings page MUST show the updated `voice_reply_enabled` value
