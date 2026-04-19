# OpenClaw Migration

This tool imports existing OpenClaw data into OpenViking through two paths:

- `memory`: import native OpenClaw memory markdown files directly into OpenViking memory URIs
- `transcript`: replay historical OpenClaw jsonl transcripts into OpenViking sessions and commit them
- `all`: run both paths in one pass

By default it reads from `~/.openclaw` and connects to OpenViking over HTTP using the same config as `ovcli.conf`. You can also point it at an embedded local data path with `--ov-path`.

The migration entrypoint is synchronous. Use `SyncHTTPClient` or `SyncOpenViking`;
async OpenViking clients are intentionally rejected to avoid silent coroutine misuse.

## Examples

```bash
python examples/openclaw-migration/migrate.py --mode memory --dry-run
```

```bash
python examples/openclaw-migration/migrate.py --mode all --wait
```

```bash
python examples/openclaw-migration/migrate.py \
  --mode transcript \
  --agent main \
  --url http://127.0.0.1:1933 \
  --api-key "$OPENVIKING_API_KEY"
```

```bash
python examples/openclaw-migration/migrate.py \
  --mode memory \
  --ov-path ~/.openviking
```

## Mapping

Native OpenClaw memory files map to deterministic OpenViking memory URIs:

- `workspace/MEMORY.md` -> `viking://user/memories/entities/openclaw-memory.md`
- `workspace/memory/YYYY-MM-DD.md` -> `viking://user/memories/events/openclaw-YYYY-MM-DD.md`
- `workspace/memory/YYYY-MM-DD-*.md` -> `viking://agent/memories/cases/openclaw-YYYY-MM-DD-*.md`

Deterministic URIs and session ids make reruns naturally resumable:

- existing memory targets are skipped unless `--overwrite` is set
- replayed transcript sessions use a stable `openclaw-<agent>-<session>` target id
