# @openviking/dsh-plugin

OpenViking memory & context plugin for [deepseek-harness](https://github.com/deepseek-ai/deepseek-harness) (`dsh`).

A thin dsh adapter over the OpenViking memory-plugin family's shared runtime:
everything in [`shared/`](./shared) is vendored from
[`examples/memory-plugin-shared`](../memory-plugin-shared) by its `sync.mjs`
(do not edit those files here). Credentials, capture filtering, durable
delivery with replay, and recall — server-side query expansion, cross-turn
dedup, peer scoping, graceful downgrade for older servers, token-budgeted
rendering — are all family code shared with the Claude Code, Codex, OpenCode,
pi, and zcode plugins. Only the dsh integration is new:

- **Capture** — maps dsh `session/event` surface messages into family capture
  payloads (ordered per session); `turn/end` flushes through `batch-send`
  (failures spill into the durable pending queue and replay on next start)
  and triggers a throttled `commit`, OpenViking's memory-extraction step.
- **Recall** — an `agent/pre-step` waterfall appends the shared
  `buildRecallBlock` output as a durable, source-attributed user message
  (`source: { kind: 'plugin', plugin: 'openviking', form: 'recall' }`). It
  never touches the system prompt, so it survives presets whose persona is
  `complete: true` (e.g. `minimal`), where prompt-assembly contributions are
  silently dropped.
- **Session seed** — on `agent/session-start` (resume) the committed
  OpenViking session overview is queued through `agent.inject()`.
- **Tools** — optional `ov_search` / `ov_read` / `ov_add_memory`
  (`ov_add_memory` mirrors the `ov add-memory` CLI flow: ephemeral session
  plus commit).

## Install

```sh
dsh plugin --profile <profile> add @openviking/dsh-plugin
```

The package declares `dsh.bundle.patch`, so dsh composes its
[`cordis.patch.yml`](./cordis.patch.yml) into the profile automatically.

## Configuration

Credentials resolve through the family chain — `OPENVIKING_*` environment →
`~/.openviking/ovcli.conf` → `~/.openviking/ov.conf` — so an `ov`-configured
machine needs nothing. The actor peer defaults to the workspace-derived peer
(same rule as the other plugins); set an explicit peer or `workspacePeer: false`
to change that. Behaviour knobs live in cordis config:

```yaml
- id: openviking
  name: '@openviking/dsh-plugin'
  config:
    syncTurns: true          # mirror conversation into OpenViking
    autoRecall: true         # inject recalled context at eligible steps
    recallLimit: 5
    scoreThreshold: 0.35
    minQueryLength: 3
    recallTokenBudget: 2000
    sessionSeed: true        # inject committed session overview on resume
    seedTokenBudget: 2000
    commitOnTurnEnd: true
    commitMinIntervalMs: 300000
    commitKeepRecentCount: 10
    workspacePeer: true
    tools: true              # ov_search / ov_read / ov_add_memory
```

All OpenViking traffic is failure-contained: an unreachable server degrades
the plugin to a no-op (rate-limited warnings, durable queue for capture) and
never breaks an agent step.

## Development

```sh
npm install
npm run typecheck
npm test          # unit tests
npm run test:e2e  # real Loader boot + mock LLM + OpenViking stub, keyless
npm run build
```

The e2e boots a real dsh Loader composition (`tests/fixtures/dsh-plugin.cordis.yml`)
with a deterministic mock LLM adapter and an in-process OpenViking stub whose
context-face endpoints 404 (pinning the shared recall runtime to its raw
`search/find` fallback), then asserts both directions on the persisted session
log and the recorded stub traffic: the conversation is mirrored into one
`dsh-<session>` OpenViking session and committed, and each turn's recall block
is appended as a durable `user/message` that reaches the model surface but
never the request headers and never echoes back into capture.
