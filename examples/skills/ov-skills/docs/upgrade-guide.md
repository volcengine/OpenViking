# Upgrade Guide: Generic Commands to `ov skills`

`ov add-skill` is the same command as `ov skills add` and takes the same flags, so scripts that call it keep working.

## Generic vs `ov skills` Commands

| Scenario | Generic Command | `ov skills` Command |
|---|---|---|
| Install from GitHub tree | `git clone <repo>` then manual upload | `ov skills add https://github.com/anthropics/skills/tree/main/skills/algorithmic-art` |
| List installed skills | `ov ls viking://~/skills` | `ov skills list` |
| Search skills | Generic `ov find` across all context | `ov skills find "video generation"` |
| View skill content | `ov read viking://~/skills/video-generate/SKILL.md` | `ov skills show video-generate` |
| View auxiliary files | `ov tree viking://~/skills/video-generate` | `ov skills show video-generate --files` |
| Update skill | Re-upload manually | `ov skills update video-generate` |
| Delete skill | `ov rm viking://~/skills/video-generate --recursive` | `ov skills remove video-generate` |
| Validate skill format | No dedicated command | `ov skills validate ./skills/my-skill` |

## Key Differences

1. **Unified namespace**: `ov skills` operates on the skill roots, the private `viking://~/skills` and the account-shared `viking://agent/skills`, not the generic resources tree. `list` and `find` cover both roots; `-p` narrows a command to one.
2. **Source tracking**: Skills installed via `ov skills add` record their source in `.source.json`; skills installed from Git can be refreshed with `ov skills update`.
3. **Level-based viewing**: `ov skills show` supports `-L 0/1/2` to view abstract, overview, or full content.
4. **GitHub tree support**: Direct installation from `https://github.com/.../tree/...` URLs without manual clone.
5. **Cleanup**: `ov skills remove` also deletes the skill's privacy configuration; `ov rm` only deletes the skill directory.
