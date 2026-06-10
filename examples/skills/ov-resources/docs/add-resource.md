# Resource Ingestion (`ov add-resource`)

The `ov add-resource` command imports external resources into OpenViking's context database under `viking://resources/`.

## Supported Sources

| Type | Examples |
|---|---|
| Local file | `./docs/api.md`, `./team_building.jpg`, `/User/volcengine/Documents/profile.pdf` |
| Local directory | `/User/volcengine/Photo/Travels/2026/` |
| ZIP archive | `./docs-of-project.zip` (unzipped on server) |
| URL | `https://example.com/guide.md`, `https://arxiv.org/pdf/2602.09540` |
| Git repo | `https://github.com/volcengine/OpenViking`, `git@code.xxxx.org:viking/viking.git` |

## Basic Usage

```bash
# From URL
ov add-resource https://github.com/volcengine/OpenViking
ov add-resource https://arxiv.org/pdf/2602.09540

# From local file
ov add-resource ./docs/api-spec.md
ov add-resource ./team_building.jpg
ov add-resource /User/volcengine/Documents/project.docx

# From local directory
ov add-resource /User/volcengine/Photo/Travels/2026/ --include "*.jpg,*.jpeg,*.png"

# From ZIP
ov add-resource ./docs-of-project.zip
```

## Target Location

By default resources go under `viking://resources/`. Use `--to` or `--parent` to override:

```bash
# Exact target (must not exist)
ov add-resource ./docs --to "viking://resources/2026/2026-01-01/"

# Place under existing parent directory
ov add-resource ./docs --parent "viking://resources/docs/"

# Auto-create parent if missing
ov add-resource ./docs --parent-auto-create "viking://resources/docs/2026/05/07"
ov add-resource ./docs -p "viking://resources/docs/{calendar:today}"
```

## Async Processing Control

Semantic processing runs asynchronously. Use `--wait` to block until complete:

```bash
# Wait for completion
ov add-resource ./docs --wait

# Wait with timeout
ov add-resource ./docs --wait --timeout 60

# Fire and forget (default)
ov add-resource ./docs
```

When `wait=false`, the CLI returns after upload/parse/finalize (non-Git sources) or after preflight (Git sources). Use the returned `task_id` with `GET /api/v1/tasks/{task_id}` or `ov observer queue` to track progress.

## Scheduled Refresh (`--watch-interval`)

Create a watch task to re-import periodically:

```bash
# Refresh every 60 minutes, bound to a fixed URI
ov add-resource https://github.com/volcengine/OpenViking \
  --to "viking://resources/repos/OpenViking" \
  --watch-interval 60

# Refresh every 30 minutes, bound to imported root_uri
ov add-resource https://example.com/spec.md --watch-interval 30

# Cancel watch for the same target
ov add-resource https://github.com/volcengine/OpenViking \
  --to "viking://resources/repos/OpenViking" \
  --watch-interval 0
```

`--watch-interval` is in minutes. `> 0` creates/updates; `<= 0` cancels. Prefer `--to` for stable long-term watches.

## Filtering Options

```bash
# Include only matching files
ov add-resource ./project --include "*.py,*.md"

# Exclude patterns
ov add-resource ./project --exclude "*.tmp,*.log"

# Ignore specific directory names
ov add-resource ./project --ignore-dirs "node_modules,target,.git"

# Preserve directory structure
ov add-resource ./project --preserve-structure
```

For local directories, scanning respects `.gitignore` with standard Git semantics; `ignore_dirs`, `include`, and `exclude` further refine ingestion.

## CLI Output

Default table format:

```
Note: Resource is being processed in the background.
Use 'ov wait' to wait for completion, or 'ov observer queue' to check status.
status       success
root_uri     viking://resources/01-overview
task_id      uuid-xxx
```

JSON format (`-o json`):

```json
{
  "status": "success",
  "root_uri": "viking://resources/01-overview",
  "task_id": "uuid-xxx"
}
```

With `--wait`, the response includes `queue_status` with `pending`, `processing`, `completed` counts.

## Key Parameters

| Parameter | Description |
|---|---|
| `--to` | Exact target URI (mutually exclusive with `--parent`) |
| `--parent` / `-p` | Parent directory URI |
| `--parent-auto-create` | Auto-create parent if missing |
| `--reason` | Reason for adding (experimental) |
| `--instruction` | Processing instructions (experimental) |
| `--wait` | Block until semantic processing completes |
| `--timeout` | Timeout in seconds when `--wait` is used |
| `--strict` | Use strict mode |
| `--ignore-dirs` | Directory names to ignore (comma-separated) |
| `--include` | File patterns to include (glob) |
| `--exclude` | File patterns to exclude (glob) |
| `--watch-interval` | Scheduled refresh interval in minutes |

## Important Notes

- `path` and `temp_file_id` are mutually exclusive.
- `to` and `parent` are mutually exclusive.
- When `to` points to an existing resource, the call triggers an incremental update.
- For Git repos with `wait=false`, OpenViking validates the repo, resolves the target URI, reserves `root_uri`, and returns immediately; clone/parse/finalize continues in background.
- To create or update plain text directly, use `ov write` instead of `add_resource`.
