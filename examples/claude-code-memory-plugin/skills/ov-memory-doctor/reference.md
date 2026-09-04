# OpenViking Memory Doctor — reference

Companion to SKILL.md: where things live, what the exact error strings mean,
and the symptom catalogue. Paths assume the defaults; `OPENVIKING_HOME`,
`OPENVIKING_CONFIG_FILE`, `OPENVIKING_CLI_CONFIG_FILE`, `OPENVIKING_DEBUG_LOG`
and `OPENVIKING_PENDING_DIR` relocate individual pieces.

## Where things live

| Path | What |
|---|---|
| `~/.openviking/ovcli.conf` | Client connection: `url`, `api_key`, `account`, `user`, optional `plugin.claude_code.*` tuning. Mode 0600. |
| `~/.openviking/ov.conf` | Server config. The plugin reads only `server.url/host/port`, `server.root_api_key` (last-resort key) and the legacy `claude_code` block. |
| `~/.openviking/ovcli.conf.<name>` | Saved CLI profiles (`ov config switch` copies one over `ovcli.conf`). `ovcli.conf.bak.<epoch>` are installer backups, not profiles. |
| `<repo root>/.openviking/config.json` / `config.local.json` | Workspace config layers, `version: 1` required: `peer.source`, `peer.id`, `recall.*`, `capture.*`, `bypass.session_patterns`, `labels`. `config.json` is committed and shared; `config.local.json` is private and gitignored. Trusted without a prompt, but connection and credential keys (`url`, `api_key`, `account`, `user`, `extra_headers`, …) are stripped with a warning and `${VAR}` is never expanded. A blanket `.openviking/` rule in `.gitignore` stops `config.json` from ever being committed — narrow it to `.openviking/media/` and `.openviking/downloads/`. |
| `~/.openviking/workspaces/<slot>.json` | Per-machine workspace registry, one file per workspace (`<dir name>-<hash>.json`, mode 0600). Outranks both workspace files, and nothing writes it — a user creates the file by hand. An entry recorded for a different repository is ignored, not inherited. |
| `~/.claude/plugins/installed_plugins.json` | Install registry: `plugins["openviking-memory@openviking"][0].installPath/version/lastUpdated`. |
| `~/.claude/plugins/known_marketplaces.json` | Marketplace `openviking` → `source` (`directory` path or `github`), `installLocation`. |
| `~/.claude/plugins/cache/openviking/openviking-memory/<version>/` | The copy Claude Code actually runs. Keyed by `plugin.json` version. |
| `~/.claude/settings.json` | `enabledPlugins`, `statusLine`, optional `env`, legacy `hooks`. |
| `~/.openviking/marketplaces/openviking-claude/` | Installer-generated directory marketplace (GitHub dist) whose manifest points at `git-subdir` `examples/claude-code-memory-plugin`. |
| `~/.openviking/memory-plugin-marketplace/` | Unpacked TOS archive (TOS dist); cannot self-update. |
| `~/.openviking/state/` | `last-recall.json`, `last-capture.json`, `last-session-event.json`, `daily-stats.json`, `server-probe.json`, `host-cli-probe.json`, `context-face.json`, `recall-digest.json`, `ws-peer-<session>.json`, `ws-identity-<hash>.json` (60s cache of the git identity behind the peer). Anything else there is residue. |
| `~/.openviking/last_inject.md` | Full text of the last SessionStart injection. |
| `~/.openviking/logs/cc-hooks.log` | JSONL hook + proxy log; written only when `OPENVIKING_DEBUG=1` or `debug: true`. |
| `~/.openviking/pending/` | Offline write queue (retryable failures only); drained at SessionStart after `/health` passes. |
| `$TMPDIR/openviking-cc-capture-state/<session>.json` | Capture cursor (`capturedTurnCount`). macOS purges `$TMPDIR`, which forces a re-push. |

## Config resolution

