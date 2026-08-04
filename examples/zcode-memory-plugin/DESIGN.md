# ZCode Memory Plugin — Design

This document records the **assumptions** behind the ZCode memory plugin, what it reuses, and the points that need confirmation once ZCode publishes official extension documentation.

## TL;DR

- The plugin is a **thin adapter** over [`memory-plugin-shared/lib/agent-hook-runtime.mjs`](../memory-plugin-shared/lib/agent-hook-runtime.mjs), which already powers the Cursor and TRAE integrations. Recall, capture batching, dedup, pending-queue replay, peer derivation, and credential resolution are reused, not reimplemented.
- The hook surface targeted is the **Cursor/TRAE-compatible lifecycle** — `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `Stop` — with the `decision: "approve"` + `hookSpecificOutput.additionalContext` output contract.
- ZCode's extension mechanism is **not yet publicly documented** (as of 2026-07-28). The events, field names, output schema, and on-disk config layout below are assumptions inherited from Cursor/TRAE — they are the most likely shape because ZCode is an IDE-style coding agent like Cursor/TRAE, but they must be confirmed against ZCode's official docs before this is considered production-ready.

## Why mirror TRAE

TRAE is ByteDance's IDE-style coding agent — the closest published analogue to ZCode. The TRAE plugin is also the cleanest IDE-style integration in this repo: it does not depend on a transcript file (it reads prompt + assistant message directly off the hook event) and it commits every turn. That maps well onto a coding agent whose hook surface we do not yet know precisely: the fewer assumptions about ZCode internals, the better. So the ZCode plugin shares TRAE's structure almost file-for-file:

| File | TRAE | ZCode | Difference |
|---|---|---|---|
| Hook entrypoints | `session-start.mjs`, `auto-recall.mjs`, `auto-capture.mjs` | identical | none |
| URI guard | `uri-guard.mjs` | identical | none |
| Turn builder | `trae-turns.mjs` (`cleanTraeText` + `buildTraeTurns`) | `zcode-turns.mjs` (`cleanZcodeText` + `buildZcodeTurns`) | name only |
| Hook runtime | `trae-hook.mjs` | `zcode-hook.mjs` | client id + session-id prefix |
| MCP proxy | `servers/mcp-proxy.mjs` | identical | client id |
| Manifest | `openviking.integration.json` clients `["trae","trae-cn"]` | clients `["zcode"]` | one client for now |
| Hook config token | `__OPENVIKING_TRAE_ROOT__` | `__OPENVIKING_ZCODE_ROOT__` | name only |
| Session-id prefix | `tr-` / `trcn-` | `zc-` (and reserved `zcn-`) | — |

## Reuse map (what is NOT duplicated)

Everything listed here comes from `memory-plugin-shared/lib/` and is consumed by `zcode-hook.mjs` / `servers/mcp-proxy.mjs` via relative import — no copy is made into this plugin:

- `agent-hook-runtime.mjs` — `loadAgentHookConfig`, `readHookInput`, `resolveNativeSessionId`, `deriveAgentSessionId`, `resolveAgentCwd`, `makeAgentFetchJSON`, `readHookState` / `writeHookState`, `withAgentHookLock`, `recallForPrompt`, `buildAgentProfile`, `addAgentMessages`, `commitAgentSession`, `replayAgentPending`, `shouldBypassAgent`, `stableHash`, `createAgentLogger`.
- `agent-uri-guard.mjs` — `evaluateAgentUriGuard` (tool-name normalization + per-tool hint).
- `workspace-peer.mjs` — `deriveWorkspacePeerId` / `resolveEffectivePeerId` (the post-#3516 shape: explicit > workspace-derived > none).
- `credentials.mjs` — `resolveOpenVikingCredentials` (ovcli.conf → env → ov.conf → 127.0.0.1:1933).
- `recall-core.mjs`, `profile-inject.mjs`, `batch-send.mjs`, `pending-queue.mjs`, `session-model.mjs`, `debug-log.mjs`, `mcp-proxy-core.mjs`.

The only plugin-local logic is `zcode-turns.mjs` (12 lines: strip injected `<openviking-context>` / `<relevant-memories>` blocks so they are not echoed into storage, then build a `[user, assistant]` turn list from the Stop event fields).

## Commit model

ZCode uses **commit-on-every-Stop** (same as TRAE). Each `Stop`:

1. Builds `[user, assistant]` turn(s) from the event's `prompt` / `last_assistant_message` / `text_content` fields.
2. Deduplicates by `(turnKey, role, content)` hash against the per-session `capturedHashes` set.
3. Sends new turns via `POST /api/v1/sessions/zc-<id>/messages` (auto-creates the OV session on first append).
4. Calls `POST /api/v1/sessions/zc-<id>/commit` so the extractor runs immediately — short sessions are not lost even if ZCode never fires a session-end signal.

There is intentionally **no PreCompact / SessionEnd hook wiring**: as with TRAE, committing every turn removes the need for an end-of-session signal (which coding agents historically do not emit reliably — see the Codex plugin's `DESIGN.md` for the same conclusion).

## Assumptions that need ZCode confirmation

These are the load-bearing assumptions. Each one is plausible because it matches Cursor/TRAE (and ZCode is the same class of tool), but until ZCode publishes extension docs they must be treated as **hypotheses to verify**, not facts.

1. **ZCode exposes lifecycle hooks** named (or aliased as) `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `Stop`, configured via a JSON file (the assumed on-disk path is `~/.zcode/hooks.json`). If ZCode uses different event names, the keys in [`hooks/hooks.json`](./hooks/hooks.json) are the only thing to rename.
2. **The hook command line** is `node <absolute path>/scripts/<entry>.mjs <client-id>` invoked via `node`, with the hook payload delivered on **stdin as JSON** and the response emitted on **stdout as JSON**. (This is the Cursor/TRAE/Claude Code contract.)
3. **The output schema** accepts `{ "decision": "approve", "hookSpecificOutput": { "hookEventName": "...", "additionalContext": "..." } }` for context injection, and `{ "hookSpecificOutput": { "hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "..." } }` for the URI guard. If ZCode uses a different schema, only `zcode-hook.mjs`'s `approve()` and `uri-guard.mjs`'s `evaluateZcodeUriGuard` need adjustment.
4. **The Stop payload** contains the assistant text under one of `last_assistant_message` / `text_content`, the user prompt under `prompt`, and the session identifier under one of `session_id` / `conversation_id` / `generation_id`. The shared runtime (`resolveNativeSessionId`) already handles all of these aliases; if ZCode uses a unique field name, add it to that resolver in `memory-plugin-shared` (one line) rather than patching this plugin.
5. **The MCP client config** is JSON with an `mcpServers` map whose values are `{ "command": "node", "args": ["<path>/servers/mcp-proxy.mjs"] }`, located at `~/.zcode/.mcp.json` or `~/.zcode/mcp.json`. If ZCode uses a different filename/format, only the installer needs updating.
6. **The `${PLUGIN_ROOT}`-style token** for hook command paths is **not** assumed to exist. The installer substitutes the absolute plugin path into a `__OPENVIKING_ZCODE_ROOT__` placeholder at install time (same technique the TRAE installer uses with `__OPENVIKING_TRAE_ROOT__`), so this plugin works whether or not ZCode supports inline token substitution.

