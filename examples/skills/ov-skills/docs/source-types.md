# Supported Skill Source Types

## Local Directory

A directory containing a `SKILL.md` file at its root. All files in the directory become auxiliary files. The CLI zips the directory and uploads it, so the path must exist on the machine running `ov`.

```
./my-skill/
  ├── SKILL.md
  ├── helper.py
  └── templates/
      └── template.txt
```

```bash
ov skills add ./my-skill
```

## Local SKILL.md File

A single `SKILL.md` file. No auxiliary files are included.

```bash
ov skills add ./my-skill/SKILL.md
```

## Git Repository URL

A URL starting with `https://`, `http://`, `git@`, `ssh://`, or `git://`. The server clones the repository and installs every directory that contains a `SKILL.md`; when that is more than one skill, `ov skills add` asks for confirmation, so use `--list` and `--skill` to pick.

```bash
ov skills add https://github.com/org/skills-repo.git
ov skills add git@github.com:org/skills-repo.git
```

## GitHub Tree URL

Special support for GitHub tree URLs to install individual skills from monorepos.

### Single Skill Path

The URL path ends at a specific skill directory:

```bash
ov skills add https://github.com/anthropics/skills/tree/main/skills/algorithmic-art
```

This installs the `algorithmic-art` skill.

### Skills Collection with Selector

The URL points to a parent directory containing multiple skills. `--skill` matches skill directory names (comma-separated for several, `"*"` for all):

```bash
ov skills add https://github.com/anthropics/skills/tree/main/skills --skill brand-guidelines
```

### Branch Names with Slashes

Branch names containing `/` are supported as long as the skill path is under `skills/...`:

```bash
ov skills add https://github.com/org/repo/tree/feature/new-ui/skills/my-skill
```

### Preview Available Skills

Use `--list` to see what skills are available at a source without installing:

```bash
ov skills add https://github.com/anthropics/skills/tree/main/skills --list
```

## Raw Content

Pass the SKILL.md text itself as the source argument: a source that is neither an existing local path nor a Git URL is sent as SKILL.md content. Put it after `--` so its leading `---` is not parsed as a flag, with any flags before `--`:

```bash
ov skills add -- "$(cat <<'EOF'
---
name: inline-skill
description: Created inline
---
# Inline Skill
Content here.
EOF
)"
```

## Source Metadata

When a skill is installed, its source is recorded in `.source.json` (hidden, not shown in `--files`):

| Source Type | Recorded Fields |
|---|---|
| Git | `type=git`, `source`, `clone_url`, `ref_name`, `subdir`, `skill_name` |
| Local | `type=local`, `source`, `path`, `skill_name` |
| API / raw | `type=api`, `source` (`inline_content` or `temp_upload`), `operation`, `skill_name` |

Only Git metadata lets `ov skills update` refresh a skill automatically. Skills from a local path or raw content need a new source; see "Update Behavior by Source" in `examples/commands.md`.
