# Privacy Configs and Automatic Management

This page explains OpenViking privacy configs and how they work with skill and session write-time extraction and read-time restore.

## Goal

Privacy configs separate sensitive values (such as `api_key` and `token`) from content (such as skills and sessions). Automatically detected values are saved in the OpenViking user's privacy configs with version history. Content stores placeholders that can be restored from the active config at read time.

Core goals:

- When enabled, extract sensitive values at write time and replace with placeholders
- Auto-restore values at read time according to the content type
- Provide version query, switching, and auditability

---

## Core Objects and Storage Layout

Privacy configs are keyed by `category + target_key` within the current user's space.

| Content | `category` | `target_key` |
|---|---|---|
| Skill | `skill` | Skill name |
| Session | `session` | Session ID |
| Personal credentials | `credential` | For example, `github` or `database` |

Storage layout in user space:

```
viking://user/{user_id}/privacy/{category}/{target_key}/
├── .meta.json                 # metadata (active_version/latest_version/labels, etc.)
├── current.json               # active version snapshot
└── history/
    ├── version_1.json
    ├── version_2.json
    └── ...
```

`current.json` and `history/version_x.json` contain full privacy value maps (`values`). Session and skill bodies store references to these values (placeholders).

---

## Version Semantics

### upsert

- Each upsert treats incoming `values` as the candidate snapshot
- If `values` is identical to current `values`, no new version is created
- Otherwise a new version is created and activated
- New keys are allowed (no unknown-key rejection)
- `values` is a full snapshot, not a patch; omitted old keys are absent from the new version

### activate

- Sets a historical version as active (writes back to `current.json`)
- Updates `active_version` in `.meta.json`
- Does not rotate or revoke credentials at an external service; it only selects the values OpenViking uses

---

## Usage

