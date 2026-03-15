# OpenClaw OpenViking Context Engine Plugin

Use OpenViking retrieval during context assembly for OpenClaw sessions. This plugin adds retrieval-aware prompt augmentation plus memory commit/search tools.

## What it provides

- Context engine id: `contextengine-openviking`
- Retrieval pipeline:
  - Build query from recent user turns
  - Filter/rank by score threshold and top-k
  - Inject as text or simulated tool-result block
- Graceful fallback behavior when retrieval fails (timeouts/errors)
- Tools:
  - `commit_memory`
  - `search_memories`

## Files

- Plugin manifest: `openclaw.plugin.json`
- Entry point: `index.ts`
- Engine lifecycle: `context-engine.ts`
- OpenViking client: `client.ts`
- Retrieval/injection/ingestion helpers: `retrieval.ts`, `injection.ts`, `ingestion.ts`
- Fallback + telemetry helpers: `fallback.ts`, `telemetry.ts`

## Test status

Run from this folder:

```bash
pnpm exec vitest
```

Current coverage in this example includes:

- config parsing and bounds
- HTTP client behavior (headers, timeouts, error paths)
- retrieval ranking and dedupe
- injection formatting and truncation boundaries
- ingestion batching and commit flow
- context-engine lifecycle behavior
- plugin registration and tool exposure
- fallback classification + graceful degradation integration
