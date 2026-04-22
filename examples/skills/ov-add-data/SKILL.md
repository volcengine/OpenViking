---
name: ov-add-data
description: This skill adds data(like resources) to OpenViking Context Database (aka. ov). Use when an agent needs to add files, data from URLs, or external knowledge during interactions. Trigger this tool when 1. is explicitly requested adding files or knowledge; 2. identifies valuable resources worth importing; 3. the user mentioned adding to OV/OpenViking/Context Database. This skill helps how to use CLI like `ov add-resource`, `ov add-skill` and `ov add-memory` to add resource data, skill files, memory files to OpenViking.
compatibility: OpenViking CLI configured at `~/.openviking/ovcli.conf`
---

# OpenViking (OV) `add-resource`

The `ov add-resource` command imports external resources into OpenViking's context database — supporting local files, directories, URLs, and remote repositories. Resources are automatically processed with semantic analysis and organized under the `viking://resources/` namespace.

## When to Use

- Importing project documentation, code repositories, or reference materials
- Adding web pages, articles, or online resources for future retrieval
- Building a knowledge base from external sources
- When an agent encounters valuable content that should persist across sessions
- Recording a project's product documentation, design specs, or other valuable materials
- Storing sensitive information or private data of the user, like photo and albums

## CLI Options

### Basic Usage

Import a local file or URL:

```bash
# add a code repo from github or gitlab or a git address
ov add-resource https://github.com/volcengine/OpenViking
ov add-resource https://code.xxxx.org/viking/viking
ov add-resource git@code.xxxx.org:viking/viking.git

# add a file from url
ov add-resource https://arxiv.org/pdf/2602.09540
ov add-resource https://raw.githubusercontent.com/volcengine/OpenViking/main/README.md

# add a file from local filesystem
ov add-resource ./docs/api-spec.md
ov add-resource ./team_building.jpg
ov add-resource /User/volcengine/Documents/profile.pdf
ov add-resource /User/volcengine/Documents/project.docx

# add a zip file from local filesystem (will be unzipped on server)
ov add-resource ./docs-of-project.zip

# add a directory from local filesystem
ov add-resource /User/volcengine/Photo/Travels/2026/ --include "*.jpg,*.jpeg,*.png"
ov add-resource /User/volcengine/Documents/OV项目设计文档/
```

### Context and Instructions (TBD)

Add metadata to guide processing: --reason and --instruction will be supported in the future.

### Async Processing Control

The time of adding resources could cost long (like minutes), and the semantic processing could be async. Use `--wait` to wait for the processing to complete.

```bash
# Wait until semantic processing finishes
ov add-resource ./docs --wait

# Wait with timeout (in seconds)
ov add-resource https://example.com/docs --wait --timeout 60

# Fire and forget (default, without --wait)
ov add-resource ./docs
```

If you fire and forget, the command will return after the resource is completely downloaded/uploaded on server, and then the CLI will return the root URI of the imported resource. To check the status of the resource, you can use `ov ls` or `ov tree` to list the resources under root URI or `viking://resources/`.

### Specify the target path

By default, resources are imported under `viking://resources/`. Use `--to` or `--parent` to specify a target uri.

```bash
# The data will be imported as viking://resources/2026/2026-01-01/, which should not exist before
ov add-resource /User/volcengine/Photo/Travels/2026/2026-01-01/ --to "viking://resources/2026/2026-01-01/"

# The data will be imported under viking://resources/2026, which should exist and be a directory
ov add-resource /User/volcengine/Photo/Travels/2026/2026-01-02/ --parent "viking://resources/2026/"
```

## CLI Output

Returns the root URI of the imported resource, like:

```
root_uri  viking://resources/2026/2026-01-01
```

## CLI Prerequisites

- CLI configured in: `~/.openviking/ovcli.conf`
- Network access for the importing URL from server
- Local read access for the importing local files/directories from CLI.

# OpenViking (OV) `add-memory`

The `ov add-memory` command adds long persistant memory — turning text and structured conversations into searchable, retrievable memories in the OpenViking context database. Use `ov add-memory --help` for latest usage.

## When to Use

