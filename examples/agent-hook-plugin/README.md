# OpenViking Memory for the config-driven hook hosts

Cursor, TRAE, TRAE CN and ZCode all install the same way: the shared installer writes lifecycle hooks and an MCP server entry into the host's own configuration files, and assembles the OpenViking runtime beside the integration. None of them has a marketplace listing to register or a separate MCP setup to do.

```bash
bash examples/memory-plugin-shared/install.sh --harness cursor
bash examples/memory-plugin-shared/install.sh --harness trae,trae-cn
bash examples/memory-plugin-shared/install.sh --harness zcode
```

> **Requires an OpenViking server with `viking://~` home-alias support.** Recall targets the caller's own context space through `viking://~/memories` and `viking://~/skills`; the uid-less `viking://user/memories` shorthand is rejected by newer servers.

## What the hooks do

- **Session start** — injects the user profile and preferences into context, and replays anything an offline session queued.
- **Prompt submit** — searches OpenViking for memories relevant to the prompt and injects them, deduplicated by event id and a 500ms window.
- **Tool use** — denies `viking://` virtual paths to local file and shell tools, and points the agent back at the OpenViking MCP tools.
- **Stop** — captures the finished turn and commits the OpenViking session. Cursor also runs this before a compaction and at session end; ZCode answers first and finishes the writes in a detached worker.

## Layout

`scripts/hook.mjs` is the single entry every hook command runs. It owns the state machine all four clients share — the debounce, the prompt dedup, the recall cache, the cross-process lock — and asks the adapter under `hosts/` for the four things that differ: the event vocabulary, the response envelope, how a prompt is read out of the payload, and how a finished turn is captured. `scripts/uri-guard.mjs` and `servers/mcp-proxy.mjs` are likewise one file each, with the host chosen from the client id the installer passes.

`hosts/<host>/` holds only what a host reads as configuration — `hooks.json`, `.mcp.json`, `openviking.integration.json`, plus Cursor's rule and skill. Everything executable stays one level up, because `../../memory-plugin-shared/lib` is the path that resolves both in this repository and in an installed `~/.openviking/agent-integrations/<client>/`.

The memory logic itself is not here: recall, batching, the pending queue, credential resolution and the MCP proxy all come from `examples/memory-plugin-shared/lib`, which the installer copies to `~/.openviking/agent-integrations/memory-plugin-shared/lib`.

## Host notes

- **Cursor** — seven events, including the `preCompact` and `sessionEnd` no other host in this plugin has. Commits on Stop once `capturedSinceCommit` reaches the threshold, and unconditionally before a compaction. Sessions are `cu-`. See the [Cursor guide](../../docs/en/agent-integrations/12-cursor.md).
- **TRAE / TRAE CN** — capture reads `prompt`, `text_content` and `last_assistant_message` off the Stop event rather than parsing a transcript. Every Stop that carries content commits. Sessions are `tr-` and `trcn-`. See the [TRAE guide](../../docs/en/agent-integrations/13-trae.md).
- **ZCode** — the rollout file is the authoritative incremental transcript: stable host `turnId` values drive deduplication and let a later Stop recover missed turns, and hook stdin is only the fallback. ZCode supports neither `PreCompact` nor `SessionEnd`, so committing on every Stop stands in for both. Its output schema is strict, so a pass-through writes nothing at all. Sessions are `zc-`. [DESIGN.md](./DESIGN.md) records the verified extension surface.

## Diagnostics

```bash
node ~/.openviking/agent-integrations/<client>/scripts/ov-memory-doctor.mjs --offline
```

The client defaults to the one this copy was installed for; pass `cursor`, `trae`, `trae-cn` or `zcode` as an argument to override it, drop `--offline` to probe the server as well, and add `--json` for a machine-readable report.

## Tests

```bash
node --test examples/agent-hook-plugin/tests/*.test.mjs
```
