# Watch Task Management (`ov task watch`)

Watch tasks enable scheduled refresh of resources imported via `ov add-resource --watch-interval`. The control plane is available via `ov task watch` CLI subcommands.

## Core Concepts

- **Creation**: Set `watch_interval > 0` on `ov add-resource` to create or update a watch task.
- **Binding**: Tasks bind to `--to` if provided; otherwise to the imported `root_uri`.
- **Scheduling**: `WatchScheduler` checks expired tasks every 60 seconds.
- **Pause/Resume**: `is_active` is orthogonal to `watch_interval`; pause without losing cadence.

## Subcommands

### `ov task watch ls` — List watch tasks

```bash
# List active watches only
ov task watch ls --active-only

# List all (including paused)
ov task watch ls
```

### `ov task watch show` — Inspect a single watch

```bash
ov task watch show viking://resources/guide.md
ov task watch show <task_id>
```

The key argument is auto-classified: `viking://` URIs route by URI, anything else is treated as a task ID.

### `ov task watch pause` — Pause without losing cadence

```bash
ov task watch pause viking://resources/guide.md
```

Sets `is_active=false`. The `watch_interval` is preserved.

### `ov task watch resume` — Resume a paused watch

```bash
ov task watch resume viking://resources/guide.md
```

Sets `is_active=true`.

### `ov task watch update` — Update watch parameters

```bash
# Update interval
ov task watch update viking://resources/guide.md --interval 30

# Update reason and instruction
ov task watch update viking://resources/guide.md \
  --reason "Updated docs" \
  --instruction "Focus on API changes"
```

Supported update fields: `--interval`, `--active` / `--no-active`, `--reason`, `--instruction`.

### `ov task watch trigger` — Immediate refresh

```bash
ov task watch trigger viking://resources/guide.md
```

Fire-and-forget; returns immediately while re-ingest runs in background.

### `ov task watch rm` — Remove a watch task

```bash
ov task watch rm viking://resources/guide.md
```

## Lifecycle Summary

| Action | Command |
|---|---|
| Create/update | `ov add-resource <source> --to <uri> --watch-interval <minutes>` |
| List | `ov task watch ls [--active-only]` |
| Inspect | `ov task watch show <key>` |
| Pause | `ov task watch pause <key>` |
| Resume | `ov task watch resume <key>` |
| Update cadence | `ov task watch update <key> --interval <minutes>` |
| Trigger now | `ov task watch trigger <key>` |
| Remove | `ov task watch rm <key>` |
| Cancel via add-resource | `ov add-resource <source> --to <uri> --watch-interval 0` |

`key` can be either a `viking://` URI or a task ID.
