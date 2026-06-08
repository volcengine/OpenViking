# Supported Skill Source Types

## Local Directory

A directory containing a `SKILL.md` file at its root. All files in the directory become auxiliary files.

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

Any valid Git URL. The repository is cloned and processed.

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

The URL points to a parent directory containing multiple skills:

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

Pipe SKILL.md content directly via stdin:

```bash
ov skills add - <<'EOF'
---
name: inline-skill
description: Created inline
---
# Inline Skill
Content here.
EOF
```

## Source Metadata

When a skill is installed, its source is recorded in `.source.json` (hidden, not shown in `--files`):

| Source Type | Recorded Fields |
|---|---|
| Git | `type=git`, `source`, `clone_url`, `ref_name`, `subdir`, `skill_name` |
| Local | `type=local`, path, `skill_name` |
| API / raw | `type=api`, operation, upload method, `skill_name` |

This metadata enables `ov skills update` to automatically refresh from the original source.
