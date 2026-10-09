# OpenViking memory plugin for the CodeBuddy Code CLI

Long-term semantic memory for [CodeBuddy Code](https://cnb.cool/codebuddy/codebuddy-code),
powered by [OpenViking](https://github.com/volcengine/OpenViking).

The plugin reads and writes OpenViking through lifecycle hooks, and exposes the
OpenViking tools through MCP. Both halves are configured from the same resolved
plugin configuration, so hooks and MCP always agree on the server, account and
user.

## Status

Built against CodeBuddy Code **v2.163.0**. The host contract was established by
running a throwaway probe plugin against a real CLI session, not by reading the
docs alone — see [`docs/HOST-CONTRACT.md`](docs/HOST-CONTRACT.md) for the
verified facts, the method, and the remaining unknowns. Notably:

- hooks fire in both the interactive TUI and headless (`-p`) runs, **but
  `UserPromptSubmit` and `SessionEnd` only fire in the interactive TUI**;
- the `additionalContext` envelope is byte-compatible with Claude Code, and
  unknown JSON fields are tolerated;
- `PreToolUse` has **no model-visible notice channel** — `permissionDecisionReason`
  never reached the transcript — so the URI guard can only `deny`;
- a hook that overruns its `timeout` is killed, but **its descendants are not**,
  which makes detached async writes safe.

## Layout

```
.codebuddy-plugin/plugin.json   manifest (only `name` is required)
hooks/hooks.json                hook registrations (${CODEBUDDY_PLUGIN_ROOT})
.mcp.json                       MCP server for this plugin          (P2)
servers/mcp-proxy.mjs           stdio MCP proxy onto the resolved configuration   (P2)
scripts/                        hand-written hook entrypoints
scripts/lib/                    hand-written, plugin-local helpers
scripts/shared/                 GENERATED from examples/memory-plugin-shared/lib — do not edit
skills/                         GENERATED from examples/skills      (P6)
docs/HOST-CONTRACT.md           verified host contract + method
```

Phase status: **P0 (host contract) and P1 (skeleton) done** — the plugin loads and
resolves its configuration; hook entrypoints, MCP, capture and the skills land in
P2–P6.

`scripts/shared/` and `skills/` are produced by
`node examples/memory-plugin-shared/sync.mjs` from the repository root. Edit the
shared library under `examples/memory-plugin-shared/lib/`, then re-run the
generator; never edit the generated copies.

## Loading it locally

```bash
codebuddy --plugin-dir examples/codebuddy-memory-plugin
```

`--plugin-dir` is session-scoped: nothing is written to any CodeBuddy
configuration, and dropping the flag plus removing the directory leaves no
trace. For a persistent install, add a local marketplace and install it into the
user scope.

## Configuration

Resolution order (shared with every OpenViking memory plugin):

```
OPENVIKING_* env vars → workspace .openviking/config*.json → ovcli.conf `plugin.codebuddy`
→ ovcli.conf `plugin` → ov.conf `codebuddy` section → schema defaults
```

Connection and credentials always come from `OPENVIKING_URL` /
`OPENVIKING_API_KEY` (or `OPENVIKING_BEARER_TOKEN`), then `ovcli.conf`, then
`ov.conf`'s `server` section.

Enable/disable: `OPENVIKING_MEMORY_ENABLED=0|1`, or `codebuddy.enabled` in
`ov.conf`. Without either, the plugin is enabled only when `ov.conf` or
`ovcli.conf` exists.

Debug logging: `OPENVIKING_DEBUG=1` (or `codebuddy.debug: true`) writes JSON
Lines to `~/.openviking/logs/cb-hooks.log` (override with
`OPENVIKING_DEBUG_LOG`).

## Naming

The harness id is **`codebuddy`**. OpenViking's server-side log ingestion ships a
`workbuddy` adapter that parses this same transcript format, but that name
belongs to the server's ingest namespace — it is not a harness id and must not be
registered in `HARNESS_KEYS`.
