# OpenViking Memory for thin hook hosts

Cursor, TRAE, TRAE CN and ZCode use host configuration files. Kimi Code uses its native managed-plugin directory, CodeBuddy Code and Qoder CLI a local plugin marketplace, and Antigravity a plugin directory under `~/.gemini/config/plugins/`. They share the same dispatcher and memory runtime; the installer assembles the runtime at install time rather than committing a copy per host.

```bash
curl -fsSL https://openviking.ai/install | bash
```

The installer asks which harnesses to install and for your OpenViking credentials. To install from a repository checkout instead, run `bash examples/memory-plugin-shared/install.sh --harness <cursor|trae|trae-cn|zcode|kimicode|codebuddy|qoder|antigravity>` from its root.

> **Requires an OpenViking server with `viking://~` home-alias support.** Recall targets the caller's own context space through `viking://~/memories` and `viking://~/skills`; the uid-less `viking://user/memories` shorthand is rejected by newer servers.

## What the hooks do

- **Session start** — injects the user profile and preferences, plus an `<available-skills>` catalog of the user's own and account-shared OpenViking skills, and replays anything an offline session queued. Kimi replays a bounded batch at startup and injects the profile on the first successful prompt hook because its SessionStart output is not a context channel.
- **Prompt submit** — searches OpenViking for memories and skills relevant to the prompt and injects them, deduplicated by event id and a 500ms window.
- **Tool use** — denies local file tools a `viking://` virtual path and points the agent back at the OpenViking MCP tools. On TRAE and Qoder CLI a shell command that carries a `viking://` URI still runs, with a notice pointing at the same tools.
- **Stop** — captures the finished turn and commits the OpenViking session. Cursor also runs this before a compaction and at session end; ZCode answers first and finishes the writes in a detached worker.
- **After a compaction** — adds a short `<openviking-context source="session-history">` note naming the session's OpenViking id and how to look up exact details with the server's `session_history` MCP tool. Qoder answers SessionStart after a compaction or a resume with it and TRAE answers PostCompact; Cursor, CodeBuddy and Kimi Code note the compaction at PreCompact (CodeBuddy also at SessionStart after a compaction, CodeBuddy and Kimi Code also on a resume) and add it to the next prompt. A resume adds it only to a session that has not had one yet, since the resumed transcript still holds the earlier note. ZCode has no compaction signal and Antigravity no OpenViking MCP server, so neither gets it. When the model leaves the tool's `session` out, Cursor's `preToolUse` and Qoder's `PreToolUse` hooks rewrite the call with it, and the MCP proxy fills it on CodeBuddy and TRAE from the call's `_meta`; on Kimi Code and TRAE the note asks the model to pass it. `OPENVIKING_SESSION_HISTORY_POINTER=0` or `sessionHistoryPointer: false` turns the note off.

## Layout

`scripts/hook.mjs` is the single entry every hook command runs. It owns the state machine all five clients share — the debounce, the prompt dedup, the recall cache, the cross-process lock — and asks the adapter under `hosts/` for the four things that differ: the event vocabulary, the response envelope, how a prompt is read out of the payload, and how a finished turn is captured. `scripts/uri-guard.mjs` and `servers/mcp-proxy.mjs` are likewise one file each, with the host chosen from the client id the installer passes.

The root `plugin.json` is host-neutral package metadata used for version checks and diagnostics. Kimi's native manifest lives under `hosts/kimicode/` and is copied to the root of its assembled installation.

`hosts/<host>/` holds host configuration or native-manifest files; executable adapters stay one level up. The `../../memory-plugin-shared/lib` import resolves in the repository, config-driven installs, and Kimi's assembled native bundle.

`examples/memory-plugin-shared/sync.mjs` copies the shared skills from `examples/skills`, `ov-memory-doctor` among them, into each `hosts/<host>/skills`.

The memory logic itself is not here: recall, batching, the pending queue, credential resolution and the MCP proxy all come from `examples/memory-plugin-shared/lib`, which the installer copies to `~/.openviking/agent-integrations/memory-plugin-shared/lib`.

## Host notes