| Field | Order |
|---|---|
| url | `OPENVIKING_URL` → `OPENVIKING_BASE_URL` → `ovcli.conf url` → `ov.conf server.url` → `http://{server.host\|127.0.0.1}:{server.port\|1933}` |
| api_key | `OPENVIKING_BEARER_TOKEN` → `OPENVIKING_API_KEY` → `ovcli.conf api_key` → `ov.conf claude_code.apiKey` → `ov.conf server.root_api_key` |
| account / user | `OPENVIKING_ACCOUNT` / `OPENVIKING_USER` → `ovcli.conf account/user` → `ov.conf claude_code.accountId/userId` |
| peer | `OPENVIKING_PEER_ID` → registry → `config.local.json` → `config.json` (`peer.id`) → `ovcli.conf plugin.claude_code.peerId` → `ovcli.conf plugin.peerId` → `ov.conf claude_code.peerId` → derived per `peer.source` unless `OPENVIKING_WORKSPACE_PEER=0` |
| peer.source | `OPENVIKING_PEER_SOURCE` → registry → `config.local.json` → `config.json` → `ovcli.conf plugin.claude_code.peerSource` → `ovcli.conf plugin.peerSource` → `ov.conf claude_code.peerSource` → `git` |
| enabled | `OPENVIKING_MEMORY_ENABLED` → `ov.conf claude_code.enabled === false` → "ov.conf or ovcli.conf exists and parses" |
| tuning | env → registry → `config.local.json` → `config.json` → `ovcli.conf plugin.claude_code.*` → `ovcli.conf plugin.*` → `ov.conf claude_code.*` → defaults |

Only trailing slashes are stripped from the url; no scheme check, no path
normalisation. `https://api.vikingdb.cn-beijing.volces.com/openviking` (the Volcengine-hosted OpenViking Service)
is a legitimate path prefix; `/api/v1` or `/mcp` suffixes are not.

`peer.source` decides the derivation. `git` (the default) is the template list `["{git_remote}", "{git_root}"]`: the normalized origin URL (`git@github.com:volcengine/OpenViking.git` → `github.com-volcengine-openviking`, userinfo dropped so an embedded token can never reach the id), else the repository root path (the legacy `[^A-Za-z0-9] → -` rule), else nothing — outside a git repository no peer is sent at all, and what is remembered there goes to the user-level space. `{cwd}` is still a variable but sits in no default chain, and `{dir}` is the workspace root's directory name, empty when the directory is not a workspace. `cwd` is that legacy rule alone (the pre-git behaviour); `none` sends no peer, which is also what `OPENVIKING_WORKSPACE_PEER=0` means. Anything else is a template, or a list of templates tried in order (`"git-{git_remote}"`, `["team-{dir}", "{cwd}"]`); a template naming an empty variable falls through to the next. Derivation is filesystem-only — no `git` subprocess — so it survives a missing `git` and a dubious-ownership refusal; worktrees converge through `commondir`, a submodule keeps its own identity, and `$HOME` and `/` are never workspace roots. Every clone of one repository shares one peer; a fork's origin differs, so it stays separate, and `gh pr checkout` does not change origin.

Peers minted before this need no migration: the old cwd-derived id is recomputed locally, `peer_scope: "all"` already sweeps it, and under `"actor"` recall asks it separately. Doctor prints it as `previous peer`. Across the workspace layers lists union rather than replace, with a leading `"!reset"` dropping what the lower layers contributed; unknown keys are kept and ignored, and a file declaring a version other than `1` is skipped with a warning.

Sent headers: `Authorization: Bearer <key>`, `X-OpenViking-Account`,
`X-OpenViking-User`, `X-OpenViking-Actor-Peer`, `User-Agent: openviking-memory-claude-code/<version>`.
The open-source server also accepts `X-API-Key` (and prefers it when both are
sent); the Volcengine-hosted OpenViking Service (`https://api.vikingdb.cn-beijing.volces.com/openviking`) accepts Bearer only.

## Peer: giving a directory its own memory

```json
{"version": 1, "peer": {"id": "my-project"}}
```

Stored as `.openviking/config.json` in the directory to be named; that directory and everything below it then writes to that peer, repository or not.

| What the user says | What to do |
|---|---|
| "make this folder remember separately" | Write `peer.id` in `.openviking/config.json` in that folder |
| "these two repos should share memory" | Write the same `peer.id` in `.openviking/config.json` in both |
| "only recall this project's memories" | Set `recall.peer_scope` to `"actor"` in the same file |
| "go back to the old per-directory behaviour" | `peer.source: "cwd"` in the same file, or `OPENVIKING_PEER_SOURCE=cwd` for every directory on this machine |
| "do not separate by project at all" | `peer.source: "none"` in the same file |

That file and the registry file whose path the doctor prints are the whole surface. No `ov` subcommand creates, renames or merges a peer. Changing the id moves no existing memory: the cross-peer sweep under `peer_scope: "all"` still reaches what was written before, and under `"actor"` the extra query still reaches the pre-git cwd-derived id.

## Server auth modes (from `GET /health` → `auth_mode`)

