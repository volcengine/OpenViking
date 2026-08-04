# OpenViking Memory Hooks for ZCode

Long-term semantic memory for the [ZCode](https://www.volcengine.com/) AI coding agent, powered by [OpenViking](https://github.com/volcengine/OpenViking). This is the ZCode counterpart to [`trae-memory-hooks`](../trae-memory-hooks) and [`codex-memory-plugin`](../codex-memory-plugin) — it gives ZCode the same auto-recall, incremental turn capture, lifecycle commit, and OpenViking MCP tools that Claude Code, Codex, Cursor, TRAE, and OpenCode already have.

It hooks the ZCode lifecycle to:

- **Recall on `UserPromptSubmit`** — fetch relevant OpenViking memories for the current prompt and inject them as `additionalContext` so the model sees them without an extra tool call.
- **Capture on `Stop`** — append the completed user/assistant turn to a deterministic OpenViking session id `zc-<zcode_session_id>` and immediately commit it so OpenViking's memory extractor runs on every turn.
- **Profile on `SessionStart`** — load the actor's OpenViking profile (global + workspace memory digest) once per session.
- **Guard `viking://` URIs on `PreToolUse`** — block accidental local filesystem / shell access to `viking://` virtual paths and point the agent back to the OpenViking MCP tools.

It also starts a local stdio MCP proxy that forwards to OpenViking's native `/mcp` endpoint with credentials resolved from `~/.openviking/ovcli.conf` (or `OPENVIKING_*` env vars), so the model has direct access to the server's retrieval, memory, resource, watch, filesystem, and code-navigation tools.

## Status

Functional, reuses the same shared runtime as the Cursor and TRAE plugins. The hook surface it targets is the **Cursor/TRAE-compatible lifecycle** (`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `Stop`) with `decision: "approve"` + `hookSpecificOutput.additionalContext` output. See [DESIGN.md](./DESIGN.md) for the assumptions about ZCode's extension surface and what needs confirmation once ZCode publishes official hook/MCP documentation.

## Quick Start

```bash
bash examples/zcode-memory-plugin/setup-helper/install.sh
```

The installer writes the lifecycle `hooks.json` and MCP config into your ZCode config directory and walks you through the OpenViking connection settings. See [INSTALL.md](./INSTALL.md) (or [INSTALL-ZH.md](./INSTALL-ZH.md) for Chinese).

## How It Works

ZCode fires lifecycle hook events; each event invokes the matching `scripts/*.mjs` entrypoint, which delegates to [`scripts/zcode-hook.mjs`](./scripts/zcode-hook.mjs). That file is a thin adapter around [`memory-plugin-shared/lib/agent-hook-runtime.mjs`](../memory-plugin-shared/lib/agent-hook-runtime.mjs) — the same runtime used by the TRAE and Cursor integrations — so peer derivation, recall, capture batching, dedup, pending-queue replay, and idle commit are all reused, not reimplemented.

```
   ┌──────────────────────────────────────────────────────────┐
   │                          ZCode                            │
   └──┬──────────────┬────────────────┬──────────────────┬─────┘
      │              │                │                  │
 SessionStart   UserPromptSubmit   PreToolUse           Stop
      │              │                │                  │
 ┌────▼─────┐  ┌─────▼──────┐  ┌──────▼──────┐  ┌───────▼───────┐
 │ session- │  │ auto-      │  │ uri-guard   │  │ auto-capture  │
 │ start.mjs│  │ recall.mjs │  │ .mjs        │  │ .mjs          │
 └─────┬────┘  └─────┬──────┘  └─────────────┘  └───────┬───────┘
       │             │                                 │
       └──► zcode-hook.mjs ◄───────────────────────────┘
                  │
                  ▼
       memory-plugin-shared/lib/agent-hook-runtime.mjs
                  │
                  ▼
          OpenViking REST API + /mcp
```

The shared runtime reads credentials from `~/.openviking/ovcli.conf` (or `OPENVIKING_*` env vars), derives a workspace peer using Claude's project-directory naming rule, and sends `peer_id` / `X-OpenViking-Actor-Peer` on every request. Each ZCode session becomes `zc-<safe-session-id>` on the OpenViking side. Capture filters previously injected `<openviking-context>` blocks so the model's own recall context is not echoed back into storage.

## Files

```text
examples/zcode-memory-plugin/
├── openviking.integration.json   # OpenViking integration manifest
├── .mcp.json                     # stdio MCP wiring (openviking server)
├── hooks/
│   └── hooks.json                # SessionStart + UserPromptSubmit + PreToolUse + Stop
├── scripts/
│   ├── session-start.mjs         # SessionStart entrypoint
│   ├── auto-recall.mjs           # UserPromptSubmit entrypoint
│   ├── auto-capture.mjs          # Stop entrypoint
│   ├── uri-guard.mjs             # PreToolUse viking:// guard
│   ├── zcode-hook.mjs            # main hook logic (thin adapter over shared runtime)
│   ├── zcode-turns.mjs           # turn builder + injected-block cleaner
│   └── zcode-hooks.test.mjs      # node --test contract + e2e hook tests
├── servers/
│   └── mcp-proxy.mjs             # stdio -> OpenViking /mcp bridge
├── setup-helper/
│   └── install.sh                # self-contained installer
├── DESIGN.md
├── INSTALL.md
├── INSTALL-ZH.md
└── README.md
```

No `package.json` or build step: hook scripts and the MCP proxy are zero-dependency `.mjs` files running on a system Node.js 18+.

## Configuration

Connection and identity are resolved by the shared runtime in this order:

1. `OPENVIKING_CREDENTIAL_SOURCE=env` forces `OPENVIKING_URL` / `OPENVIKING_API_KEY` / `OPENVIKING_ACCOUNT` / `OPENVIKING_USER` / `OPENVIKING_PEER_ID`.
2. Active `~/.openviking/ovcli.conf` when present (`url`, `api_key`, `account`, `user`, `actor_peer_id`).
3. `~/.openviking/ov.conf` legacy fallback, then `http://127.0.0.1:1933` unauthenticated.

Auth is sent as `Authorization: Bearer <api_key>` to both the REST API (hooks) and the `/mcp` endpoint (model). By default a peer is derived from the current workspace path; set `OPENVIKING_WORKSPACE_PEER=0` to disable, or set `OPENVIKING_PEER_ID` to override.

### Tuning

| Env var | Default | Meaning |
|---|---|---|
| `OPENVIKING_MEMORY_ENABLED` | `1` | Master switch for all hooks |
| `OPENVIKING_AUTO_RECALL` | `1` | Set `0` to skip `UserPromptSubmit` recall |
| `OPENVIKING_AUTO_CAPTURE` | `1` | Set `0` to skip `Stop` capture |
| `OPENVIKING_RECALL_LIMIT` | `6` | Max recall candidates per prompt |
| `OPENVIKING_RECALL_TOKEN_BUDGET` | `2000` | Soft char budget for the recall digest |
| `OPENVIKING_COMMIT_TURN_THRESHOLD` | `8` | Reserved for batched commit (ZCode commits every turn) |
| `OPENVIKING_TIMEOUT_MS` | `15000` | Per-request timeout |
| `OPENVIKING_DEBUG` | `0` | Enable structured JSONL debug log |
| `OPENVIKING_DEBUG_LOG` | `~/.openviking/logs/zcode-hooks.log` | Debug log path |

## MCP Tools

The stdio proxy forwards OpenViking's real `tools/list` response. Current OpenViking servers expose: `recall`, `search`, `find`, `remember`, `read`, `list`, `grep`, `glob`, `add_resource`, `forget`, `list_watches`, `cancel_watch`, `code_search`, `code_outline`, `code_expand`, `health`. Tool names are namespaced by ZCode per its MCP client convention.

## License

Apache-2.0 — same as [OpenViking](https://github.com/volcengine/OpenViking).
