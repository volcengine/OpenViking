# Codex Memory Plugin

Give [Codex](https://developers.openai.com/codex) cross-session memory. Hooks handle automatic recall, capture, and session commits; MCP tools let the model search, read, and manage memories.

Source: [examples/codex-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/codex-memory-plugin) | [Blog: Motivation & demo](https://blog.openviking.ai/post/openviking-coding-agent/)

## Install

Claude Code and Codex share one installer. It asks for your language (English/中文), which harnesses to install, the download source, and your OpenViking credentials; the installation steps support repeated runs.

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh)
```

TraeCode CLI 2.0 accepts this Codex-format plugin directly. Its default
installer entry is `--harness trae-cli`:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness trae-cli
```

In regions where GitHub is hard to reach, run the same installer from the Volcengine TOS mirror (or pick "TOS mirror" at the download-source prompt). Codex installs from a TOS-hosted git repo and keeps remote update support:

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh)
```

No shell wrapper is needed anymore — the plugin ships a stdio MCP proxy that reads `~/.openviking/ovcli.conf` (or `OPENVIKING_*` env vars) at runtime, same as the hooks. After installing, launch Codex (`trae-cli` for TraeCode CLI 2.0):

```bash
codex
```

### First launch: trust the hooks

Installing the plugin does not automatically trust its hooks. On first launch Codex shows a review prompt similar to the one below; the exact layout varies by Codex version. Pick **Trust all and continue**, or **Review hooks** to read the commands first. *Continue without trusting* leaves the hooks off until you enable them in `/hooks`.

```text
Hooks need review
6 hooks are new or changed.
Hooks can run outside the sandbox after you trust them.

  1. Review hooks