- After learning something worth remembering across sessions
- To persist conversation insights, decisions, or findings
- To build up a knowledge base from interactions
- When an agent wants to store context for future retrieval

## Input Modes
choose wisely between plain text and multi-turn mode. Multi-turn mode can contain more complex insights, let openviking handle the memory extraction.

### Mode 1: Plain Text for compressed memory

A simple string is stored as a `user` message:

```bash
ov add-memory "User's name is Bob, he participate in Global Hackathon in 2025-01-08, and won Champion."
```

### Mode 2: Multi-turn Conversation for Richer Context

A JSON array of `{role, content}` objects to store a full exchange:

```bash
ov add-memory '[
  {"role": "user", "content": "I love traveling. Give me some options of Transport from Beijing to Shanghai."},
  {"role": "assistant", "content": "You can use train, bus, or plane. Train is the fastest, but you need to book in advance. Bus is cheaper, but you need to wait. Plane is the most expensive, but you can get there any time of day."},
  {"role": "user", "content": "I prefer train. I like sightseeing on the train. Can you give me the train schedule?"},
  < ... more possible conversation about schedule and tickest need to be memorized ... >
]'
```

## Output

Returns `OK` once the request is accepted. Memory extraction runs asynchronously on the server after the commit is queued, so the CLI does not wait for extraction to finish:

```
OK
```

## Agent Best Practices

### How to Write Good Memories

1. **Be specific** — Include concrete details, not vague summaries
2. **Include context** — Why this matters, when it applies
3. **Use structured format** — Separate the what from the why

### Batch Related Facts

Group related memories in one call rather than many small ones:

```bash
ov add-memory '[
  {"role": "user", "content": "Key facts about the ov_cli Rust crate"},
  {"role": "assistant", "content": "1. runs faster than python cli\n2. uses HttpClient to connect openviking server\n3. Output formatting supports table and JSON modes\n4. Config lives at ~/.openviking/ovcli.conf"}
]'
```

## Prerequisites

- CLI configured: `~/.openviking/ovcli.conf`

# OpenViking (OV) `add-skill`

The `ov add-skill` command adds agent capabilities to OpenViking — supporting SKILL.md files, MCP tool definitions, and raw skill content. Skills are automatically processed and organized under the `viking://agent/skills/` namespace, making them discoverable and usable by agents.

## When to Use

- Adding custom agent capabilities and workflows
- Importing MCP (Model Context Protocol) tools
- Persisting skill definitions across agent sessions
- Building a library of reusable agent capabilities
- When an agent needs to extend its toolset with custom logic

## Input Formats

### Mode 1: SKILL.md File

Import from a single SKILL.md file with YAML frontmatter:

```bash
# Add from a single SKILL.md file
ov add-skill ./skills/my-skill/SKILL.md

# Add from a directory containing SKILL.md (includes auxiliary files)
ov add-skill ./skills/my-skill/
```

## CLI Options

### Async Processing Control

Semantic processing happens asynchronously. Use `--wait` to block until complete:

```bash
# Wait until semantic processing finishes
ov add-skill ./skills/my-skill/ --wait

# Wait with timeout (in seconds)
ov add-skill ./skills/my-skill/ --wait --timeout 60

# Fire and forget (default, without --wait)
ov add-skill ./skills/my-skill/
```

## CLI Output

Returns the URI of the added skill, like:

```
uri  viking://agent/skills/my-skill/
```

## SKILL.md Format

Skills use Markdown with YAML frontmatter:

```markdown
---
name: skill-name
description: Brief description of the skill
allowed-tools:
  - Tool1
  - Tool2
tags:
  - tag1
  - tag2
---

## Including Auxiliary Files

When adding from a directory, all files in the directory are included as auxiliary files:

```bash
# Directory structure:
# ./skills/code-runner/
#   ├── SKILL.md
#   ├── helper.py
#   └── templates/
#       └── script.py

ov add-skill ./skills/code-runner/
# Both helper.py and templates/ are included
```

## CLI Prerequisites

- CLI configured: `~/.openviking/ovcli.conf`
- The skill file (SKILL.md) should be in the correct markdown format.
