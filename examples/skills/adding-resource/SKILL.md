---
name: ovr
description: Add resources to OpenViking. Trigger when user says "ovr", asks to import files/URLs, or when valuable external knowledge should be saved.
user-invocable: true
command-dispatch: tool
command-tool: exec
command-arg-mode: raw
compatibility: CLI configured at `~/.openviking/ovcli.conf`
---

# OpenViking (`/ovr`) — Add Resource

The `ov add-resource` command (or `/ovr` slash command) imports external resources into OpenViking's context database — supporting local files, directories, URLs, and remote repositories.

## When to Use

- Importing project documentation, code repositories, or reference materials
- Adding web pages, articles, or online resources for future retrieval
- Building a knowledge base from external sources
- When an agent encounters valuable content that should persist across sessions

## Usage

### As a slash command:
```
/ovr https://raw.githubusercontent.com/volcengine/OpenViking/main/README.md
```

### Via model/tool invocation:
```bash
ov add-resource https://raw.githubusercontent.com/volcengine/OpenViking/main/README.md
```

## Input Options

### Basic Usage

Import a local file or URL:

```bash
# Import from URL
ov add-resource https://raw.githubusercontent.com/volcengine/OpenViking/main/README.md

# Import local file
ov add-resource ./docs/api-spec.md

# Import local directory
ov add-resource ./project-source
```

### Context and Instructions

Add metadata to guide processing:

```bash
# Provide reason for import
ov add-resource ./api-docs.md --reason "REST API documentation for v2 endpoints"

# Add processing instructions
ov add-resource ./large-repo --instruction "Focus on authentication patterns"

# Combine reason and instruction
ov add-resource https://example.com/guide --reason "User guide" --instruction "Extract best practices"
```

### Async Processing Control

Wait for semantic processing to complete:

```bash
# Wait until processing finishes
ov add-resource ./docs --wait

# Wait with timeout (in seconds)
ov add-resource https://example.com/docs --wait --timeout 60

# Fire and forget (default)
ov add-resource ./docs
```

## Output

Returns the root URI of the imported resource:

```
root_uri  viking://resources/...
```

## Prerequisites

- CLI configured: `~/.openviking/ovcli.conf`
- Network access for URL imports
- Read permissions for local files/directories