> 2. Trust all and continue
  3. Continue without trusting (hooks won't run)
```

You can also open `/hooks`, review the OpenViking commands, and trust and enable the hooks you intend to use. In `/plugins`, also confirm that `openviking-memory` is enabled. The plugin currently declares six hooks. New or changed definitions require another review. See the [official hook trust documentation](https://learn.chatgpt.com/docs/hooks#review-and-trust-hooks).

MCP tools can work while hooks are disabled. Automatic recall requires `UserPromptSubmit`; capture requires `Stop`, with lifecycle commits handled by the other hooks listed below. If setup was skipped, return to `/hooks` to enable the relevant entries.

<details>
<summary><b>Manual setup</b></summary>

Prerequisites: Node.js >= 22, Codex >= 0.130.0, and the `plugin_hooks` feature enabled.

1. **Configure the connection** — write `~/.openviking/ovcli.conf` (`url`, `api_key`, optional `account`/`user`), or run the bundled wizard `node <plugin-dir>/scripts/setup.mjs` after installing.

2. **Install the plugin** from the remote marketplace:

   ```bash
   codex plugin marketplace add volcengine/OpenViking
   codex plugin add openviking-memory@openviking
   ```

   Then enable plugin hooks in `~/.codex/config.toml` if your build doesn't already: `[features]` → `plugin_hooks = true`. Update later with `codex plugin marketplace upgrade openviking`.

</details>

## Verify

Launch `codex`; on the first prompt of a session, the `SessionStart` hook should load your profile, and the plugin should then recall relevant memories for every prompt. Set `OPENVIKING_DEBUG=1` to write events to `~/.openviking/logs/codex-hooks.log`.
For TraeCode CLI 2.0, launch `trae-cli` and use `trae-cli plugin list` to confirm the plugin is enabled.

## How it works

The plugin handles memory at these Codex lifecycle events:

| Codex event | Plugin behavior |
| --- | --- |
| `SessionStart` (`startup`, `clear`, `resume`) | Inject `profile.md`, URI and abstract indexes for `preferences/` and `entities/`, and the `<available-skills>` catalog. It uses the shared CJK-aware profile builder; resumed sessions may also receive the latest archive digest. |
| `UserPromptSubmit` | Retrieve and inject memories relevant to the current prompt. |
| `Stop` | Append new conversation turns to the OpenViking session. |
| `PreCompact` | Capture remaining turns and commit the complete transcript before compaction. |
| `SessionEnd` | Commit the session on graceful exit for subsequent memory extraction. |
| `PreToolUse` (`Bash`) | Detect `viking://` URIs in shell commands and suggest the OpenViking MCP tools. The command still runs; the model can ignore the notice when the URI is intentional data, such as an `ov` argument. |

Starting a new session also sweeps orphaned sessions from earlier runs once their idle TTL has expired.

> **Known limitation**: `SessionEnd` requires Codex 0.145 or newer, and it only fires on a graceful exit (`/quit`, `/exit`, double `Ctrl-C`, EOF, end of a `codex exec` run). It does not fire on `SIGTERM`, a closed terminal, `kill -9`, or a crash, and it is deferred when the TUI runs against a `codex app-server` daemon. Those sessions — and every session on Codex older than 0.145, and any TraeCode CLI build without it — are recovered by the idle-TTL sweep (30 minutes) at the next `SessionStart`.

The `<available-skills>` catalog lists your own skills first, then the skills shared with the account under `viking://agent/skills`; a shared skill with the same name as one of yours is left out. It has its own token budget, separate from the profile budget: when the descriptions do not fit, it lists names only, and when not even one name fits, it shrinks to a one-line count. Its first line tells the model to read a skill's `SKILL.md` with the OpenViking `read` tool before following it. The bundled `openviking-skills` skill, next to `openviking-memory` and `ov-experience-memory`, tells the model how to find skills, create or replace one with the MCP `add_skill` tool, install one from Git or a local folder, share one with the account, and move local skills into OpenViking when you ask.

Tool calls and results are captured as dedicated `tool` parts, and `tool_output` is reported verbatim. Truncation is the server's job: output larger than `tool_output_externalization.threshold_chars` (default `20000`) is written to the session's tool-result store, and the part keeps a synopsis stub plus `tool_output_ref`, so the original stays readable through [`/api/v1/sessions/{id}/tool-results`](../api/05-sessions.md#read-tool-result).

<details>
<summary><b>Configuration</b></summary>

Credential source: env vars win by default — when any `OPENVIKING_*` credential env var (`OPENVIKING_URL`/`OPENVIKING_BASE_URL`, `OPENVIKING_BEARER_TOKEN`/`OPENVIKING_API_KEY`, `OPENVIKING_ACCOUNT`, `OPENVIKING_USER`, `OPENVIKING_PEER_ID`) is set, its value takes precedence over the active `ovcli.conf`. Only when none of them are set does the active `ovcli.conf` (`OPENVIKING_CLI_CONFIG_FILE` or `~/.openviking/ovcli.conf`) drive hooks, MCP proxy, and child `ov` commands together, so `ov config switch <name>` takes effect on the next launch. Set `OPENVIKING_CREDENTIAL_SOURCE=cli` to force the active ovcli config even while credential env vars are present. Fields not covered by either fall back to `ovcli.conf`, then `ov.conf`, then built-in defaults.

| Env Var | Default | Description |
|---------|---------|-------------|
| `OPENVIKING_URL` / `OPENVIKING_BASE_URL` | — | Full server URL |
| `OPENVIKING_API_KEY` | — | API key (sent as `Authorization: Bearer`) |
| `OPENVIKING_CLI_CONFIG_FILE` | `~/.openviking/ovcli.conf` | Active CLI config to use for hooks, MCP, and child `ov` commands |
| `OPENVIKING_CREDENTIAL_SOURCE` | `auto` | `auto` prefers env-var credentials when any are set; `cli` forces the active ovcli config; `env` reads env vars only, and neither config file |
| `OPENVIKING_NO_AUTO_INJECT` | `false` | Disable fixed session-start profile/background injection, including the skill catalog, without disabling per-prompt recall |
| `OPENVIKING_PROFILE_TOKEN_BUDGET` | `10000` | CJK-aware token budget for `profile.md` plus `preferences/` and `entities/` indexes |
| `OPENVIKING_SKILL_CATALOG` | `true` | Add the `<available-skills>` catalog to the session-start block; `false` leaves it out |
| `OPENVIKING_SKILL_CATALOG_TOKEN_BUDGET` | `1200` | CJK-aware token budget for the `<available-skills>` catalog, separate from `OPENVIKING_PROFILE_TOKEN_BUDGET`; `0` also leaves the catalog out |
| `OPENVIKING_SESSION_START_MAX_BYTES` | `9500` | Byte cap on the whole SessionStart context, kept under Codex's default hook-output limit (about 10,000 bytes) so the model sees it in full rather than a truncated preview; on resume the session archive takes up to half. `0` removes the cap |
| `OPENVIKING_CODEX_IDLE_TTL_MS` | `1800000` | SessionStart idle-TTL sweep threshold |
| `OPENVIKING_CODEX_LOCK_WAIT_MS` | `120000` (SessionEnd), `40000` (PreCompact) | How long a capture hook waits for the per-session state lock |
| `OPENVIKING_CODEX_COMMITTED_TTL_MS` | `2592000000` | How long a committed session's transcript cursor is kept before its state file is retired |
| `OPENVIKING_RECALL_QUERY_FILTERS` | `""` | CSV of sed-style regex rules applied to the prompt before it becomes a query ([grammar and examples](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md#input-filters)) |
| `OPENVIKING_CAPTURE_FILTERS` | `""` | CSV of sed-style regex rules applied to every captured turn (same grammar) |
| `OPENVIKING_DEBUG` | `false` | Write logs to `~/.openviking/logs/codex-hooks.log` |

Most of these knobs can also live in `ovcli.conf` under `plugin` — see [Plugin Settings](../configuration/02-client.md#plugin-settings). The two filter knobs are better written there, as JSON arrays, because the environment form is split on commas.

If recall latency matters most, see [Low-latency recall](./01-overview.md#low-latency-recall) for the environment-variable and `ovcli.conf` settings that disable query expansion and Codex's local result compression.

Additional tuning options (e.g., `OPENVIKING_RECALL_LIMIT`, `OPENVIKING_CAPTURE_ASSISTANT_TURNS`) are documented in the [plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md#tuning-the-plugin).

</details>

## Workspace peer

Memories are filed under a peer derived from the repository you are working in, so one project keeps one memory across clones, worktrees, and subdirectories. The default `peer.source: "git"` uses the repository's normalized `origin` URL — with `origin git@github.com:volcengine/OpenViking.git`, the peer is `github.com-volcengine-openviking` — falling back to the repository root path; outside a repository no peer is sent at all, and what is remembered there goes to your user-level space at `viking://user/<you>/memories`. A fork has its own `origin`, so it stays a separate peer.

Change it with `OPENVIKING_PEER_SOURCE`, with `plugin.peerSource` in `ovcli.conf`, or with `peer.source` in the workspace's `.openviking/config.json` (a `"version": 1` file the team can commit): `"cwd"` restores the previous behavior — the working directory with every non-alphanumeric character replaced by `-` — `"none"` sends no peer, and a template such as `"team-{dir}"` builds your own. To [give a directory that is not a repository its own memory](../configuration/02-client.md#give-a-directory-its-own-peer), create `.openviking/config.json` in it containing `{"version": 1, "peer": {"id": "my-project"}}`. Memories written under the earlier cwd-derived peer are still recalled, so nothing needs migrating. The layer precedence and the full workspace-file schema are in [Client Configuration → Workspace Configuration](../configuration/02-client.md#workspace-configuration).

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| MCP tool calls fail with an auth error | The active ovcli config has no valid `api_key` for an authenticated server | Fix `~/.openviking/ovcli.conf` (or run `node <plugin-dir>/scripts/setup.mjs`) and restart Codex; the stdio proxy re-reads it on launch and after auth failures. |
| MCP tool calls fail with a connection error | Server unreachable or the URL is wrong | Check the endpoint: `curl "$(jq -r '.url' ~/.openviking/ovcli.conf)/health"` |
| Hook review warning, or installed plugin with no recall/capture | Required hooks are untrusted, disabled, or the plugin is disabled | Review and enable the relevant entries in `/hooks`, then confirm `openviking-memory` in `/plugins`. |
| Plugin still targets an old server after `ov config switch` | The existing proxy retains its connection, or credential env vars override the selected config | Restart Codex and check credential env vars and `OPENVIKING_CLI_CONFIG_FILE`. Remote calls reload changed files after an auth failure, not on every successful request. |
| Hooks use one server, MCP another | Stale `OPENVIKING_*` credential env vars in one context (env vars override ovcli.conf by default) | Unset the stale env vars (ovcli.conf then drives both), set `OPENVIKING_CREDENTIAL_SOURCE=cli`, or make the env vars consistent. |

## See also

- [Capability Reference](./16-capability-reference.md)
- [Blog: OpenViking in Claude Code / Codex](https://blog.openviking.ai/post/openviking-coding-agent/) — Motivation, architecture overview, and demo.
- [Plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md) — Full environment variable list and architecture diagram.
- [DESIGN.md](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/DESIGN.md) — Commit decision tree.
- [MCP Clients](./06-mcp-clients.md) — MCP protocol, tools, and other clients.
- [Deployment Guide → CLI](../guides/03-deployment.md#cli) — `ovcli.conf` setup instructions.

### Recall compression

Set `OPENVIKING_RECALL_COMPRESS=server` to compress recalled context on the OpenViking server without launching a local Codex compressor. `client` uses local compression only; `auto` (the default) uses the server when the local compressor is unavailable; `off` disables compression. Existing server digests are used directly, and an explicit no-relevant result injects nothing.

Codex calls the shared `buildRecallBlockDetailed()` pipeline for retrieval, ranking, budgets and old-server fallback. Only session mapping, model execution and hook output remain host-specific. Local compression failures preserve bounded retrieved context. Without local compression, raw fallback now honors `recallPreferAbstract` instead of always reading every leaf in full. Budgets include body text, URIs and wrapper text. See the [shared plugin configuration](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/README.md#cloud-recall-compression).
