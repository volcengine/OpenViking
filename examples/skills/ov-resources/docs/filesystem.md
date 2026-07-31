# Filesystem Operations

OpenViking provides Unix-like filesystem operations for the `viking://resources/` namespace.

## Read Operations

### `ov read` — Read L2 full content

```bash
ov read viking://resources/docs/api.md
```

Accepts file URIs only. Passing a directory returns `INVALID_ARGUMENT` (400) with structured `details.expected="file"`, `details.actual="directory"` so clients can fall back to `ov ls`.

Parameters: `uri` (required), `offset` (line number, 0-indexed), `limit` (lines, -1 for all).

### `ov abstract` — Read L0 abstract (~100 tokens)

```bash
ov abstract viking://resources/docs/
```

Directories only.

### `ov overview` — Read L1 overview

```bash
ov overview viking://resources/docs/
```

Directories only.

### `ov ls` — List directory contents

```bash
# Basic listing
ov ls viking://resources/

# Simple paths only
ov ls viking://resources/ --simple

# Recursive
ov ls viking://resources/ --recursive
```

Parameters: `uri` (required), `--simple`, `--recursive`, `--show-all-hidden`, `--node-limit`.

Entry fields: `name`, `size`, `mode`, `modTime`, `isDir`, `uri`, `meta`.

### `ov tree` — Directory tree structure

```bash
ov tree viking://resources/my-project/
```

Parameters: `uri` (required), `--show-all-hidden`, `--node-limit`, `--level-limit`.

### `ov stat` — File/directory status

```bash
ov stat viking://resources/docs/api.md
ov stat viking://resources/docs
```

For directories, returns `count` (estimated item count). `isLocked` reports whether a path lock or ancestor TreeLock is held.

## Write Operations

### `ov write` — Update or create a file

```bash
ov write viking://resources/docs/api.md \
  --content "# Updated API\n\nFresh content." \
  --wait
```

Modes:
- `replace` (default): overwrite existing file
- `append`: append to existing file
- `create`: create new file (fails if exists, accepts `.md`, `.txt`, `.json`, `.yaml`, `.yml`, `.toml`, `.py`, `.js`, `.ts`)

`--wait` blocks until semantic/vector refresh completes. Parent directories are auto-created for `create`.

Derived semantic files cannot be written directly: `.abstract.md`, `.overview.md`, `.relations.json`.

### `ov mkdir` — Create a directory

```bash
ov mkdir viking://resources/new-project/
ov mkdir viking://resources/new-project/ --description "API docs directory"
```

`--description` writes to `.abstract.md` and queues for L0 vectorization.

## Modify Operations

### `ov rm` — Remove file or directory

```bash
# Remove single file
ov rm viking://resources/docs/old.md

# Remove directory recursively
ov rm viking://resources/old-project/ --recursive
```

`rm` is idempotent: removing a non-existent valid URI succeeds. Invalid URI formats return `INVALID_URI`. Recursive delete returns `estimated_deleted_count`.

### `ov mv` — Move file or directory

```bash
ov mv viking://resources/old-name/ viking://resources/new-name/
```

## Search Operations

### `ov grep` — Search by regex pattern

```bash
ov grep "authentication" --uri viking://resources/ --ignore-case
```

Parameters: `uri`, `pattern` (required), `--ignore-case`, `--exclude-uri`, `--node-limit`, `--level-limit`.

Response: `matches` with `uri`, `line`, `content`.

### `ov glob` — Match files by glob pattern

```bash
ov glob "**/*.md" --uri viking://resources/
ov glob "**/*.py" --uri viking://resources/
```

Parameters: `pattern` (required), `--uri`, `--node-limit`.

## Relation Operations

### `ov link` — Create relations

```bash
# Single link
ov link viking://resources/docs/auth/ viking://resources/docs/security/ \
  --reason "Security best practices"

# Multiple links (via API/SDK)
```

### `ov relations` — List relations

```bash
ov relations viking://resources/docs/auth/
```

### `ov unlink` — Remove a relation

```bash
ov unlink viking://resources/docs/auth/ viking://resources/docs/security/
```

## WebDAV

OpenViking exposes a minimal WebDAV adapter at `/webdav/resources`:

- Resources only (memories, skills, sessions not exposed)
- `PUT` accepts UTF-8 text only
- Supported methods: `OPTIONS`, `PROPFIND`, `GET`, `HEAD`, `PUT`, `DELETE`, `MKCOL`, `MOVE`
- Semantic sidecars and internal files are hidden
- `PUT` does not auto-create parent collections; use `MKCOL` first
- Creating or replacing a file triggers semantic generation