| Mode | Credential | Identity |
|---|---|---|
| `dev` | none needed, any key accepted | `X-OpenViking-Account/User` headers, else `default`; role root |
| `api_key` | key required | from the key; account/user headers are silently stripped |
| `trusted` | root key optional | from the headers; missing → 400 `Trusted mode requests must include X-OpenViking-Account …` |

Key formats: `base64url(account).base64url(user).base64url(secret)` (three
segments, identity readable) or a bare 64-hex legacy key (identity only via
server lookup). Both fail identically as `401 Invalid API Key` when unknown —
there is no "wrong account" error.

`role: root` keys are allowed only on `/api/v1/system/status`, `/api/v1/admin/*`,
`/api/v1/observer/*`, `/api/v1/tasks/*` and a few system paths; everything the
plugin uses (`/api/v1/sessions`, `/api/v1/search`, `/api/v1/fs`, `/mcp`)
answers `403 ROOT API keys cannot access tenant-scoped data APIs in api_key mode`.

## Error strings

Server (REST): `{"status":"error","error":{"code":…,"message":…}}`

| HTTP | code | message | Meaning |
|---|---|---|---|
| 401 | UNAUTHENTICATED | `Missing API Key when resolving identity.` | No credential reached the server (gateway stripped `Authorization`, or `bearer` lowercase) |
| 401 | UNAUTHENTICATED | `Invalid API Key` | Unknown/revoked key, or a well-formed key for a non-existent account/user |
| 403 | PERMISSION_DENIED | `ROOT API keys cannot access tenant-scoped data APIs …` | Root key used as the plugin key |
| 403 | PERMISSION_DENIED | `Requires role: …` | Key valid, role too low for that route |
| 400 | INVALID_ARGUMENT | `Trusted mode requests must include X-OpenViking-Account …` | Trusted mode without account/user |
| 412 | FAILED_PRECONDITION | `User deletion is in progress` | The user is being deleted |

`/mcp` directly: `406 Not Acceptable: Client must accept both application/json and text/event-stream`;
`400` plain text `Invalid Content-Type header`; `401/403` as JSON-RPC `-32001`
with the server message; `404` → no MCP endpoint at that url.

Plugin MCP proxy (what Claude Code shows for a failing tool call):

| JSON-RPC | Message | Meaning |
|---|---|---|
| `-32001` | `OpenViking MCP authentication failed (HTTP 401\|403). Check ~/.openviking/ovcli.conf or OPENVIKING_API_KEY …` | Credentials; `data.serverMessage` carries the server text. `data.credentialPath` may name ov.conf even when the key came from ovcli.conf on older versions. |
| `-32001` | `OpenViking MCP request failed. Check the configured URL (<mcpUrl>) …` | Transport: `data.cause` = `fetch failed` (refused/DNS/TLS), `This operation was aborted` (timeout), a JSON `SyntaxError` (garbage SSE body). The url in the message is the one actually used. |
| `-32002` | `OpenViking MCP upstream returned HTTP <n>.` | Any other status; HTML in `data.serverMessage` means the url is not an OpenViking endpoint |
| `-32003` | `OpenViking MCP upstream returned an empty response` | 2xx with blank/non-JSON body — captive portal or proxy interstitial |

Hook log (`cc-hooks.log`) stages worth grepping: `health_check`
(connectivity), `push_turns` / `capture_write` / `pending_enqueue` (capture),
`recall_context_assembled` / `search_summary` / `injection_built` (recall),
`mcp-proxy` `start` (resolved `mcpUrl`, `hasApiKey` = present, not valid),
`uncaught` (crash). Hooks: `session-start`, `auto-recall`, `auto-capture`,
`session-end`, `pre-compact`, `subagent-start`, `subagent-stop`,
`skill-experience`, `mcp-proxy`. Ordinary failed proxy requests are not logged
— the JSON-RPC error on stdout is the artifact.

## State file fields

`last-recall.json`: `reason` ∈ `ok`, `no_results`, `filtered_out`, `short_query`,
`offline`, `bypass`, `disabled`, `bad_stdin`; plus `count`, `latency_ms`,
`server_url`, `cc_session_id`, `ts`.

`last-capture.json`: `turns_captured`, `turns_queued` (retryable, will replay),
`turns_failed` (non-retryable, dropped), `pending_tokens` / `commit_threshold`,
`commit_count`, `ov_session_id` (`cc-<claude session id>`), `ts`.

