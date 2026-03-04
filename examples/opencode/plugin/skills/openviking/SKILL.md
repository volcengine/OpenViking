---
name: openviking
description: "Activate when the user asks about any repository listed in the system prompt under 'OpenViking — Indexed Code Repositories', or when they ask about an external library, framework, or project that may have been indexed. Also activate when the user wants to add, remove, or manage repos. Always search the local codebase first before using this skill."
license: MIT
compatibility: opencode
---

# OpenViking Code Repository Search

**IMPORTANT: All `ov` commands are terminal (shell) commands — run them via the `bash` tool. Execute directly — no pre-checks, no test commands. Handle errors when they occur.**

## How OpenViking Organizes Data

OpenViking stores content in a virtual filesystem under the `viking://` namespace, similar to a local directory tree:

```
viking://
└── resources/
    ├── fastapi/               ← repo A
    │   ├── fastapi/
    │   │   ├── routing.py
    │   │   └── dependencies/
    │   └── tests/
    └── requests/              ← repo B
        ├── requests/
        └── tests/
```

Each directory has AI-generated summaries (`abstract` / `overview`). **The key principle: narrow the URI scope to improve retrieval efficiency.** Instead of searching all repos, lock to a specific repo or subdirectory — this reduces noise and speeds up results significantly.

## Search Commands

Choose the right command based on what you're looking for:

| Command | Use when | Example |
|---------|----------|---------|
| `ov find` | You know the **concept** but not the exact code | "dependency injection", "rate limiting logic" |
| `ov grep` | You know the **exact keyword or symbol** | function name, class name, error string |
| `ov glob` | You want to **enumerate files** by pattern | all `*.py` files, all test files |

```bash
# Semantic search — concept/intent based
ov find "dependency injection" --uri viking://resources/fastapi --limit 10
ov find "how tokens are refreshed" --uri viking://resources/fastapi/fastapi/security
ov find "JWT authentication" --limit 10   # across all repos

# Keyword search — exact match or regex
ov grep "verify_token" --uri viking://resources/fastapi
ov grep "class.*Session" --uri viking://resources/requests/requests

# File enumeration — by name pattern (--uri is required)
ov glob "**/*.py" --uri viking://resources/fastapi
ov glob "**/test_*.py" --uri viking://resources/fastapi/tests
ov glob "**/*.py" --uri viking://resources/   # across all repos
```

**Narrowing scope:** once you identify a relevant directory, pass it as `--uri` to restrict subsequent searches to that subtree — this is faster and more precise than searching the whole repo.

## Read Content

```bash
# Directories: AI-generated summaries
ov abstract viking://resources/fastapi/fastapi/dependencies/   # one-line summary
ov overview viking://resources/fastapi/fastapi/dependencies/   # detailed breakdown

# Files: raw content
ov read viking://resources/fastapi/fastapi/dependencies/utils.py
ov read viking://resources/fastapi/fastapi/dependencies/utils.py --offset 100 --limit 50
```

`abstract` / `overview` only work on directories. `read` only works on files.

## Browse

```bash
ov ls viking://resources/                        # list all indexed repos
ov ls viking://resources/fastapi                 # list repo top-level contents
ov tree viking://resources/fastapi               # full directory tree
```

## Add a Repository

```bash
ov add-resource https://github.com/owner/repo --to viking://resources/ --timeout 300
ov add-resource /path/to/project --to viking://resources/ --timeout 300
```

`--timeout` is required (seconds). Use 300 (5 min) for small repos, increase for larger ones.

After submitting, run `ov observer queue` once and report status to user. Indexing runs in background — do not poll or wait.

| Repo Size | Files | Est. Time |
|-----------|-------|-----------|
| Small | < 100 | 2–5 min |
| Medium | 100–500 | 5–20 min |
| Large | 500+ | 20–60+ min |

## Remove a Repository

```bash
ov rm viking://resources/fastapi --recursive
```

This permanently deletes the repo and all its indexed content. Confirm with the user before running.

## Error Handling

**`command not found: ov`** → Tell user: `pip install openviking --upgrade`. Stop.

**`url is required` / `CLI_CONFIG` error** → Auto-create config and retry:
```bash
mkdir -p ~/.openviking && echo '{"url": "http://localhost:1933"}' > ~/.openviking/ovcli.conf
```

**`CONNECTION_ERROR` / failed to connect:**
- `~/.openviking/ov.conf` **exists** → auto-start server, wait until healthy, retry:
  ```bash
  openviking-server --config ~/.openviking/ov.conf > /tmp/openviking.log 2>&1 &
  for i in $(seq 1 10); do ov health 2>/dev/null && break; sleep 3; done
  ```
- **Does not exist** → Tell user to configure `~/.openviking/ov.conf` first. Stop.

## More Help

For other issues or command details, run:

```bash
ov help
ov <command> --help   # e.g. ov find --help
```
