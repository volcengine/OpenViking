# Privacy Configs and Skill Privacy Extraction/Restore

This page explains OpenViking privacy configs and how they work with skill write-time extraction and read-time restore.

## Goal

Privacy configs separate sensitive values (such as `api_key`, `token`, `base_url`) from skill body content to reduce plaintext storage of recognized values in `SKILL.md`, with version lookup and switching. Extraction relies on a model and replaces only recognized, matched spans; it does not guarantee discovery of every sensitive value.

Core goals:

- Auto-extract sensitive values at write time and replace with placeholders
- Auto-restore values from the active config at read time
- Provide version query, switching, and auditability

---

## Core Objects and Storage Layout

Privacy configs are keyed by `category + target_key`.

- `category`: config category (currently skill uses `skill`)
- `target_key`: target identifier (usually the skill name)

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

`current.json` and `history/version_x.json` are full `values` snapshots.

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

## Skill Privacy Extraction (Write Path)

When adding a skill via `add_skill`, OpenViking runs extraction + placeholderization.

```
add_skill
  -> SkillProcessor._sanitize_skill_privacy
    -> extract_skill_privacy_values (LLM extracts values)
    -> placeholderize_skill_content_with_blocks (replace plaintext with placeholders)
    -> privacy.upsert(category="skill", target_key=skill_name, values=...)
  -> write placeholderized SKILL.md
```

### Key Points

1. **Extraction source**: model returns JSON; `values` is used as privacy key-value pairs.  
2. **Content replacement**: matched plaintext is replaced by placeholders:
   - <code>&#123;&#123;ov_privacy:skill:&#123;skill_name&#125;:&#123;field_name&#125;&#125;&#125;</code>
3. **Block mapping captured**:
   - `original_content_blocks`
   - `replacement_content_blocks`
4. **Persisted content**: matched spans become placeholders; unmatched content remains unchanged. This processes the skill body, not every attached file.

---

## Skill Restore on Read (Load Path)

When reading `SKILL.md`, `FSService.read` attempts placeholder restoration automatically.

```
fs.read(uri)
  -> get_skill_name_from_uri(uri)
  -> privacy.get_current(category="skill", target_key=skill_name)
  -> restore_skill_content(content, skill_name, current.values)
```

### URI Matching

Current matching is suffix-based: `/skills/{name}/SKILL.md`, so it supports user-scoped skill paths such as:

- `viking://~/skills/{name}/SKILL.md`
- `viking://user/{user_id}/skills/{name}/SKILL.md`

### restore Rules

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

> Current implementation only runs restore when a `current` privacy config exists for that skill. If no current config exists, no notice is appended.

---

`read` returns restored values, and extra-config notices also include values. Privacy APIs and historical snapshots can return full values as well. This feature does not redact responses or encrypt storage; see [Storage Encryption](./10-encryption.md) for encryption.

## Relationship with CLI/API

- Management plane: Privacy API/CLI for versions (query, write, rollback)
- Content plane: `read` automatically restores placeholders
- Separation of concerns: content files hold placeholders; privacy service holds sensitive values and versions

Common commands:

```bash
ov privacy categories
ov privacy list skill
ov privacy skill search-web
ov privacy upsert skill search-web --values-json '{"api_key":"..."}'
ov privacy activate skill search-web 1
ov read viking://~/skills/search-web/SKILL.md
```

---

## Benefits

- Reduces plaintext sensitive data exposure in skill content
- Version history supports configuration changes after external credential rotation
- Transparent to callers: `read` returns restored executable skill text
- Notice block improves troubleshooting when config is incomplete or over-provisioned

---

## Related Documentation

- [Skills](../api/04-skills.md) - Skill write/read API
- [Privacy API](../api/10-privacy.md) - Privacy config endpoints
- [Context Extraction](./06-extraction.md) - Main extraction pipeline
- [Architecture Overview](./01-architecture.md) - Layering and module relationships