Statusline: `OV ✓` = `/health` 200 within 1s (says nothing about auth);
`OV ⚠ slow` = probe timed out; `OV ✗ offline` = refused/DNS/TLS/non-2xx;
`OV ⚡ bypass` = bypass matched; `✗ N dropped` = `turns_failed`. Segments
disappear after 30 minutes of inactivity — a bare `OV ✓` means idle, not broken.

## Local server (loopback url only)

| Path | What |
|---|---|
| `~/.openviking/ov.conf` (or `OPENVIKING_CONFIG_FILE`, then `/etc/openviking/ov.conf`) | The server's config. The doctor checks the copy the plugin resolves for plugin-only keys; a server started with another `--config` runs from that file instead. |
| `<storage.workspace>/` | Data: `viking/` (content), `vectordb/context/` (index), `_system/queue/queue.db`. `storage.workspace` defaults to `./data` relative to the server's cwd. |
| `<workspace>/log/openviking.log` | Only with `log.output: "file"`. Default is stdout (terminal / tmux / nohup file / `journalctl -u openviking` / `docker logs openviking`). Time-rotated as `openviking.log.YYYY-MM-DD`. |
| docker | Container `openviking`, image `ghcr.io/volcengine/openviking`, `~/.openviking` mounted at `/app/.openviking` (config, ovcli.conf and data). `docker exec openviking openviking-server doctor` works. |

Process: `<python> -E …/bin/openviking-server` — match `openviking-server` in
the command line, never the process name (`Python`). Default bind
`127.0.0.1:1933`; `--host`/`--port` override `server.host`/`server.port`. The
port is bound only after initialization finishes, so "connection refused"
during startup is normal; `/health` 200 says nothing about embedding or VLM.

`GET /ready` (no auth, 200 or 503):
`{"status":"ready"|"not_ready","checks":{"agfs":{"status":…,"checks":{"filesystem":…,"multiwrite_sync":…}},"vectordb":…,"api_key_manager":…,"embedding":…,"ollama":…}}`.
`ok`, `not_configured` and `not_supported` count as healthy; `embedding` is a
real embed call with a 10s cap. `503 {"status":"not_ready","reason":"initializing"}`
while booting; 404 on servers that predate the endpoint. The official docker
image answers every route with `503 {"status":"pending_initialization", "fix":[…]}`
until it has an ov.conf.

Startup failures (printed by the server; exit 1 unless noted):

| Text | Cause |
|---|---|
| `OpenViking configuration file not found.` | No ov.conf at any resolved path |
| `Unknown config field '…' in OpenVikingConfig` / `Extra inputs are not permitted` | Unknown key — including `claude_code`, `codex` and `server.url`, which only the plugins read |
| `SECURITY: server.auth_mode='dev' requires server.host to be localhost` | Dev mode (no `auth_mode`, no `root_api_key`) on a non-loopback bind |
| `Invalid server.root_api_key: empty string is not allowed` | `""` instead of `null` |
| `Another OpenViking process (PID n) is already using the data directory` | Two servers on one workspace (exit 3, `Application startup failed. Exiting.`) |
| `EmbeddingRebuildRequiredError` / `embedding dimension (…) does not match current configuration` | Embedding model changed on an existing workspace (exit 3) |
| `[Errno 48] / [Errno 98] Address already in use` | Port taken — `lsof -nP -iTCP:1933 -sTCP:LISTEN` |
| `FATAL: AUTHENTICATION HEALTH CHECK FAILED` | OIDC/LDAP backend unreachable |

Runtime signatures in the server log: `Dimension mismatch` (config), `Dense
vector dimension mismatch` (writes dropped), `Credential … failed with auth`
and `Backup VLM also failed` (VLM key), `Embedding circuit breaker is open`
(provider down, messages re-queued).

`openviking-server doctor` (same as `ov doctor`) checks Config, Python,
Native Engine, AGFS, Authentication, Embedding (live probe), VLM (key presence
only), Ollama, VikingBot and Disk. Text only, exit 1 on FAIL, needs the
server's Python environment; the bare Rust `ov` binary (npm / cargo install)
reports `unknown command`.

## Symptom catalogue

