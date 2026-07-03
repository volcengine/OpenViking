# Snapshots (Multi-Version Management)

On top of VikingFS, OpenViking provides Git-based multi-version management, called **Snapshots**. It saves an account's entire resource tree as a series of immutable commits, letting you walk history, compare versions, and restore the workspace to any past state.

Snapshots are powered by [gitoxide](https://github.com/Byron/gitoxide) embedded in the Rust RAGFS layer, maintaining one logical Git repository per `account_id`. This is fully transparent to callers — you never touch a `.ovgit` directory, the object store, or ref internals.

The four core commands:

| Command | Purpose |
|---------|---------|
| `commit` | Save the current workspace state as a new snapshot |
| `log` | Walk commit history starting from the newest |
| `show` | View a commit's metadata, or read a file's content from that commit |
| `restore` | Restore a directory (or the whole account tree) to a past snapshot |

In addition, account-level `.ovgitignore` exclusion rules can be managed (`get`/`set`/`delete`) to exclude matching files from `commit`. See [Ignore management](#ignore-management).

## Core Concepts

- **Commit**: A snapshot is a commit, uniquely identified by a 40-hex SHA-1 `commit_oid`. Most commands also accept an abbreviated OID prefix or a branch name (e.g. `main`).
- **Branch**: The default branch is `main`. Unless you pass one explicitly, every command operates on `main`.
- **Forward-commit restore**: `restore` does **not** rewind or rewrite history. It reads the content at `source_commit`, writes the diff back into the workspace, and creates a **new commit on top of the current HEAD**. The new commit's parent is therefore the HEAD that existed before the restore — **not** `source_commit`. HEAD always advances monotonically and history is never lost.
- **Scope**: `commit` can be limited to specific URIs via `paths`; `restore` can be limited to a subtree via `project_dir`, leaving files outside it untouched.

## Implementation

- HTTP routes: [snapshot.py](file:///cloudide/workspace/OpenViking/openviking/server/routers/snapshot.py), prefix `/api/v1/snapshot`.
- SDK namespace: [snapshot_namespace.py](file:///cloudide/workspace/OpenViking/openviking/snapshot_namespace.py), exposed as `client.snapshot.*`.
- Underlying semantics: `commit` / `restore` / `show` / `log` in [viking_fs.py](file:///cloudide/workspace/OpenViking/openviking/storage/viking_fs.py).
- CLI: the `SnapshotCmd` in [main.rs](file:///cloudide/workspace/OpenViking/crates/ov_cli/src/main.rs), subcommands in [snapshot.rs](file:///cloudide/workspace/OpenViking/crates/ov_cli/src/commands/snapshot.rs).

## API Reference

### commit()

Save the current workspace state as a new snapshot.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| message | str | Yes | - | Commit message |
| paths | List[str] | No | null | `viking://` URIs to scope the snapshot to; entries may be files or directories. Directories are expanded recursively with the snapshot pruning rules applied. `null` snapshots the whole account tree. An empty list `[]` is forwarded as an explicit empty path set (no-op). A path that exists in neither the VFS nor the previous snapshot logs a warning and is treated as a no-op deletion |
| branch | str | No | `main` | Branch to advance |
| author_name | str | No | null | Override the default author name (default `viking-bot`) |
| author_email | str | No | null | Override the default author email |

**Python SDK (Embedded / HTTP)**

```python
result = client.snapshot.commit(
    message="v1 initial import",
    paths=["viking://resources/my_md.md"],
)
print(result["commit_oid"])
```

**HTTP API**

```
POST /api/v1/snapshot/commit
```

```bash
curl -X POST "http://localhost:1933/api/v1/snapshot/commit" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "message": "v1 initial import",
    "paths": ["viking://resources/my_md.md"]
  }'
```

**CLI**

```bash
ov snapshot commit -m "v1 initial import" --paths viking://resources/my_md.md -o json
```

**Response**

When a new snapshot is created:

```json
{
  "status": "ok",
  "result": {
    "result": "created",
    "commit_oid": "3f2a1b9c4d5e6f70819293a4b5c6d7e8f9a0b1c2",
    "changed": 3,
    "ignored": 1
  }
}
```

`changed` is the number of paths added/updated/removed in this commit; `ignored` is the number of candidate paths skipped by the account `.ovgitignore` rules (built-in system pruning is not counted). When the workspace is unchanged relative to the last commit, the result is `noop` and `commit_oid` is the current HEAD (`noop` also returns `ignored` but has no `changed`):

```json
{
  "status": "ok",
  "result": {
    "result": "noop",
    "commit_oid": "3f2a1b9c4d5e6f70819293a4b5c6d7e8f9a0b1c2",
    "ignored": 0
  }
}
```

---

### log()

Starting from a branch's HEAD, walk history along the first parent (`parents[0]`) and return commits newest-first.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| branch | str | No | `main` | Branch to walk |
| limit | int | No | 20 | Max commits to return. The HTTP endpoint clamps this to 1–500 |

**Python SDK (Embedded / HTTP)**

```python
history = client.snapshot.log(limit=10)
for commit in history:
    print(commit["oid"], commit["message"])
```

**HTTP API**

```
GET /api/v1/snapshot/log?branch={branch}&limit={limit}
```

```bash
curl -X GET "http://localhost:1933/api/v1/snapshot/log?branch=main&limit=10" \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
ov snapshot log --limit 10 -o json
```

**Response**

`result` is a list of commit metadata, each element having the same shape as the metadata returned by [show()](#show):

```json
{
  "status": "ok",
  "result": [
    {
      "oid": "9a0b1c2d3e4f5061728394a5b6c7d8e9f0a1b2c3",
      "tree": "11223344556677889900aabbccddeeff00112233",
      "parents": ["3f2a1b9c4d5e6f70819293a4b5c6d7e8f9a0b1c2"],
      "author": {
        "name": "viking-bot",
        "email": "bot@openviking.local",
        "time_seconds": 1750300000,
        "tz_offset_seconds": 28800
      },
      "committer": {
        "name": "viking-bot",
        "email": "bot@openviking.local",
        "time_seconds": 1750300000,
        "tz_offset_seconds": 28800
      },
      "message": "v2 modify delete add"
    }
  ]
}
```

> When the branch has no commits yet, the HTTP endpoint returns `404 NOT_FOUND`.

---

### show()

View a commit's metadata; if `path` is given, return that file's content from the commit instead.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| target_ref | str | Yes | - | Commit OID (abbreviated prefix allowed), branch name, or tag |
| path | str | No | null | `viking://` URI of a single file; omit to return commit metadata |

**Python SDK (Embedded / HTTP)**

```python
# View commit metadata
meta = client.snapshot.show("3f2a1b9c")
print(meta["message"], meta["parents"])

# Read a file's content from the commit
blob = client.snapshot.show("3f2a1b9c", path="viking://resources/my_project/guide.md")
```

> Note: when reading a file (`path` given), the **Embedded (local) client** returns raw `bytes`, while the **HTTP client** returns a `{"oid": str, "size": int, "bytes": bytes}` dict.

**HTTP API**

```
GET /api/v1/snapshot/show?target_ref={ref}[&path={uri}]
```

```bash
# Commit metadata (returns JSON)
curl -X GET "http://localhost:1933/api/v1/snapshot/show?target_ref=3f2a1b9c" \
  -H "X-API-Key: your-key"

# File content (returns a binary stream)
curl -X GET "http://localhost:1933/api/v1/snapshot/show?target_ref=3f2a1b9c&path=viking://resources/my_project/guide.md" \
  -H "X-API-Key: your-key"
```

Without `path`, the response is commit metadata JSON. With `path`, the response is a raw byte stream (`Content-Type: application/octet-stream`) plus two headers:

- `X-Snapshot-Oid`: the blob object's OID
- `X-Snapshot-Size`: the blob size in bytes

**CLI**

```bash
# Commit metadata
ov snapshot show 3f2a1b9c -o json

# Read file content (defaults to stdout; use --out-file to write to a local file)
ov snapshot show 3f2a1b9c --path viking://resources/my_project/guide.md --out-file ./guide.md
```

**Response (commit metadata)**

```json
{
  "status": "ok",
  "result": {
    "oid": "3f2a1b9c4d5e6f70819293a4b5c6d7e8f9a0b1c2",
    "tree": "00112233445566778899aabbccddeeff00112233",
    "parents": [],
    "author": {
      "name": "viking-bot",
      "email": "bot@openviking.local",
      "time_seconds": 1750299000,
      "tz_offset_seconds": 28800
    },
    "committer": {
      "name": "viking-bot",
      "email": "bot@openviking.local",
      "time_seconds": 1750299000,
      "tz_offset_seconds": 28800
    },
    "message": "v1 initial import"
  }
}
```

---

### restore()

Restore a directory (or the whole account tree) to its state at `source_commit`.

This is a **forward-commit restore**: it computes the diff between `source_commit` and the current HEAD, writes it back into the workspace, and creates a **new commit on top of the current HEAD**. The new commit's parent is the pre-restore HEAD (not `source_commit`), so history is never rewritten. Files outside `project_dir` are left untouched.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| source_commit | str | Yes | - | What to restore from: commit OID (abbreviated prefix allowed), branch name, or tag |
| project_dir | str | No | null | `viking://` URI of the subtree to restore; omit to restore the whole account tree |
| branch | str | No | `main` | Branch to advance |
| dry_run | bool | No | false | Compute and return the diff only; write nothing |
| message | str | No | null | Message for the new commit; auto-generated when omitted |
| author_name | str | No | null | Override the default author name |
| author_email | str | No | null | Override the default author email |

**Python SDK (Embedded / HTTP)**

```python
result = client.snapshot.restore(
    project_dir="viking://resources/my_project",
    source_commit="3f2a1b9c",
    message="restore to v1",
)
print(result["result"], result["new_commit_oid"])

# Preview which files would change first
plan = client.snapshot.restore(
    project_dir="viking://resources/my_project",
    source_commit="3f2a1b9c",
    dry_run=True,
)
print(plan["diff"])
```

**HTTP API**

```
POST /api/v1/snapshot/restore
```

```bash
curl -X POST "http://localhost:1933/api/v1/snapshot/restore" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "project_dir": "viking://resources/my_project",
    "source_commit": "3f2a1b9c",
    "message": "restore to v1"
  }'
```

**CLI**

```bash
# Positional args are <source_commit> then <project_dir>
ov snapshot restore 3f2a1b9c viking://resources/my_project -m "restore to v1" -o json

# Dry run
ov snapshot restore 3f2a1b9c viking://resources/my_project --dry-run -o json
```

**Response (applied)**

On a successful write that produces a new commit, `result` is `applied`. Note `parent_commit` equals the old (pre-restore) HEAD, confirming the forward-commit semantics:

```json
{
  "status": "ok",
  "result": {
    "result": "applied",
    "new_commit_oid": "c3d4e5f60718293a4b5c6d7e8f9a0b1c2d3e4f50",
    "source_commit": "3f2a1b9c4d5e6f70819293a4b5c6d7e8f9a0b1c2",
    "parent_commit": "9a0b1c2d3e4f5061728394a5b6c7d8e9f0a1b2c3",
    "written": 1,
    "deleted": 1,
    "unchanged": 1,
    "written_paths": ["resources/my_project/guide.md"],
    "deleted_paths": ["resources/my_project/changelog.md"],
    "task_id": "snapshot_restore_reindex-..."
  }
}
```

When the restore has vector side effects (files written/deleted), the response carries a `task_id` you can poll via `GET /api/v1/tasks/{task_id}` to track the background vector rebuild.

**Response (noop)**

When the source is byte-identical to the current state, the result is `noop` and no new commit is created:

```json
{
  "status": "ok",
  "result": {
    "result": "noop",
    "head": "9a0b1c2d3e4f5061728394a5b6c7d8e9f0a1b2c3",
    "source": "3f2a1b9c4d5e6f70819293a4b5c6d7e8f9a0b1c2"
  }
}
```

**Response (dry_run)**

With `dry_run=true`, only the planned diff is returned and nothing is written. Diff paths are relative to `project_dir`:

```json
{
  "status": "ok",
  "result": {
    "result": "dry_run",
    "head": "9a0b1c2d3e4f5061728394a5b6c7d8e9f0a1b2c3",
    "source": "3f2a1b9c4d5e6f70819293a4b5c6d7e8f9a0b1c2",
    "diff": {
      "to_write": [{"path": "guide.md", "oid": "..."}],
      "to_delete": ["changelog.md"],
      "unchanged": ["notes/todo.md"]
    }
  }
}
```

---

## Ignore management

The `.ovgitignore` file at the account root is an account-level exclusion file. At `commit` time, files matching the rules are excluded from the snapshot; the rules file itself is never ignored by `.ovgitignore` rules (a rule matching `.ovgitignore` does not exclude it) and never enters vector indexing. Rules affect only `commit`, not `restore`/`show`/`log`.

The syntax is a common glob subset: blank lines are ignored, `#`-prefixed lines are comments, leading/trailing whitespace is trimmed; `!` negation and backslash escaping are **unsupported**; the file is capped at 64 KiB (validated on write). Matching uses account-relative Git tree paths (`/`-separated).

Three methods are provided: `get_gitignore` (read, empty string when absent), `set_gitignore` (write), and `delete_gitignore` (delete, missing is success and idempotent). All three only need the account from the request context and take no path argument.

### get_gitignore()

Reads the account `.ovgitignore` content; returns an empty string when the file is absent.

**Python SDK (Embedded / HTTP)**

```python
content = client.snapshot.get_gitignore()
```

**HTTP API**

```
GET /api/v1/snapshot/ignore
```

```bash
curl -X GET "http://localhost:1933/api/v1/snapshot/ignore" \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
ov snapshot ignore-get -o json
```

**Response**

```json
{
  "status": "ok",
  "result": "*.log\n"
}
```

> Without `-o json`, the CLI prints the raw content to stdout (so it can be redirected to a file).

### set_gitignore()

Writes the account `.ovgitignore` content (overwrites). The size limit (64 KiB) is validated up front; syntax (negation, escaping) is validated at `commit` time by the Rust layer.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| content | str | Yes | - | The `.ovgitignore` content (UTF-8) |

**Python SDK (Embedded / HTTP)**

```python
client.snapshot.set_gitignore(content="*.log\n")
```

**HTTP API**

```
PUT /api/v1/snapshot/ignore
```

```bash
curl -X PUT "http://localhost:1933/api/v1/snapshot/ignore" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"content": "*.log\n"}'
```

**CLI**

```bash
# Pass content inline with --content, or read from a file with --file
ov snapshot ignore-set --content "*.log" -o json
ov snapshot ignore-set --file ./my-rules -o json
```

**Response**

```json
{
  "status": "ok",
  "result": null
}
```

### delete_gitignore()

Deletes the account `.ovgitignore`. Missing is success (idempotent).

**Python SDK (Embedded / HTTP)**

```python
client.snapshot.delete_gitignore()
```

**HTTP API**

```
DELETE /api/v1/snapshot/ignore
```

```bash
curl -X DELETE "http://localhost:1933/api/v1/snapshot/ignore" \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
ov snapshot ignore-delete -o json
```

**Response**

```json
{
  "status": "ok",
  "result": null
}
```

## A Typical Flow

A complete "commit → modify → restore" flow (Python SDK):

```python
import openviking as ov

client = ov.OpenViking()
client.initialize()

root = "viking://resources/my_project"

# 1. Write initial content and commit v1
client.write(f"{root}/guide.md", "# Guide\n\nv1 content\n", mode="create", wait=True)
v1 = client.snapshot.commit(message="v1 initial import")

# 2. Modify and commit v2
client.write(f"{root}/guide.md", "# Guide\n\nv2 content\n", mode="replace", wait=True)
v2 = client.snapshot.commit(message="v2 update")

# 3. Walk history
for c in client.snapshot.log(limit=10):
    print(c["oid"][:8], c["message"])

# 4. Restore the workspace to v1 (creates a new commit on top of v2)
client.snapshot.restore(project_dir=root, source_commit=v1["commit_oid"], message="restore to v1")

client.close()
```

For more end-to-end examples, see the [examples/snapshot/](file:///cloudide/workspace/OpenViking/examples/snapshot) directory in the repository, covering the SDK, HTTP, and CLI surfaces.

## Error Handling

| Scenario | HTTP Status | Error Code |
|----------|-------------|------------|
| Branch/commit not found, or `show`'s `path` does not exist in that commit | 404 | `NOT_FOUND` |
| Branch concurrently advanced during restore (CAS conflict) | 409 | `CONFLICT` |
| `.ovgitignore` too large, non-UTF-8, or containing unsupported `!` negation/backslash escaping (validated at `commit` time) | 400 | `INVALID_ARGUMENT` |
| Request body contains an unknown field (request model is `extra="forbid"`) | 400 | `INVALID_ARGUMENT` |

## Related Documentation

- [File System](03-filesystem.md): snapshots build on filesystem resources
- [System](07-system.md): track the background vector rebuild triggered by restore via `GET /api/v1/tasks/{task_id}`
- [API Overview](01-overview.md): full endpoint reference
