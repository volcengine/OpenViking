# Common Resource Management Command Patterns

## Adding Resources

```bash
# Local file
ov add-resource ./docs/api.md

# Local directory with filters
ov add-resource ./project --include "*.py,*.md" --ignore-dirs "node_modules"

# URL
ov add-resource https://example.com/guide.md

# Git repository with watch
ov add-resource https://github.com/volcengine/OpenViking \
  --to "viking://resources/repos/OpenViking" \
  --watch-interval 60

# To specific location with wait
ov add-resource ./docs --to "viking://resources/docs/" --wait

# Auto-create parent path
ov add-resource ./guide.md -p "viking://resources/docs/{calendar:today}"
```

## Browsing Resources

```bash
# List top-level resources
ov ls viking://resources/

# Recursive listing
ov ls viking://resources/my-project/ --recursive

# Tree view (limited depth)
ov tree viking://resources/my-project/ --level-limit 3

# Simple path list
ov ls viking://resources/ --simple

# File stats
ov stat viking://resources/docs/api.md
```

## Reading Content

```bash
# Full content
ov read viking://resources/docs/api.md

# With line range
ov read viking://resources/docs/api.md --offset 10 --limit 20

# Abstract (directory)
ov abstract viking://resources/docs/

# Overview (directory)
ov overview viking://resources/docs/
```

## Writing Content

```bash
# Replace existing file
ov write viking://resources/docs/api.md \
  --content "# Updated\n\nNew content." \
  --wait

# Create new file
ov write viking://resources/docs/new.md \
  --content "# New doc" \
  --mode create

# Append to existing
ov write viking://resources/docs/notes.md \
  --content "\nNew line." \
  --mode append
```

## Directory Management

```bash
# Create directory
ov mkdir viking://resources/new-project/

# Create with description
ov mkdir viking://resources/new-project/ --description "Project docs"

# Move
ov mv viking://resources/old-name/ viking://resources/new-name/

# Remove file
ov rm viking://resources/docs/old.md

# Remove directory recursively
ov rm viking://resources/old-project/ --recursive
```

## Searching Resources

```bash
# Grep content
ov grep "TODO" --uri viking://resources/ --ignore-case

# Glob files
ov glob "**/*.md" --uri viking://resources/
ov glob "**/*.py" --uri viking://resources/
```

## Watch Task Management

```bash
# List active watches
ov task watch ls --active-only

# Inspect a watch
ov task watch show viking://resources/guide.md

# Pause / resume
ov task watch pause viking://resources/guide.md
ov task watch resume viking://resources/guide.md

# Update interval
ov task watch update viking://resources/guide.md --interval 30

# Trigger immediate refresh
ov task watch trigger viking://resources/guide.md

# Remove watch
ov task watch rm viking://resources/guide.md

# Cancel via add-resource
ov add-resource https://example.com/guide.md \
  --to "viking://resources/guide.md" \
  --watch-interval 0
```

## OVPack Operations

```bash
# Export
ov export viking://resources/my-project/ ./backups/my-project.ovpack

# Import
ov import ./backups/my-project.ovpack viking://resources/imported/

# Backup all public scopes
ov backup ./backups/openviking.ovpack

# Restore from backup
ov restore ./backups/openviking.ovpack --on-conflict overwrite
```

## Searching Resources

```bash
# Semantic find across all context
ov find "how to handle API rate limits"

# Find within specific scope
ov find "authentication flow" --uri "viking://resources/my-project"

# Find with time filter
ov find "invoice" --after 7d --time-field created_at

# Find L0 abstracts only
ov find "overview" --level 0

# Context-aware search
ov search "best practices" --session-id abc123

# Search with intent analysis
ov search "how to implement OAuth 2.0"

# Combined: search then read
ov find "authentication" --uri "viking://resources/project-A"
ov overview viking://resources/project-A/backend
ov read viking://resources/project-A/backend/auth.md
```

## Relations

```bash
# Link resources
ov link viking://resources/docs/auth/ viking://resources/docs/security/ \
  --reason "Security best practices"

# List relations
ov relations viking://resources/docs/auth/

# Unlink
ov unlink viking://resources/docs/auth/ viking://resources/docs/security/
```
