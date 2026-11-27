# DESIGN: the hook plugin

## The flow owns the behaviour

`lib/flow/` decides everything a session does: which requests are sent, when a session commits, and what the model is told. `runStage(host, event, payload, { detached, cfg, outcome })` in `lib/flow/index.mjs` runs one host event; the caller hands the result to `host.emit`. The stages are in `start.mjs` (offline-queue replay, profile, skill catalog, optional archive overview), `prompt.mjs` (recall, and anything an earlier event could not deliver), `capture.mjs` (turn end, compaction, session end), `commit.mjs`, `pointer.mjs` (the session-history note) and `tool.mjs` (the `viking://` guard and `session_history` defaults).

## What a host hands the flow

A host module (`hosts/<id>/host.mjs`) describes the host and decides nothing. Its contract is `lib/flow/host.d.mts`: `id`, `sessionPrefix`, `events` (host event name to stage: start, prompt, turnEnd, compactSoon, end, compacted, toolCall, toolResult), `budgets`, `canDetach`, `syncEvents`, `detachEvents`, `facts`, `config`, `toolHints`, and the functions `read` and `decode` (pure), `locate`, `emit`, `tool`, `mcpSession`, `legacyCursor`, `present`, `compress` and `observe` (told what each event did, for files the host keeps for its own UI; it cannot change the answer). `decode.mjs` beside it turns the host's transcript into turns. A host module does no network, no queue and no state files.

`scripts/hook.mjs <event> <host>` is the one hook entry, `scripts/uri-guard.mjs <host>` the guard, `servers/mcp-proxy.mjs` the one MCP proxy. OpenCode, DSH and pi run the same flow in-process through their own `host.mjs`.

## Where state lives

- Per-session hook state: `~/.openviking/hook-state/<host>/<session>.json` (`OPENVIKING_HOOK_STATE_DIR` overrides the root), written by `lib/agent-hook-runtime.mjs` under a lock.
- Offline queue: `~/.openviking/pending`, replayed at session start; network errors, 408, 429 and 5xx are retried.

## When a session commits

`lib/flow/commit.mjs`: the plugin commits at a compaction (unless `autoCommitOnCompact` is false) and at session end, with `keep_recent_count` 0. After the session's first write it sets the session's auto-commit policy once: `pending_token_threshold` = `commitTokenThreshold`, `idle_timeout_seconds` = `commitIdleSeconds` (0 = none; under 300 raised to 300), `message_count_threshold` 0. The server checks the threshold on each write; the plugin never checks a threshold itself. This needs OpenViking server 0.4.13 or later, and the idle commit needs `memory.session_auto_commit.enabled` on the server.

## Server Feature Table

`lib/server-features.json` names each OpenViking server capability a client depends on, with the first server version that has it. The gateway and the plugins answer "does this server have X" from this one table: `lib/server-features.mjs` reads it for the plugins (`supports(feature, version)`, `featureStatus`, `healthVersion`), and `openviking_gateway/client.py` reads a copy the sync writes into the gateway package (`openviking_gateway/server-features.json`; `supports(feature, version)`, `VikingClient.supports(key, feature)`). The copy is committed, and CI fails a pull request whose copy differs from the source; a plugin gets the table only with a module that reads it.

Adding a row:

1. Add `"name": "X.Y.Z"` to `lib/server-features.json`.
2. Add `"name"` to the `ServerFeature` union in `lib/server-features.d.mts`, so TypeScript callers can name it.
3. Run `node examples/memory-plugin-shared/sync.mjs` and commit the regenerated `openviking_gateway/server-features.json` with the source.
4. Add boundary cases for the row (the version itself, the release before it, a development build of it) to `tests/fixtures/server_feature_versions.json`.

Rules for a row:

- The version is the next unreleased release at the time the server change merges, the release its users will first see it in.
- Only the release part of a version is compared, so a development build such as `0.5.7.dev44` counts as 0.5.7.
- A version that cannot be read, or none at all, counts as not supporting. Both readers accept the PEP 440 grammar and nothing else; `tests/fixtures/server_feature_versions.json` holds the cases both test suites check.
- Callers ask the table before they use a capability. They do not infer support from a 404 or another error from the server.
