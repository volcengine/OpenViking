# Snapshots (Multi-Version Management) Guide

Snapshots save immutable versions of a selected file tree. Use `commit` to save a version, `log` to browse history, `show` to read an older file, and `restore` to recover saved content. Snapshots do not save uncommitted changes or files excluded at commit time; ACLs and vector indexes are not versioned. Restore compares the source snapshot with HEAD, so affected paths can overwrite uncommitted changes.

Multi-version management is powered by [gitoxide](https://github.com/Byron/gitoxide) embedded in the Rust RAGFS layer, maintaining one logical Git repository per `account_id`. It is fully transparent to callers — you never run any `git` command yourself.

> For the full API reference of each command's parameters and responses, see [Snapshots API](../api/11-snapshot.md).

## When to commit

Snapshots version the file tree. They do not roll back current ACLs or preserve historical vector indexes. After restoring files, `restore` rebuilds indexes asynchronously when needed. It changes the workspace; inspect its plan with `dry_run` first.

`client.snapshot.commit()` saves file versions, while a session's `commit()` archives conversation and processes memory. Some memory workflows call snapshot after updating experiences; this does not make snapshots automatic for every write.

## Commit scope and concurrency

USER / ADMIN callers must supply `paths` for `commit` and `log`, `project_dir` for `restore`, and a file `path` for `show`. Only local ROOT mode may omit those scope parameters. Committing a project directory with read access keeps unrelated resources out of the change.

| `commit(paths=...)` input | Meaning |
| --- | --- |
| Existing file URI | Process that file |
| Existing directory URI | Recursively process current files and record deletions of previously snapshotted files in that subtree |
| Missing URI | Record deletion of that path and its subtree from the previous snapshot; no change if neither existed there |
| `[]` | Explicit empty scope; no changes |
| `None` / omitted | Entire account tree; ROOT only |

A partial commit starts from the branch's previous snapshot and preserves its versions outside the requested scope. After deletion, include the deleted URI or a covering parent directory in the next commit. Dropping the URI from `paths` would omit the deletion. A trailing `/` does not declare a file/directory type, and the current API has no explicit per-target type parameter.

For non-ROOT explicit-path commits, locks are selected from current target state before scope authorization and snapshot creation:

| Target state | Filesystem PathLock | Logical-path PathLock (Redis / SQLite / PostgreSQL / memory) |
| --- | --- | --- |
| Existing file | Exact | Exact |
| Existing directory | Tree | Tree |
| Missing path | Skip locking this target | Tree |

Filesystem skips missing targets to avoid creating a target directory or a missing parent chain just to store a lock token. The target still participates in snapshot deletion processing. A concurrent recreation may be missed or read before its write finishes; a later commit records the final state. ROOT bypasses this explicit-path locking flow.

Snapshot selection queries the actual runtime Provider's handle mode through the
native binding. Logical-path Providers request Tree locks for missing targets
without creating business directories; memory coordinates only within one client.
While the lease remains valid, a conflicting acquisition by another participant
using the same lock authority fails. Paths may still change between the initial
state check and lock acquisition. See [PostgreSQL PathLock configuration](../configuration/01-server.md#postgresql-pathlock)
for shared database coordination.

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
    },
    "snapshot": {
      "log": {
        "provider": "sqlite",
        "sqlite": {
          "path": "/data/openviking/_system/snapshot/log/snapshot_log.db"
        }
      }
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
| `git.snapshot.gc.enabled` | `true` | Schedule physical reclamation of objects after snapshot deletion. On S3, use an unversioned bucket. Physical GC requires a successful `GetBucketVersioning` check confirming versioning was never enabled. If the check fails, inspect the warning, restore access and restart the instance to retry. |
| `git.snapshot.gc.grace_seconds` | `3600` | Minimum age in seconds before reclamation. Must be at least 600. Within one instance, successive background GC scans for the same account start at least one hour after the previous scan finishes. |
| `git.snapshot.log.provider` | `sqlite` | Path history index provider for `snapshot/log?paths=...`: `sqlite`, `mysql`, `postgres`, or `none` |
| `git.snapshot.log.sqlite.path` | `{storage.workspace}/_system/snapshot/log/snapshot_log.db` | Optional absolute SQLite database path. When omitted, each workspace gets its own index DB |

Writers refresh reused objects older than 300 seconds so peers configured with the minimum grace period can collect safely during a configuration rollout. Refreshing a stale object on a versioned S3 bucket can create another object version, even when local collection is disabled.

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
    },
    "snapshot": {
      "log": {
        "provider": "mysql",
        "mysql": {
          "dsn_env": "OPENVIKING_SNAPSHOT_LOG_DSN",
          "max_connections": 16,
          "connect_timeout_ms": 3000
        }
      }
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
| `git.snapshot.log.provider` | `sqlite` | Path history index provider for `snapshot/log?paths=...`. Use `mysql` or `postgres` for multi-instance deployments |
| `git.snapshot.log.mysql.dsn_env` | `OPENVIKING_SNAPSHOT_LOG_DSN` | Environment variable containing the MySQL DSN |
| `git.snapshot.log.mysql.max_connections` | `16` | Maximum MySQL pool connections |
| `git.snapshot.log.mysql.connect_timeout_ms` | `3000` | MySQL connect timeout in milliseconds |
| `git.snapshot.log.postgres.dsn_env` | `OPENVIKING_SNAPSHOT_LOG_POSTGRES_DSN` | Environment variable containing a PostgreSQL connection URL, including TLS options |
| `git.snapshot.log.postgres.max_connections` | `16` | Maximum PostgreSQL pool connections |
| `git.snapshot.log.postgres.connect_timeout_ms` | `3000` | PostgreSQL pool connection acquisition timeout in milliseconds |
| `git.snapshot.log.postgres.operation_timeout_ms` | `3000` | Deadline for initialization and each complete index operation (including connection acquisition, lock waits and all write batches), in milliseconds |

### Path-Filtered Log Index

`git.snapshot.log` controls the derived index used by `snapshot/log` when
`paths` is provided. It does not change snapshot commits, stored file content,
or API responses. It only reduces the storage work needed to find commits that
touched a requested file or directory.

Provider choices:

| Provider | Use case |
| --- | --- |
| `sqlite` | Single-node deployments. By default the DB is stored at `{storage.workspace}/_system/snapshot/log/snapshot_log.db`; override with an absolute `git.snapshot.log.sqlite.path` if needed |
| `mysql` | Multi-instance deployments. Set the DSN in the environment variable named by `git.snapshot.log.mysql.dsn_env` |
| `postgres` | Multi-instance deployments. Provision the schema first, then configure `git.snapshot.log.postgres.dsn_env` |
| `none` | Disable the index and keep the original history scan path |

For MySQL:

```bash
export OPENVIKING_SNAPSHOT_LOG_DSN='mysql://user:password@host:3306/openviking'
```

For PostgreSQL (14+), provision the schema once with a deployment role:

```bash
psql "$OV_POSTGRES_ADMIN_DSN" -v ON_ERROR_STOP=1 \
  -f openviking/service/sql/snapshot_log_postgres_init.sql
export OPENVIKING_SNAPSHOT_LOG_POSTGRES_DSN='postgresql://user:password@host:5432/openviking?sslmode=verify-full&sslrootcert=/etc/ssl/pg-ca.pem'
```

```json
{
  "git": {
    "snapshot": {
      "log": {
        "provider": "postgres",
        "postgres": {
          "dsn_env": "OPENVIKING_SNAPSHOT_LOG_POSTGRES_DSN",
          "max_connections": 16,
          "connect_timeout_ms": 3000,
          "operation_timeout_ms": 3000
        }
      }
    }
  }
}
```

Runtime startup does not create or migrate PostgreSQL tables. The runtime role
needs `SELECT`, `INSERT`, `UPDATE` and `DELETE` on both `snapshot_log` and `snapshot_commit`, `USAGE` on the `snapshot_log` identity sequence,
and `SELECT`, `INSERT` and `UPDATE` on `snapshot_log_generation`. Reapply the provisioning SQL when upgrading.
Use a dedicated database or schema per OV deployment; accounts and branches
are isolated within that index. When using a custom schema, set the URL's
`options` parameter to a URL-encoded `-c search_path=your_schema`.
No PostgreSQL extension or Python database extra is required: the driver is
part of the Rust binding (`ragfs/postgres` for standalone Rust consumers).

PostgreSQL index operations have a finite execution deadline. The provider also
sets `statement_timeout` and `lock_timeout` on its connections. If initialization
times out, startup continues with the Git history scan. If an append times out,
commit or restore continues and queues best-effort backfill; queries fall back to
Git when the index is unavailable. A write transaction that times out before
commit is rolled back.
Increase `operation_timeout_ms` if normal index operations exceed the default.

Each account/branch append is serialized with a transaction advisory lock,
including the first append to an empty index. All rows are committed atomically
only when the latest indexed commit matches the expected parent.
Path indexes use `md5(path)` to stay within PostgreSQL B-tree width limits;
queries also check the original path. A digest collision for the same commit
rejects the append rather than silently dropping a path.
Switching providers does not copy an existing index. Path queries trigger
background backfill from Git history when needed. An unavailable database or
missing schema disables the index for that service instance at startup and
falls back to history scanning; restart after restoring the backend.
The fallback still has the 1,000-candidate-commit scan limit.

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
print(client.wait_processed())
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
print(client.wait_processed())
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

The example uses a new directory on each run, waits for background processing before each commit, and ends with a preview. Inspect the processing results for errors; queue completion alone does not prove index consistency. After reviewing the plan, reconnect and call `restore` with the same `project_dir` and `source_commit`, with `dry_run=False`, to apply it.

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
- When the source and HEAD trees differ in the selected scope, restore adds a commit without rewriting history. If those trees match, it returns `result: "noop"` without a new commit, even if the working files differ. Snapshot storage still needs backups.
- `restore` only affects files within `project_dir` (the whole account tree when omitted); files outside that scope are left untouched.

## Delete historical snapshots

Copy full commit IDs from `ov snapshot log -o json`. Multiple historical commits can be deleted together. Preview the parent changes and the number of affected paths per commit before applying the deletion.

```sh
ov snapshot delete "$COMMIT_ID" --dry-run -o json
ov snapshot delete "$COMMIT_ID" -o json
```

The change to history is immediate and retained snapshots keep their IDs. Background GC reclaims unique objects after the grace period. Check whether Compile runs or other workflows still need a commit before deleting it. See the [snapshot API](../api/11-snapshot.md#delete) for permissions, ref-tip protection and GC configuration.

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
- `git.snapshot.log.sqlite.path` must be an absolute path. Use `provider: "none"` to disable the path index.
- If a restore has vector side effects (files written/deleted), the response carries a `task_id` you can poll via `GET /api/v1/tasks/{task_id}` to track the background vector rebuild (see the [Observability guide](05-observability.md) and [API Overview](../api/01-overview.md)).
- If `.ovgitignore` is too large (over 64 KiB) or contains unsupported syntax (`!` negation, backslash escaping), `commit` fails with an `invalid operation` error; `set_gitignore` validates the size up front.
- Do not operate on the `.ovgit` directory with an external `git` tool; it is maintained by OpenViking.

## Related Documentation

- [Snapshots API](../api/11-snapshot.md): full reference of command parameters and responses
- [Configuration](01-configuration.md): full `ov.conf` reference
- [Multi-Write Storage Guide](13-multi-write-storage.md): multi-backend replication of resource data
