# Common `ov skills` Command Patterns

`list`, `find`, and `show` take `-p`/`--uri <root>`; `add`, `update`, and `remove` take `-p`/`--parent-auto-create <root>`. The root is `viking://~/skills` (private) or `viking://agent/skills` (shared with the account). Every command accepts the global `-o json` for JSON output.

## Listing Skills

```bash
# List all installed skills (private and shared roots)
ov skills list

# List only the skills shared with the account
ov skills list -p viking://agent/skills

# Cap the number of entries (default 1000)
ov skills list -n 50

# List in JSON format
ov skills list -o json
```

## Finding Skills

```bash
# Semantic search across installed skills
ov skills find "video generation"

# Search and return only abstracts (L0)
ov skills find "video generation" --level 0

# Search with result limit (default 10) and score threshold
ov skills find "API design" -n 5 --threshold 0.3

# Search only your private skills
ov skills find "API design" -p viking://~/skills
```

## Adding Skills

### From Local Directory

```bash
# Add a skill directory (must contain SKILL.md)
ov skills add ./skills/my-skill

# Add with wait for processing
ov skills add ./skills/my-skill --wait

# Share the skill with the account instead of keeping it private
ov skills add ./skills/my-skill -p viking://agent/skills

# List skills available in a directory without installing
ov skills add ./skills --list

# Install specific skills from a directory (--skill matches skill directory names, shown as `path` by --list)
ov skills add ./skills --skill my-skill,another-skill --yes

# Install all skills from a directory
ov skills add ./skills --skill "*" --yes
```

### From Local SKILL.md File

```bash
ov skills add ./skills/my-skill/SKILL.md
```

### From Git Repository

```bash
ov skills add https://github.com/org/repo.git
```

### From GitHub Tree URL

```bash
# Single skill from GitHub tree
ov skills add https://github.com/anthropics/skills/tree/main/skills/algorithmic-art

# Specific skill from a skills collection
ov skills add https://github.com/anthropics/skills/tree/main/skills --skill brand-guidelines

# List available skills in a GitHub tree without installing
ov skills add https://github.com/anthropics/skills/tree/main/skills --list
```

### From Raw Content

Pass the SKILL.md text itself as the source, after `--` so its leading `---` is not parsed as a flag. Put any flags before `--`.

```bash
ov skills add --wait -- "$(cat <<'MD'
---
name: my-inline-skill
description: A skill defined inline
---
# My Skill
Skill content here.
MD
)"
```

## Showing Skills

```bash
# Show full skill content (default: L0 + L1 + L2)
ov skills show video-generate

# Show only abstract (L0)
ov skills show video-generate --level 0

# Show only overview (L1)
ov skills show video-generate --level 1

# Show only full SKILL.md (L2)
ov skills show video-generate --level 2

# Show auxiliary files
ov skills show video-generate --files

# Show source information
ov skills show video-generate --source

# Combine flags
ov skills show video-generate --files --source

# Show the shared copy as JSON
ov skills show video-generate -p viking://agent/skills --format json
```

## Updating Skills

```bash
# Update a specific skill (asks for confirmation)
ov skills update video-generate

# Update multiple skills without confirmation
ov skills update video-generate image-generate --yes

# Update all installed skills (asks for confirmation)
ov skills update

# Update all without confirmation
ov skills update --yes

# Wait for processing
ov skills update video-generate --wait --yes

# Update the shared copy
ov skills update video-generate -p viking://agent/skills --yes
```

### Update Behavior by Source

| Source Type | Update Behavior |
|---|---|
| Git | Re-clone the recorded repository and re-process |
| Local path / API / raw content | Not refreshed from a recorded source. A named update prompts for a local path or Git URL in an interactive terminal and fails without one; `ov skills update` without names reports these skills under `skipped` |

## Removing Skills

```bash
# Remove a specific skill (asks for confirmation)
ov skills remove video-generate

# Remove without confirmation
ov skills remove video-generate --yes

# Remove the shared copy
ov skills remove video-generate -p viking://agent/skills --yes

# Interactive selection (no name provided; needs a terminal)
ov skills remove

# Remove all skills (destructive — confirm first)
ov skills remove --all
```

## Validating Skills

```bash
# Validate a skill directory
ov skills validate ./skills/my-skill

# Validate a single SKILL.md file
ov skills validate ./skills/my-skill/SKILL.md

# Strict validation (warnings such as a name/directory mismatch become errors)
ov skills validate ./skills/my-skill --strict
```

### Validation Rules

| Check | Strict Mode | Normal Mode |
|---|---|---|
| `name` missing | Error | Error |
| `description` missing | Error | Error |
| Invalid YAML frontmatter | Error | Error |
| Name doesn't match directory | Error | Warning |
| Name > 64 chars | Error | Warning |
| Name has illegal chars | Error | Warning |
| Description > 1024 chars | Error | Warning |
| Body > 500 lines | Warning | — |
