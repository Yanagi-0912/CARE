## ADDED Requirements

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

### Requirement: Link Rich Menu when user language changes

When a user's `settings.language` is updated via the profile settings API, the system SHALL attempt to link the corresponding Rich Menu to that LINE user. Link failure MUST NOT fail the settings update response.

#### Scenario: Language change links matching menu
- **WHEN** `PATCH /api/profiles/me/settings` includes `language` set to a supported code and a richMenuId is configured for that code
- **THEN** the system MUST call LINE link-user-richmenu for that user with the mapped richMenuId

#### Scenario: Missing menu id is non-fatal
- **WHEN** language changes but no richMenuId is configured for that language (after fallback)
- **THEN** settings MUST still be saved and the API MUST return success without raising