Automatic sanitization is disabled by default (`mode=off`), while placeholder restoration is enabled (`restore=true`). Configure `privacy` through [User settings](../api/08-admin.md#user-settings), or set deployment defaults in `server.user_config_defaults.privacy`. User settings take precedence.

| `mode` | Where new sensitive values are discovered | Where registered values are matched |
|---|---|---|
| `off` (default) | Disabled | Disabled |
| `registered` | Disabled | Skills and Sessions |
| `auto` (recommended) | Skills and Sessions | Skills and Sessions |
| `auto_strict` | Skills and Sessions | Skills, Sessions, ordinary file writes, resource imports, and default reads |

`restore` is independent of `mode`: when enabled, `raw=true` reads of Skills and Sessions restore placeholders to their current values. Add detection rules with `custom_rules`, or use `never_mask` to exclude specific values from automatic discovery and matching.

> These settings do not affect explicit lookups with `ov privacy resolve`.

Built-in detectors recognize formats such as Volcengine Ark API keys, GitHub tokens, JWTs, and common private key formats. For credentials in other formats, register the value under `credential` or add a detection rule with `custom_rules`. Changes to these settings apply to subsequent read and write requests.

---

## Skill Privacy Extraction and Restore

### Registered-Value Protection (Write Path)

When adding a Skill via `add_skill`, `mode` controls registered-value protection and discovery of new values.

```
add_skill
  -> SkillProcessor.prepare_skill_privacy (find sensitive values in the body)
  -> SkillProcessor.apply_skill_privacy
    -> PrivacySanitizer (match registered values)
    -> replace matched text with placeholders
    -> save newly detected values in the Skill's Privacy config
  -> write placeholderized SKILL.md
```

#### Key Points

1. **Extraction source**: by default, extraction uses the rule detector and custom rules. Set `OPENVIKING_SKILL_PRIVACY_EXTRACTION_METHOD=vlm` to switch to model extraction.
2. **Content replacement**: matched plaintext is replaced by placeholders:
   - <code>&#123;&#123;ov_privacy:skill:&#123;skill_name&#125;:&#123;field_name&#125;&#125;&#125;</code>
3. **Metadata protection**: automatic discovery and value matching process the body, preserving metadata such as `name` and `description`.
4. **Persisted content**: matched spans become placeholders; unmatched content remains unchanged. This processes the skill body, not every attached file.
5. **Configuration retention**: updating a skill adds newly discovered fields while retaining existing fields and version history. Remove fields or configurations explicitly when they are no longer needed.

---

### Restore on Read (Load Path)

Reads of `SKILL.md` preserve placeholders by default. With explicit `raw=true` and `restore=true`, `FSService.read` attempts to restore them.

```
fs.read(uri)
  -> get_skill_name_from_uri(uri)
  -> restore_skill_content
    -> privacy.get_current(category, target_key)
    -> restore references in the content
```

#### URI Matching

Current matching is suffix-based: `/skills/{name}/SKILL.md`, so it supports user-scoped skill paths such as:

- `viking://~/skills/{name}/SKILL.md`
- `viking://user/{user_id}/skills/{name}/SKILL.md`

#### restore Rules

`restore_skill_content` behavior:

1. **Placeholder exists in content and value exists and is non-empty**  
   -> replace directly.

2. **Placeholder exists in content but value is missing/empty**  
   -> keep placeholder; add item to `unresolved_entries`.

3. **Config key is non-empty but not referenced by any placeholder in content**  
   -> add to extra-config notice (`Configured but not referenced in content`).

4. If `unresolved_entries` or extra-config entries exist, append notice block:
   - `[Privacy Config Notice]`
   - `Missing config: ...` (if any)
   - `Configured but not referenced in content: ...` (if any)

Placeholders that reference the current user's Privacy fields are also restored. References to other users or missing fields remain unchanged.

> Config notices for a skill are appended only when that skill has a `current` config.

---

With restoration enabled, `raw=true` reads return actual values, and extra-config notices also include values. Privacy APIs and historical snapshots can return full values as well. This feature does not encrypt storage; see [Storage Encryption](./10-encryption.md) for encryption.

## Session Privacy Extraction and Restore

### Automatic Extraction (Write Path)

The `mode` setting determines which sensitive values are replaced with placeholders in Session messages. Replacement happens before the messages are saved or tool output is externalized.

```
Session.add_messages_async
  -> extract_and_store_session_privacy
    -> PrivacySanitizer (match registered values)
    -> detect new values using built-in and custom rules (auto / auto_strict)
    -> save newly detected values in the Session's Privacy config
    -> replace matched text with placeholders
  -> externalize tool output
  -> write placeholderized messages
```

#### Key Points

1. **Matching sources**: the current user's registered credentials and values in this Session's Privacy config. When discovery is enabled, built-in and custom rules also detect new sensitive values.
2. **Coverage**: TextPart text, ContextPart abstracts, and ToolPart input and output, including strings nested in dictionaries or lists. Field names alone do not cause an entire value to be stored or masked.
3. **Content replacement**: sensitive spans in the original content are replaced with placeholders:
   - Sensitive values belonging to the current Session use the same placeholder format, with the Session ID or its hash value: <code>&#123;&#123;ov_privacy:session:&#123;session_id&#125;:&#123;field_name&#125;&#125;&#125;</code>.
   - If a sensitive span matches registered personal credentials (`credential`), prefer its field placeholder, for example <code>&#123;&#123;ov_privacy:alice:viking://~/privacy/credential/github#/values/pat&#125;&#125;</code>.
4. **Persisted content**: matched text is saved as placeholders; unmatched content remains unchanged. Repeated detections of the same value reuse the same entry in the Session's Privacy config. Existing placeholders are left unchanged.

---

### Restore on Read (Load Path)

Reads of Session messages or externalized tool output preserve placeholders by default. With explicit `raw=true` and `restore=true`, `FSService.read` attempts to restore them.

```
fs.read(uri)
  -> get_session_id_from_uri(uri, session_root)
  -> restore_session_content
    -> privacy.get_current(category, target_key)
    -> restore references in the content
```

#### URI Matching

Restoration is supported for these files under the current user's Session directory:

- `sessions/{session_id}/messages.jsonl`
- `sessions/{session_id}/history/archive_*/messages.jsonl`
- `sessions/{session_id}/tool-results/{id}/output.txt`

#### restore Rules

`restore_session_content` behavior:

1. **Placeholder exists in content and privacy value exists and is non-empty**

   -> Replace directly with the corresponding value. Whole-value placeholders in tool input restore the original JSON type.

2. **Placeholder exists in content but privacy value is missing or empty**

   -> Keep the placeholder unchanged.

3. **Placeholder in content refers to another user, or uses <code>&#123;&#123;ov_privacy:session:...&#125;&#125;</code> for another Session**

   -> Keep the placeholder unchanged.

> Restoration affects only the read result, without modifying stored files. References follow the active config: rotating a field or activating another version also changes the value used on the next read of historical messages.

---

## Relationship with CLI/API

- Management plane: Privacy API/CLI for versions (query, write, rollback)
- Content plane: restore placeholders by passing `raw=true` to `read`
- Separation of concerns: content files hold placeholders; the privacy service holds sensitive values and versions

PrivacySanitizer does not infer sensitivity from field names. String values in the active Privacy catalog participate in automatic content scanning and reference replacement only when they contain at least 6 Unicode characters. Shorter values can still be stored, referenced, and explicitly resolved, but are excluded from global substring replacement to reduce false positives in ordinary text. Python owns catalog loading, reference generation, and cache policy; matcher construction, leftmost-longest matching, and text rewriting run in RAGFS's Rust Aho-Corasick implementation through a reusable PyO3 matcher that releases the GIL while scanning. The matcher catalog is cached per user and process-local revision; local changes invalidate it immediately, and a 3600-second TTL bounds staleness after changes made by another worker or node.

Values stored under `credential` for the current user take priority. Otherwise, only values in the current Skill's or Session's Privacy config that match a recognized credential format are used. Matching respects word boundaries and skips values listed in `never_mask`.

Using a GitHub Personal Access Token (PAT) as an example:

```bash
# Create or rotate the PAT; credential/github is the target and pat is the field
ov privacy upsert credential github --field-pat 'github_pat_...'

# Inspect current targets and fields without exposing values
ov privacy tree --category credential --references

# Omit the field when pat is the target's only field
ov privacy reference credential github
ov privacy resolve credential github

# Equivalent explicit-field forms
ov privacy reference credential github pat
ov privacy resolve credential github pat
ov privacy resolve '{{ov_privacy:alice:viking://~/privacy/credential/github#/values/pat}}'

# Inspect/activate history, or remove pat (the final field also removes the target)
ov privacy versions credential github
ov privacy activate credential github 1
ov privacy delete credential github pat --yes

# Scan one explicit URI; the default only reports, while --fix replaces plaintext PATs
ov privacy scan --uri viking://~/resources/project-a
```

---

## Benefits

- Reduces plaintext sensitive data exposure in skill content
- Version history supports configuration changes after external credential rotation
- Transparent to callers: `read` with `raw=true` returns restored, executable skill content
- Notice blocks improve troubleshooting when config is incomplete or over-provisioned

---

## Related Documentation

- [Skills](../api/04-skills.md) - Skill write/read API
- [Sessions](../api/05-sessions.md) - Session creation and message writes
- [Privacy API](../api/10-privacy.md) - Privacy config endpoints
- [Context Extraction](./06-extraction.md) - Main extraction pipeline
- [Architecture Overview](./01-architecture.md) - Layering and module relationships
