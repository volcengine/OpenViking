# Common `ov skills` Command Patterns

## Listing Skills

```bash
# List all installed skills
ov skills list

# List in JSON format
ov skills list --json
```

## Finding Skills

```bash
# Semantic search across installed skills
ov skills find "video generation"

# Search and return only abstracts (L0)
ov skills find "video generation" --level 0

# Search with result limit and score threshold
ov skills find "API design" --limit 5 --threshold 0.3
```

## Adding Skills

### From Local Directory

```bash
# Add a skill directory (must contain SKILL.md)
ov skills add ./skills/my-skill

# Add with wait for processing
ov skills add ./skills/my-skill --wait

# List skills available in a directory without installing
ov skills add ./skills --list

# Install specific skills from a directory
ov skills add ./skills --skill my-skill --skill another-skill

# Install all skills from a directory
ov skills add ./skills --skill "*"
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

```bash
ov skills add - <<'EOF'
---
name: my-inline-skill
description: A skill defined inline
---
# My Skill
Skill content here.
EOF
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
```

## Updating Skills

```bash
# Update a specific skill
ov skills update video-generate

# Update multiple skills
ov skills update video-generate image-generate

# Update all updatable skills (interactive)
ov skills update

# Update all without confirmation
ov skills update --yes

# Wait for processing
ov skills update video-generate --wait
```

### Update Behavior by Source

| Source Type | Update Behavior |
|---|---|
| Git | Re-clone and re-process |
| Local path | Re-read from recorded local path |
| API / raw content | Skipped (no external source to pull) |

## Removing Skills

```bash
# Remove a specific skill
ov skills remove video-generate

# Remove without confirmation
ov skills remove video-generate --yes

# Interactive selection (no name provided)
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

# Strict validation (name must match directory name)
ov skills validate ./skills/my-skill --strict
```

### Validation Rules

| Check | Strict Mode | Normal Mode |
|---|---|---|
| `name` missing | Error | Error |
| `description` missing | Error | Error |
| Invalid YAML frontmatter | Error | Error |
| Name doesn't match directory | Error | — |
| Name > 64 chars | Error | Warning |
| Name has illegal chars | Error | Warning |
| Description > 1024 chars | Error | Warning |
| Body > 500 lines | Warning | Warning |