| Symptom | Likely cause | Detect | Fix |
|---|---|---|---|
| Plugin listed and enabled, but no `<openviking-context>` ever, no log | No parseable config → hooks exit at once | doctor "plugin disabled"; `/ov` prints `DISABLED` | Create/fix `ovcli.conf` |
| Worked until an edit to `ovcli.conf` | JSON broken by the edit | doctor "cannot be parsed" | Fix JSON |
| Edits to `ovcli.conf` have no effect | Env var or rc-file export wins | doctor "← env"; `env \| grep OPENVIKING_` | Remove the export |
| Recall empty on a healthy server | Wrong key (401 reads as no results), wrong user space, peer scope, threshold | `/health` with key; `/api/v1/system/status` → `result.user`; `last-recall.json.reason` | Fix key; `OPENVIKING_PEER_ID`; lower `OPENVIKING_SCORE_THRESHOLD` |
| Recall empty after moving/renaming the repo | Mostly gone: the peer follows `origin`, so a move, rename, worktree or fresh clone keeps it. What still changes identity is a repository with no origin, which falls back to the root path | doctor `peer … ← workspace (…)` — `{git_remote}` is move-proof, `{git_root}` is not; `previous peer` names the id left behind | `git remote add origin …`, or pin `peer.id` in `.openviking/config.json` / `OPENVIKING_PEER_ID` |
| No project memory in a directory that is not a repository | By design: no `.git` and no `.openviking/config.json` above it means no peer, so those memories are in the user-level space | doctor Workspace section: `not a workspace: …` | Create `.openviking/config.json` there with a `peer.id`, or leave it as it is |
| Recall or capture dead in one repository only | A committed `.openviking/config.json` turns `recall.enabled` / `capture.enabled` off, or pins `peer.id` | doctor Workspace section: `<key> = … ← config.json (workspace)` plus the announcement warning | Override in `.openviking/config.local.json` or `~/.openviking/workspaces/<slot>.json` |
| Statusline green, MCP tools 401 | `/health` needs no auth | doctor "api key rejected" | Fix key |
| Statusline green, MCP tools missing | Plugin MCP not loaded, node missing for `.mcp.json`, or `/mcp` not proxied | `claude mcp list`; `/mcp` | Fix node/PATH; reverse proxy `/mcp` |
| Everything duplicated (two context blocks, doubled latency) | Legacy `settings.json` hooks + plugin; or two plugin ids enabled | doctor legacy/duplicate warnings | Remove legacy entries |
| `claude plugin update` says up to date but bug persists | Version-keyed cache, version string unchanged | doctor "no skills/ directory"; compare `installPath` version with repo | Uninstall + install |
| Statusline blank | `statusLine.command` path gone, `OPENVIKING_STATUSLINE=off`, or plugin disabled | doctor statusline check | Re-run installer / fix path |
| `UserPromptSubmit hook timed out` | Recall > 60s (server query expansion / rewrite) | `last-recall.json.latency_ms` | `OPENVIKING_RECALL_QUERY_EXPANSION=off`, `OPENVIKING_RECALL_COMPRESS=off` |
| Every prompt is slow by ~10–30s | Local compressor spawns `claude -p` (`recallCompress: auto`) | log `host_compressor_*`; `state/host-cli-probe.json` | `OPENVIKING_RECALL_COMPRESS=off` or `server` |
| `Stop` hook timeouts | Sync write path against a slow server | `writePathAsync` in doctor toggles | Keep `OPENVIKING_WRITE_PATH_ASYNC=1` |
| curl works, plugin says offline | Corporate proxy or private CA; Node ignores both | doctor proxy/TLS hints; `node -e "fetch('<url>/health')"` | `NODE_USE_ENV_PROXY=1` / `NODE_EXTRA_CA_CERTS` in the launching environment |
| Local `Write`/`Edit` denied with "viking:// URIs are OpenViking virtual paths" | Old uri-guard in a stale cache | `grep -c DEFAULT_CONTENT_KEYS <installPath>/scripts/shared/uri-guard.mjs` → 0 | Update the plugin |
| Installer exits silently | Pre-2026-07 installer | No `OpenViking installer stopped unexpectedly.` line | Re-fetch the installer |
| Installer: `Unsupported OS` | Windows | — | Manual marketplace install |
| `0 memories extracted` / commits never produce memories | VLM missing or failing, or embedding failing on the server | doctor `/ready: embedding`; ov.conf without a `vlm` section; server log `Backup VLM also failed` / `Credential … failed with auth` | Fix vlm/embedding in ov.conf, restart the server |
| "server unreachable" right after editing ov.conf | The server exited at its restart because of the edit | doctor Server health lint; the startup text in the server's terminal | Fix the finding, start it again |
| Recall empty and the index never grows after switching the embedding model | Dimension mismatch — every vector write is dropped | startup `EmbeddingRebuildRequiredError`, or log `Dense vector dimension mismatch` while it still runs | Original model, or a fresh workspace |

## Links

- Source: <https://github.com/volcengine/OpenViking>
- Docs index: <https://docs.openviking.ai/llms.txt>
