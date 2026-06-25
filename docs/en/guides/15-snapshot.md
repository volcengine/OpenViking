# Snapshots (Multi-Version Management) Guide

This guide explains how to enable and use OpenViking's multi-version management (snapshots). On top of VikingFS, it provides Git-based `commit`/`log`/`show`/`restore` primitives, letting you save an account's resource tree as a series of immutable snapshots, walk history, compare versions, and restore the workspace to any past state.

Multi-version management is powered by [gitoxide](https://github.com/Byron/gitoxide) embedded in the Rust RAGFS layer, maintaining one logical Git repository per `account_id`. It is fully transparent to callers — you never run any `git` command yourself.

> For the full API reference of each command's parameters and responses, see [Snapshots API](../api/11-snapshot.md).

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

After editing the config, restart the OpenViking service (or re-initialize the SDK client) for it to take effect.

> The repository ships ready-to-use examples: [ov.conf.git-local.example](file:///cloudide/workspace/OpenViking/examples/snapshot/ov.conf.git-local.example) and [ov.conf.git-s3-tos.example](file:///cloudide/workspace/OpenViking/examples/snapshot/ov.conf.git-s3-tos.example).

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
- When backing up or migrating the workspace, copy `.ovgit` along with it to preserve the full version history.
- With the `s3` backend, no local `.ovgit` directory is created; data lives under the bucket's `{prefix}/{account}/...` keys instead.

## Usage

Once enabled, all three surfaces expose snapshot commands. The examples below show a minimal "commit → modify → restore" flow.

### Python SDK

Snapshot methods live under the `client.snapshot.*` namespace.

```python
import openviking as ov

client = ov.OpenViking()
client.initialize()

root = "viking://resources/my_project"

# 1. Write initial content and commit v1
client.write(f"{root}/guide.md", "# Guide\n\nv1 content\n", mode="create", wait=True)
v1 = client.snapshot.commit(message="v1 initial import")
print("v1:", v1["commit_oid"])

# 2. Modify and commit v2
client.write(f"{root}/guide.md", "# Guide\n\nv2 content\n", mode="replace", wait=True)
v2 = client.snapshot.commit(message="v2 update")

# 3. Walk history
for c in client.snapshot.log(limit=10):
    print(c["oid"][:8], c["message"])

# 4. Inspect a commit's metadata
print(client.snapshot.show(v1["commit_oid"])["message"])

# 5. Restore the workspace to v1 (creates a new "forward" commit on top of v2)
client.snapshot.restore(project_dir=root, source_commit=v1["commit_oid"], message="restore to v1")

client.close()
```

### CLI

The CLI subcommands live under `ov snapshot`:

```bash
# Commit the current workspace state
ov snapshot commit -m "v1 initial import" -o json

# Walk history (newest first)
ov snapshot log --limit 10 -o json

# View commit metadata
ov snapshot show <commit_oid> -o json

# Read a file's content from a commit (defaults to stdout; use --out-file to write a local file)
ov snapshot show <commit_oid> --path viking://resources/my_project/guide.md --out-file ./guide.md

# Restore a directory to a past snapshot (positional args are <source_commit> then <project_dir>)
ov snapshot restore <commit_oid> viking://resources/my_project -m "restore to v1" -o json

# Preview which files would change first
ov snapshot restore <commit_oid> viking://resources/my_project --dry-run -o json
```

### HTTP API

```bash
# Commit
curl -X POST "http://localhost:1933/api/v1/snapshot/commit" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"message": "v1 initial import"}'

# Walk history
curl -X GET "http://localhost:1933/api/v1/snapshot/log?branch=main&limit=10" \
  -H "X-API-Key: your-key"

# View commit metadata
curl -X GET "http://localhost:1933/api/v1/snapshot/show?target_ref=<commit_oid>" \
  -H "X-API-Key: your-key"

# Restore
curl -X POST "http://localhost:1933/api/v1/snapshot/restore" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"project_dir": "viking://resources/my_project", "source_commit": "<commit_oid>", "message": "restore to v1"}'
```

## Key Semantics: Forward-Commit Restore

`restore` uses **forward-commit** semantics: it reads the content at `source_commit`, writes the diff back into the workspace, and creates a **new commit on top of the current HEAD**. Therefore:

- The new commit's parent is the HEAD that existed before the restore — **not** `source_commit`.
- HEAD always advances monotonically, and **history is never rewritten or lost** — going back to an older version is itself a new commit.
- `restore` only affects files within `project_dir` (the whole account tree when omitted); files outside that scope are left untouched.

## Notes

- After editing the `git` config, restart the service / re-initialize the client for it to take effect.
- With the `s3` backend, `git.s3.bucket` and `git.s3.region` are required; missing them causes initialization to fail.
- If a restore has vector side effects (files written/deleted), the response carries a `task_id` you can poll via `GET /api/v1/tasks/{task_id}` to track the background vector rebuild (see the [Observability guide](05-observability.md) and [API Overview](../api/01-overview.md)).
- Do not operate on the `.ovgit` directory with an external `git` tool; it is maintained by OpenViking.

## Related Documentation

- [Snapshots API](../api/11-snapshot.md): full reference of command parameters and responses
- [Configuration](01-configuration.md): full `ov.conf` reference
- [Multi-Write Storage Guide](13-multi-write-storage.md): multi-backend replication of resource data
