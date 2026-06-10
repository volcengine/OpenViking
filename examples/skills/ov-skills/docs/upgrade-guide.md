# Upgrade Guide: Legacy Skill Commands to `ov skills`

## Legacy vs New Commands

| Scenario | Old Command | New Command |
|---|---|---|
| Install local single skill | `ov resources add ./skills/my-skill/SKILL.md` | `ov skills add ./skills/my-skill` |
| Install local directory skill | `ov add-skill ./skills/my-skill/` | `ov skills add ./skills/my-skill` |
| Install from GitHub tree | `git clone <repo>` then manual upload | `ov skills add https://github.com/anthropics/skills/tree/main/skills/algorithmic-art` |
| List installed skills | `ov resources ls viking://user/default/skills` | `ov skills list` |
| Search skills | Generic find across resources | `ov skills find "video generation"` |
| View skill content | Manual read of skill directory | `ov skills show video-generate` |
| View auxiliary files | `ov resources tree viking://user/default/skills/...` | `ov skills show video-generate --files` |
| Update skill | Re-upload manually | `ov skills update video-generate` |
| Delete skill | `ov resources rm viking://user/default/skills/... --recursive` | `ov skills remove video-generate` |
| Validate skill format | No dedicated command | `ov skills validate ./skills/my-skill` |

## Key Differences

1. **Unified namespace**: `ov skills` operates on a dedicated skills namespace, not the generic resources tree.
2. **Source tracking**: Skills installed via `ov skills add` record their source in `.source.json`, enabling automatic `update`.
3. **Level-based viewing**: `ov skills show` supports `-L 0/1/2` to view abstract, overview, or full content.
4. **GitHub tree support**: Direct installation from `https://github.com/.../tree/...` URLs without manual clone.
