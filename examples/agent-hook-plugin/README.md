# OpenViking Memory for thin hook hosts

Cursor, TRAE, TRAE CN and ZCode use host configuration files. Kimi Code uses its native managed-plugin directory. They share the same dispatcher and memory runtime; the installer assembles the runtime at install time rather than committing a copy per host.

```bash
bash examples/memory-plugin-shared/install.sh --harness cursor
bash examples/memory-plugin-shared/install.sh --harness trae,trae-cn
bash examples/memory-plugin-shared/install.sh --harness zcode
bash examples/memory-plugin-shared/install.sh --harness kimicode
```

> **Requires an OpenViking server with `viking://~` home-alias support.** Recall targets the caller's own context space through `viking://~/memories` and `viking://~/skills`; the uid-less `viking://user/memories` shorthand is rejected by newer servers.

## What the hooks do

- **Session start** — injects the user profile and preferences, plus an `<available-skills>` catalog of the user's own and account-shared OpenViking skills, and replays anything an offline session queued. Kimi replays a bounded batch at startup and injects the profile on the first successful prompt hook because its SessionStart output is not a context channel.
- **Prompt submit** — searches OpenViking for memories and skills relevant to the prompt and injects them, deduplicated by event id and a 500ms window.
- **Tool use** — denies local file tools a `viking://` virtual path and points the agent back at the OpenViking MCP tools. On TRAE a shell command that carries a `viking://` URI still runs, with a notice pointing at the same tools.
- **Stop** — captures the finished turn and commits the OpenViking session. Cursor also runs this before a compaction and at session end; ZCode answers first and finishes the writes in a detached worker.

## Layout

`scripts/hook.mjs` is the single entry every hook command runs. It owns the state machine all five clients share — the debounce, the prompt dedup, the recall cache, the cross-process lock — and asks the adapter under `hosts/` for the four things that differ: the event vocabulary, the response envelope, how a prompt is read out of the payload, and how a finished turn is captured. `scripts/uri-guard.mjs` and `servers/mcp-proxy.mjs` are likewise one file each, with the host chosen from the client id the installer passes.

The root `plugin.json` is host-neutral package metadata used for version checks and diagnostics. Kimi's native manifest lives under `hosts/kimicode/` and is copied to the root of its assembled installation.

`hosts/<host>/` holds host configuration or native-manifest files; executable adapters stay one level up. The `../../memory-plugin-shared/lib` import resolves in the repository, config-driven installs, and Kimi's assembled native bundle.

The memory logic itself is not here: recall, batching, the pending queue, credential resolution and the MCP proxy all come from `examples/memory-plugin-shared/lib`, which the installer copies to `~/.openviking/agent-integrations/memory-plugin-shared/lib`.

## Host notes

- **Cursor** — six events, including the `preCompact` and `sessionEnd` no other host in this plugin has. Commits on Stop once `capturedSinceCommit` reaches the threshold, and unconditionally before a compaction. Sessions are `cu-`. See the [Cursor guide](../../docs/en/agent-integrations/12-cursor.md).
- **TRAE / TRAE CN** — capture reads `prompt`, `text_content` and `last_assistant_message` off the Stop event rather than parsing a transcript. Every Stop that carries content commits. Sessions are `tr-` and `trcn-`. See the [TRAE guide](../../docs/en/agent-integrations/13-trae.md).
- **ZCode** — the rollout file is the authoritative incremental transcript: stable host `turnId` values drive deduplication and let a later Stop recover missed turns, and hook stdin is only the fallback. ZCode supports neither `PreCompact` nor `SessionEnd`, so committing on every Stop stands in for both. Its output schema is strict, so a pass-through writes nothing at all. Sessions are `zc-`. [DESIGN.md](./DESIGN.md) records the verified extension surface.
- **Kimi Code** — `wire.jsonl` is the authoritative transcript. UserPromptSubmit emits raw context text, while Stop, PreCompact and SessionEnd may detach; Interrupt remains synchronous under a two-second OpenViking request budget. The installer creates a self-contained native plugin without editing legacy `config.toml` or `mcp.json`. Sessions are `kc-`. [Host contract](./hosts/kimicode/DESIGN.md).

## Diagnostics

```bash
node ~/.openviking/agent-integrations/<client>/scripts/ov-memory-doctor.mjs --offline
```

For Kimi's managed plugin, use its installed path instead:

```bash
node "${KIMI_CODE_HOME:-$HOME/.kimi-code}/plugins/managed/openviking-memory/agent-integrations/kimicode/scripts/ov-memory-doctor.mjs" kimicode --offline
```

For config-driven copies, the client defaults to the one that copy was installed for; pass `cursor`, `trae`, `trae-cn` or `zcode` as an argument to override it. Drop `--offline` to probe the server as well, and add `--json` for a machine-readable report.

## Tests

```bash
node --test examples/agent-hook-plugin/tests/*.test.mjs
```
