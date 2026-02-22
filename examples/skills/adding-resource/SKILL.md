---
name: ovr
description: Add resources to OpenViking. Trigger when user says "ovr", asks to import files/URLs, or when valuable external knowledge should be saved.
user-invocable: true
command-dispatch: tool
command-tool: exec
command-arg-mode: raw
compatibility: CLI configured at `~/.openviking/ovcli.conf`
---

# OpenViking `add-resource`

The `ov add-resource` command imports external resources — supporting local files, directories, URLs, and remote repositories.

## When to Use

- Importing project documentation, code repositories, or reference materials
- Adding web pages, articles, or online resources for future retrieval

## Usage

```bash
# Import from URL
ov add-resource https://example.com/docs.md

# Import local file
ov add-resource ./docs/api-spec.md

# With context
ov add-resource ./docs --reason "API documentation"
```

## Prerequisites

- CLI configured: `~/.openviking/ovcli.conf`