### If ZCode uses a Cursor-style marketplace

Should ZCode ship a plugin marketplace (like Codex's `codex plugin` or Cursor's `.cursor-plugin/plugin.json`), the natural follow-up is a tiny `marketplace.json` entry pointing at `./examples/zcode-memory-plugin`, mirroring how the Codex marketplace catalog is structured at the repo root. That is intentionally out of scope for this first cut; it should land alongside official ZCode marketplace docs.

## What is NOT assumed

- No assumption about ZCode's CLI binary name. The installer detects `zcode` on PATH but lets the user override the config directory via `ZCODE_CONFIG_DIR` and the binary via `ZCODE_BIN`.
- No assumption about a CN-specific build. The client id parameterization (`zcode` vs `zcode-cn`) is reserved so a future TRAE-CN-style split is a one-line change; only `zcode` is wired today.
- No write into ZCode's system settings, no installation of skills or rules — just hooks + MCP, matching the minimal TRAE footprint.

## Testing

`scripts/zcode-hooks.test.mjs` (run with `node --test`) covers:

- The plugin ships the required hook + MCP + integration-manifest files.
- `hooks.json` commands reference all four entrypoints via the `__OPENVIKING_ZCODE_ROOT__` token.
- The URI guard follows the Cursor/TRAE PreToolUse deny contract for `viking://` paths and passes through local paths.
- The turn builder reads event fields (no transcript file) and strips previously injected memory blocks.
- End-to-end: a `UserPromptSubmit` hook against a stub OpenViking server injects the recall digest; a duplicate event (same `generation_id`) is deduped; `Stop` captures the turn and commits it under the `zc-<session-id>` id; a later identical turn is not mistaken for a duplicate.

These tests mirror [`trae-hooks.test.mjs`](../trae-memory-hooks/scripts/trae-hooks.test.mjs) almost exactly — the structural parity is intentional and is the main signal that the shared runtime is being exercised rather than reimplemented.

## Open questions / future work

- **Official ZCode hook docs**: replace assumptions 1–5 above with verified facts and adjust file/field names as needed.
- **ZCode plugin marketplace**: add a `marketplace.json` entry once the marketplace contract is documented.
- **Per-turn commit cadence**: if ZCode's hook surface emits a reliable SessionEnd, consider switching from commit-on-every-Stop to commit-on-SessionEnd + threshold (closer to the Codex plugin's policy).
- **CN build**: if a `zcode-cn` build ships separately, add it to `openviking.integration.json` clients and let the installer select the prefix (`zcn-`), mirroring the trae/trae-cn split.
