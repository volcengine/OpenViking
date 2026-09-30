# Snapshots (Multi-Version Management) Guide

Snapshots save immutable versions of a selected file tree. Use `commit` to save a version, `log` to browse history, `show` to read an older file, and `restore` to recover saved content. Uncommitted or excluded files are outside the recovery scope; ACLs and vector indexes are not versioned.

Multi-version management is powered by [gitoxide](https://github.com/Byron/gitoxide) embedded in the Rust RAGFS layer, maintaining one logical Git repository per `account_id`. It is fully transparent to callers — you never run any `git` command yourself.

> For the full API reference of each command's parameters and responses, see [Snapshots API](../api/11-snapshot.md).

## When to commit

Ordinary `write` and `rm` operations change the live workspace directly. Enabling snapshots does not automatically create a version after every write. A prior file version is recoverable through snapshots only if a successful snapshot commit covered that path. Commit after an import, before and after a batch change, or at a business checkpoint, and retain the returned `commit_oid`.

Snapshots version the file tree. They do not roll back current ACLs or preserve historical vector indexes. After restoring files, `restore` rebuilds indexes asynchronously when needed. It changes the workspace; inspect its plan with `dry_run` first.

`client.snapshot.commit()` saves file versions, while a session's `commit()` archives conversation and processes memory. Some memory workflows call snapshot after updating experiences; this does not make snapshots automatic for every write.

## Commit scope and concurrency

USER / ADMIN callers must supply `paths` for `commit` and `log`, `project_dir` for `restore`, and a file `path` for `show`. Only local ROOT mode may omit those scope parameters. Committing a project directory with write access keeps unrelated resources out of the change.

| `commit(paths=...)` input | Meaning |
| --- | --- |
| Existing file URI | Process that file |
| Existing directory URI | Recursively process current files and record deletions of previously snapshotted files in that subtree |
| Missing URI | Record deletion of that path and its subtree from the previous snapshot; no change if neither existed there |
| `[]` | Explicit empty scope; no changes |
| `None` / omitted | Entire account tree; ROOT only |

A partial commit starts from the branch's previous snapshot and preserves its versions outside the requested scope. After deletion, include the deleted URI or a covering parent directory in the next commit. Dropping the URI from `paths` would omit the deletion. A trailing `/` does not declare a file/directory type, and the current API has no explicit per-target type parameter.

For non-ROOT explicit-path commits, locks are selected from current target state before scope authorization and snapshot creation:

| Target state | Filesystem PathLock | Cache (Redis) PathLock |
| --- | --- | --- |
| Existing file | Exact | Exact |
| Existing directory | Tree | Tree |
| Missing path | Skip locking this target | Tree |

Filesystem skips missing targets to avoid creating a target directory or a missing parent chain just to store a lock token. The target still participates in snapshot deletion processing. A concurrent recreation may be missed or read before its write finishes; a later commit records the final state. ROOT bypasses this explicit-path locking flow.

Snapshots are not a globally atomic view of arbitrary concurrent I/O. For a deterministic checkpoint, finish writes in the intended scope before committing. Locks coordinate only operations participating in the [PathLock protocol](../concepts/09-transaction.md).

## Prerequisites

- You already have a working `ov.conf`.
- Resource read/write is verified to work (snapshots build on filesystem resources).
- If you choose the S3 backend for Git objects, prepare the bucket, region, endpoint, and access credentials first.

## Enabling Multi-Version Management

Multi-version management is **enabled** by default (`git.enabled` defaults to `true`). The Git object backend can be `local` (local filesystem) or `s3` (S3-compatible object storage); when `git.backend` is not set explicitly, it **inherits `storage.agfs.backend`** (a `memory` storage backend maps to `local`). To turn multi-version management off, set `git.enabled` to `false`.

### Local Backend (recommended for single-node deployments)

```json
{
  "storage": {
    "workspace": "./data"
  },
  "git": {
    "enabled": true,
    "backend": "local",
    "default_branch": "main",
    "author_name": "viking-bot",
    "author_email": "bot@viking.local",
    "local": {
      "base_dir": ""
    }
  }
}
```

Configuration reference:

| Field | Default | Description |
|-------|---------|-------------|
| `git.enabled` | `true` | Whether multi-version management is on. Set to `false` to disable snapshot commands |
| `git.backend` | inherits `storage.agfs.backend` | Git object backend: `local` or `s3`. When not set explicitly, inherits `storage.agfs.backend` (`memory` maps to `local`) |
| `git.default_branch` | `main` | Default branch name when none is specified |
| `git.author_name` | `viking-bot` | Default author name when callers omit `author_name` |
| `git.author_email` | `bot@viking.local` | Default author email |
| `git.local.base_dir` | `""` | Directory holding Git objects/refs. **When empty, defaults to `{storage.workspace}/.ovgit`** |

> Usually leave `git.local.base_dir` empty so snapshot data lands in `.ovgit` under the workspace, making it easy to back up and migrate alongside resource data.

### S3 Backend (recommended for distributed / cloud deployments)

Stores Git objects and refs in S3-compatible object storage (e.g. Volcengine TOS, MinIO, AWS S3). When `backend` is `s3`, the `git.s3` section is **required**, and `bucket` and `region` must not be empty.

> Tip: the `git.s3` fields `bucket`, `region`, `endpoint`, `access_key`, and `secret_key` **inherit the matching `storage.agfs.s3`** values when not set explicitly. So when `storage.agfs` is already configured as an s3 backend, you usually don't need to repeat `git.s3` — as long as `git.backend` is not set explicitly, multi-version management reuses the bucket and credentials from `storage.agfs`.

```json
{
  "storage": {
    "workspace": "./data"
  },
  "git": {
    "enabled": true,
    "backend": "s3",
    "default_branch": "main",
    "author_name": "viking-bot",
    "author_email": "bot@viking.local",
    "s3": {
      "bucket": "your-tos-bucket",
      "region": "cn-beijing",
      "endpoint": "https://tos-s3-cn-beijing.volces.com",
      "access_key": "<your-volcengine-ak>",
      "secret_key": "<your-volcengine-sk>",
      "prefix": ".ovgit",
      "use_path_style": false,
      "cas_mode": "native"
    }
  }
}
```

Configuration reference:

| Field | Default | Description |
|-------|---------|-------------|
| `git.s3.bucket` | inherits `storage.agfs.s3.bucket` | Bucket holding Git objects/refs. Required (may be inherited from `storage.agfs.s3`) |
| `git.s3.region` | inherits `storage.agfs.s3.region`, else `us-east-1` | Region the bucket is in. Required |
| `git.s3.prefix` | `.ovgit` | Key prefix; all data is stored under `{prefix}/{account}/...` |
| `git.s3.endpoint` | inherits `storage.agfs.s3.endpoint`, else `""` | Custom S3 endpoint (MinIO/TOS, etc.); leave empty for standard AWS S3 |
| `git.s3.access_key` / `git.s3.secret_key` | inherit the matching `storage.agfs.s3` fields, else `null` | Credentials read directly; empty falls back to the SDK default credentials chain |
| `git.s3.use_path_style` | `true` | `true` uses path-style addressing (MinIO, etc.); `false` uses virtual-host style (TOS, etc.) |
| `git.s3.cas_mode` | `native` | Ref CAS mode. `native` uses S3 conditional writes (If-Match) |

After editing the server config, restart the OpenViking service. Reinitializing an HTTP SDK client does not reload server configuration.

> The repository ships ready-to-use examples: [ov.conf.git-local.example](https://github.com/volcengine/OpenViking/blob/main/examples/snapshot/ov.conf.git-local.example) and [ov.conf.git-s3-tos.example](https://github.com/volcengine/OpenViking/blob/main/examples/snapshot/ov.conf.git-s3-tos.example).

## Directory Layout Change: the `.ovgit` Directory

When the `local` backend is enabled and `base_dir` is left empty, OpenViking adds a **`.ovgit`** directory under the workspace to hold Git objects and refs:

```text
data/                      # storage.workspace
├── viking/                # user-visible resource tree (viking:// maps here)
│   └── ...
└── .ovgit/                # multi-version management data (new)
    └── {account_id}/      # one logical Git repository per account
        ├── objects/       # Git objects (commit/tree/blob), standard fanout aa/bb...
        ├── refs/
        │   └── heads/
        │       └── main   # branch ref, content is a 40-hex OID
        └── HEAD           # current branch pointer, content "ref: refs/heads/main"
```

Key points:

- `.ovgit` is an internal data directory. It is **not** exposed through `viking://` — users cannot see or modify it through the filesystem APIs (`ls`/`read`, etc.).
- Its layout matches a standard Git object store (content-addressed `objects/`, loose `refs/`), but it is managed automatically by OpenViking. You should **not** run `git` commands against it.
- For a physical backup or migration, pause writes and copy the workspace and `.ovgit` at the same checkpoint. An [OVPack export](09-ovpack.md) saves current content, not snapshot history.
- With the `s3` backend, no local `.ovgit` directory is created; data lives under the bucket's `{prefix}/{account}/...` keys instead.

## Usage

Once enabled, all three surfaces expose snapshot commands. The examples below show a minimal "commit → modify → restore" flow.

### Python SDK

Snapshot methods live under the `client.snapshot.*` namespace.

```python
from uuid import uuid4
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
client.initialize()

root = f"viking://resources/snapshot-demo-{uuid4().hex[:8]}"

# 1. Write initial content and commit v1
client.write(
    uri=f"{root}/guide.md",
    content="# Guide\n\nv1 content\n",
    mode="create",
)
v1 = client.snapshot.commit(message="v1 initial import", paths=[root])
if not v1.get("commit_oid"):
    raise RuntimeError(f"No snapshot created: {v1}")
print("v1:", v1["commit_oid"])

# 2. Modify and commit v2
client.write(
    uri=f"{root}/guide.md",
    content="# Guide\n\nv2 content\n",
    mode="replace",
)
v2 = client.snapshot.commit(message="v2 update", paths=[root])

# 3. Walk history
for c in client.snapshot.log(limit=10, paths=[root]):
    print(c["oid"][:8], c["message"])

# 4. Read historical file content
print(client.snapshot.show(v1["commit_oid"], path=f"{root}/guide.md"))

# 5. Preview the restore before changing files
print(client.snapshot.restore(project_dir=root, source_commit=v1["commit_oid"], dry_run=True))

client.close()
```

The example uses a new directory on each run and ends with a preview. After reviewing the plan, reconnect and call `restore` with the same `project_dir` and `source_commit`, with `dry_run=False`, to apply it.

### CLI

The CLI subcommands live under `ov snapshot`:

```bash
# Commit the current workspace state
ov snapshot commit -m "v1 initial import" --paths viking://resources/my_project -o json

# Walk history (newest first)
ov snapshot log --paths viking://resources/my_project --limit 10 -o json

# Set this to the commit_oid returned above
COMMIT_OID="replace-with-commit-oid"

# Read historical file content
ov snapshot show "$COMMIT_OID" --path viking://resources/my_project/guide.md

# Read a file's content from a commit (defaults to stdout; use --out-file to write a local file)
ov snapshot show "$COMMIT_OID" --path viking://resources/my_project/guide.md --out-file ./guide.md

# Preview which files would change
ov snapshot restore "$COMMIT_OID" viking://resources/my_project --dry-run -o json
```

After reviewing the preview, apply the restore:

```bash
ov snapshot restore "$COMMIT_OID" viking://resources/my_project -m "restore to v1" -o json
```

### HTTP API

```bash
# Commit
curl -X POST "http://localhost:1933/api/v1/snapshot/commit" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"message": "v1 initial import", "paths": ["viking://resources/my_project"]}'

# Walk history
curl -X GET "http://localhost:1933/api/v1/snapshot/log?branch=main&limit=10&paths=viking://resources/my_project" \
  -H "X-API-Key: your-key"

# Read historical file content
curl -X GET "http://localhost:1933/api/v1/snapshot/show?target_ref=<commit_oid>&path=viking://resources/my_project/guide.md" \
  -H "X-API-Key: your-key"

# Preview restore
curl -X POST "http://localhost:1933/api/v1/snapshot/restore" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"project_dir": "viking://resources/my_project", "source_commit": "<commit_oid>", "message": "restore to v1", "dry_run": true}'
```

Replace `<commit_oid>` with the saved commit ID. The restore request above is a preview; set `dry_run` to `false` only when you intend to apply it.

## Key Semantics: Forward-Commit Restore

`restore` uses **forward-commit** semantics: it reads the content at `source_commit`, writes the diff back into the workspace, and creates a **new commit on top of the current HEAD**. Therefore:

- The new commit's parent is the HEAD that existed before the restore — **not** `source_commit`.
- A restore that changes files adds a commit without rewriting existing history. A no-op restore may return no new commit. Snapshot storage still needs backups.
- `restore` only affects files within `project_dir` (the whole account tree when omitted); files outside that scope are left untouched.

## Excluding Files with `.ovgitignore`

The `.ovgitignore` file at the account root is an account-level exclusion file, analogous to a root `.gitignore`: files matching its rules are excluded from `commit` snapshots. It composes with the built-in system pruning (`_system`, `tasks`, vector-index derived files, etc.).

Key points:

- The rules file itself is **never ignored by `.ovgitignore` rules** — even if a rule matches `.ovgitignore` it is still included in snapshots, so rule changes are auditable and restorable.
- Rules affect **only `commit`**; `restore`, `show`, and `log` treat commit contents as authoritative and do not apply the current `.ovgitignore` as a filter. So restoring a historical snapshot still restores files that match the current rules.
- If a file was tracked in an earlier commit and a later rule matches it, the next `commit` removes it from the new snapshot (the workspace file itself is untouched).
- `.ovgitignore` never enters vector indexing/retrieval.

### Rule syntax

`.ovgitignore` is UTF-8 text supporting a common glob subset:

- Blank lines are ignored.
- A line whose first non-space character is `#` is a comment.
- Leading/trailing whitespace is trimmed.
- `!` negation is **unsupported** (its presence makes `commit` fail with an error).
- Git-style backslash escaping is **unsupported**.
- The file is capped at 64 KiB.

Matching uses account-relative Git tree paths (`/`-separated), e.g. `resources/proj/a.log`. For example, `*.log` matches `.log` files at any depth, `build/` matches a directory named `build` and its contents, and `/cache/**` matches only `cache/` at the account root.

### Python SDK

```python
# Write the rules
client.snapshot.set_gitignore(content="*.log\n")

# Read (returns an empty string when absent)
print(client.snapshot.get_gitignore())

# Delete (missing is success, idempotent)
client.snapshot.delete_gitignore()
```

On subsequent commits, files matching the rules are excluded, and the response's `ignored` field reports how many candidate paths were skipped:

```python
v = client.snapshot.commit(message="with ignore", paths=["viking://resources/my_project"])
print(v["result"], v.get("ignored"))
```

### CLI

```bash
# Set (pass content inline with --content, or read from a file with --file)
ov snapshot ignore-set --content "*.log" -o json
ov snapshot ignore-set --file ./my-rules -o json

# Get (-o json returns {"result": "<content>"}; without -o json it prints the content to stdout)
ov snapshot ignore-get -o json

# Delete (idempotent)
ov snapshot ignore-delete -o json
```

### HTTP API

```bash
# Get
curl -X GET "http://localhost:1933/api/v1/snapshot/ignore" \
  -H "X-API-Key: your-key"

# Set
curl -X PUT "http://localhost:1933/api/v1/snapshot/ignore" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"content": "*.log\n"}'

# Delete
curl -X DELETE "http://localhost:1933/api/v1/snapshot/ignore" \
  -H "X-API-Key: your-key"
```

## Notes

- Restart the service after editing its `git` configuration.
- With the `s3` backend, `git.s3.bucket` and `git.s3.region` are required; missing them causes initialization to fail.
- If a restore has vector side effects (files written/deleted), the response carries a `task_id` you can poll via `GET /api/v1/tasks/{task_id}` to track the background vector rebuild (see the [Observability guide](05-observability.md) and [API Overview](../api/01-overview.md)).
- If `.ovgitignore` is too large (over 64 KiB) or contains unsupported syntax (`!` negation, backslash escaping), `commit` fails with an `invalid operation` error; `set_gitignore` validates the size up front.
- Do not operate on the `.ovgit` directory with an external `git` tool; it is maintained by OpenViking.

## Related Documentation

- [Snapshots API](../api/11-snapshot.md): full reference of command parameters and responses
- [Configuration](01-configuration.md): full `ov.conf` reference
- [Multi-Write Storage Guide](13-multi-write-storage.md): multi-backend replication of resource data