- **Cursor** — eight events. `afterMCPExecution` appends OpenViking tool results to a local JSONL log; Stop uploads them with the text transcript. Text deduplication and the tool-upload position are separate. Only accepted messages advance capture progress, and the source records own retries. Commits when `capturedSinceCommit` reaches the threshold, or before compaction after capture completes. Sessions are `cu-`. See the [Cursor guide](../../docs/en/agent-integrations/12-cursor.md).
- **TRAE / TRAE CN** — capture reads `prompt`, `text_content` and `last_assistant_message` off the Stop event rather than parsing a transcript. Every Stop that carries content commits. The installer copies the five skills (`openviking-memory`, `openviking-skills`, `ov-experience-memory`, `openviking-rule-search`, `ov-memory-doctor`) into `~/.trae/skills` or `~/.trae-cn/skills` and keeps a skill the user edited, or an unmanaged directory of the same name, on upgrade and uninstall; TraeCode CLI also reads `~/.trae/skills`, so with its plugin installed too the CLI lists each skill twice. Sessions are `tr-` and `trcn-`. See the [TRAE guide](../../docs/en/agent-integrations/13-trae.md).
- **ZCode** — the rollout file is the authoritative incremental transcript: stable host `turnId` values drive deduplication and let a later Stop recover missed turns, and hook stdin is only the fallback. ZCode supports neither `PreCompact` nor `SessionEnd`, so committing on every Stop stands in for both. Its output schema is strict, so a pass-through writes nothing at all. The five skills go into `~/.zcode/skills`, tracked the same way as TRAE's. Sessions are `zc-`. [DESIGN.md](./DESIGN.md) records the verified extension surface.
- **Kimi Code** — `wire.jsonl` is the authoritative transcript. UserPromptSubmit emits raw context text, while Stop, PreCompact and SessionEnd may detach; Interrupt remains synchronous under a two-second OpenViking request budget. The installer creates a self-contained native plugin without editing legacy `config.toml` or `mcp.json`, and bundles the five skills under the plugin's `skills/`, declared in `kimi.plugin.json`. Sessions are `kc-`. [Host contract](./hosts/kimicode/DESIGN.md).
- **Antigravity** — the desktop app and the `agy` CLI read the same plugin under `~/.gemini/config/plugins/`. Antigravity runs PreInvocation before every model call, so recall runs only on a turn's first call (`invocationNum` 0), and context returns as an `injectSteps` system message. Stop carries no text: capture reads finished steps of `transcriptPath` after the last captured step, in a detached worker, and commits. PreToolUse denies a file tool whose location argument (a name ending in `Path`, `File` or `Directory`) is a `viking://` URI; `run_command` is not checked. The plugin directory also holds the five skills, each invocable as `/<name>`, and an `mcp_config.json` for the shared MCP proxy, which the installer leaves out when `~/.gemini/config/mcp_config.json` already defines an `openviking` server. Sessions are `ag-`. Install with `--harness antigravity`, which writes that plugin directory and leaves one it did not write in place; see the [community plugins guide](../../docs/en/agent-integrations/08-community-plugins.md#antigravity-memory-integration).

Cursor tool capture follows the [Hooks contract](https://cursor.com/docs/hooks) and the MCP executor in [`@cursor/sdk` 1.0.38](https://registry.npmjs.org/@cursor/sdk/-/sdk-1.0.38.tgz), which supplies `tool_use_id` on `afterMCPExecution`. Older events without that native identity retain text capture only; the adapter does not invent call IDs or completion evidence.

## Diagnostics

```bash
node ~/.openviking/agent-integrations/<client>/scripts/ov-memory-doctor.mjs --offline
```

`<client>` is `cursor`, `trae`, `trae-cn`, `zcode` or `antigravity`. Kimi Code, CodeBuddy Code and Qoder CLI run the copy inside their installed plugin:

```bash
node "${KIMI_CODE_HOME:-$HOME/.kimi-code}/plugins/managed/openviking-memory/agent-integrations/kimicode/scripts/ov-memory-doctor.mjs" --offline
node ~/.openviking/agent-integrations/codebuddy/marketplace/openviking-memory/agent-integrations/codebuddy/scripts/ov-memory-doctor.mjs --offline
node ~/.openviking/agent-integrations/qoder/marketplace/openviking-memory/agent-integrations/qoder/scripts/ov-memory-doctor.mjs --offline
```

Each copy checks the client it was installed for; pass `cursor`, `trae`, `trae-cn`, `zcode`, `antigravity`, `kimicode`, `codebuddy` or `qoder` as an argument to override it. For Kimi Code, CodeBuddy Code and Qoder CLI it also checks the plugin's manifest, hooks and MCP file and whether the host has loaded the plugin. Drop `--offline` to probe the server as well, and add `--json` for a machine-readable report. In a session, the `ov-memory-doctor` skill runs the same doctor for the host it runs in.

## Tests

```bash
node --test examples/agent-hook-plugin/tests/*.test.mjs
```
