# Privacy Configs and Automatic Management

This page explains OpenViking privacy configs and how they work with skill and session write-time extraction and read-time restore.

## Goal

Privacy configs separate sensitive values (such as `api_key` and `token`) from content (such as skills and sessions). Automatically detected values are saved in the OpenViking user's privacy configs with version history. Content stores placeholders that can be restored from the active config at read time.

Core goals:

- Auto-extract sensitive values at write time and replace with placeholders
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

## Skill Privacy Extraction and Restore

### Registered-Value Protection (Write Path)

When adding a Skill via `add_skill`, sensitive values are extracted and replaced with placeholders.

```
add_skill
  -> SkillProcessor._sanitize_skill_privacy
    -> PrivacySanitizer (protect registered personal credentials)
    -> extract_skill_privacy_values (LLM extracts values)
    -> placeholderize_skill_content_with_blocks (replace plaintext with placeholders)
    -> privacy.upsert(category="skill", target_key=skill_name, values=...)
  -> write placeholderized SKILL.md
```

#### Key Points

1. **Extraction source**: model returns JSON; `values` is used as privacy key-value pairs.  
2. **Content replacement**: matched plaintext is replaced by placeholders:
   - <code>&#123;&#123;ov_privacy:skill:&#123;skill_name&#125;:&#123;field_name&#125;&#125;&#125;</code>
3. **Block mapping captured**:
   - `original_content_blocks`
   - `replacement_content_blocks`
4. **Persisted content**: matched spans become placeholders; unmatched content remains unchanged. This processes the skill body, not every attached file.

---

### Restore on Read (Load Path)

Reads of `SKILL.md` preserve placeholders by default. With explicit `raw=true`, `FSService.read` attempts to restore them.

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

`read` returns restored values, and extra-config notices also include values. Privacy APIs and historical snapshots can return full values as well. This feature does not redact responses or encrypt storage; see [Storage Encryption](./10-encryption.md) for encryption.

## Session Privacy Extraction and Restore

### Automatic Extraction (Write Path)

When writing messages to a Session, values matching registered Privacy entries are replaced with placeholders before tool output externalization and message persistence. Automatic discovery of new sensitive values is temporarily disabled in code because false positives create excessive invalid entries. It will be revisited later; there is currently no runtime configuration switch.

```
Session.add_messages_async
  -> extract_and_store_session_privacy
    -> PrivacySanitizer (protect registered personal credentials)
    -> skip new-value discovery; do not add Session Privacy fields
  -> externalize tool output
  -> write placeholderized messages
```

#### Key Points

1. **Matching source**: only registered Privacy values are matched; fixed rules and sensitive field names no longer trigger new-value extraction.
2. **Coverage**: TextPart text, ContextPart abstracts, and ToolPart input and output. Tool inputs are traversed recursively to match registered values; field names no longer cause entire values to be stored and masked.
3. **Content replacement**: sensitive spans in the original content are replaced with placeholders:
   - Sensitive values belonging to the current Session use the same placeholder format, with the Session ID or its hash value: <code>&#123;&#123;ov_privacy:session:&#123;session_id&#125;:&#123;field_name&#125;&#125;&#125;</code>.
   - If a sensitive span matches registered personal credentials (`credential`), prefer the uniquely matching field's placeholder, for example <code>&#123;&#123;ov_privacy:alice:viking://~/privacy/credential/github#/values/pat&#125;&#125;</code>.
4. **Persisted content**: matched spans are saved as placeholders; unmatched content remains unchanged. No new Session Privacy data is created. Existing configs and restoration of historical placeholders remain supported.

---

### Restore on Read (Load Path)

Reads of Session messages or externalized tool output preserve placeholders by default. With explicit `raw=true`, `FSService.read` attempts to restore them.

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
- Content plane: automatically restore placeholders through `read`
- Separation of concerns: content files hold placeholders; the privacy service holds sensitive values and versions

PrivacySanitizer does not infer sensitivity from field names. String values in the active Privacy catalog participate in automatic content scanning and reference replacement only when they contain at least 6 Unicode characters. Shorter values can still be stored, referenced, and explicitly resolved, but are excluded from global substring replacement to reduce false positives in ordinary text. Python owns catalog loading, reference generation, and cache policy; matcher construction, leftmost-longest matching, and text rewriting run in RAGFS's Rust Aho-Corasick implementation through a reusable PyO3 matcher that releases the GIL while scanning. The matcher catalog is cached per user and process-local revision; local changes invalidate it immediately, and a 3600-second TTL bounds staleness after changes made by another worker or node.

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
- Transparent to callers: `read` returns restored, executable skill content
- Notice blocks improve troubleshooting when config is incomplete or over-provisioned

---

## Related Documentation

- [Skills](../api/04-skills.md) - Skill write/read API
- [Sessions](../api/05-sessions.md) - Session creation and message writes
- [Privacy API](../api/10-privacy.md) - Privacy config endpoints
- [Context Extraction](./06-extraction.md) - Main extraction pipeline
- [Architecture Overview](./01-architecture.md) - Layering and module relationships
