# OpenViking Memory Doctor — reference

Companion to SKILL.md: where things live, what the exact error strings mean, the symptom catalogue, and the notes for the hosts that have a doctor of their own. Paths assume the defaults; `OPENVIKING_HOME`, `OPENVIKING_CONFIG_FILE`, `OPENVIKING_CLI_CONFIG_FILE`, `OPENVIKING_DEBUG_LOG`, `OPENVIKING_HOOK_STATE_DIR` and `OPENVIKING_PENDING_DIR` relocate individual pieces. `<harness>` below is the client's config name: `claude_code`, `codex`, `cursor`, `trae`, `trae_cn`, `zcode`, `opencode`, `dsh`, `pi`, and so on.

## Where things live

| Path | What |
|---|---|
| `~/.openviking/ovcli.conf` | Client connection: `url`, `api_key`, `account`, `user`, optional `plugin.<harness>.*` tuning. Mode 0600. |
| `~/.openviking/ov.conf` | Server config. The integrations read only `server.url/host/port`, `server.root_api_key` (last-resort key) and a legacy top-level `<harness>` block. |
| `~/.openviking/ovcli.conf.<name>` | Saved CLI profiles (`ov config switch` copies one over `ovcli.conf`). `ovcli.conf.bak.<epoch>` are installer backups, not profiles. |
| `<repo root>/.openviking/config.json` / `config.local.json` | Workspace config layers, `version: 1` required: `peer.source`, `peer.id`, `recall.*`, `capture.*`, `bypass.session_patterns`, `labels`. `config.json` is committed and shared; `config.local.json` is private and gitignored. Trusted without a prompt, but connection and credential keys (`url`, `api_key`, `account`, `user`, `extra_headers`, …) are stripped with a warning and `${VAR}` is never expanded. A blanket `.openviking/` rule in `.gitignore` stops `config.json` from ever being committed — narrow it to `.openviking/media/` and `.openviking/downloads/`. |
| `~/.openviking/workspaces/<slot>.json` | Per-machine workspace registry, one file per workspace (`<dir name>-<hash>.json`, mode 0600). Outranks both workspace files, and nothing writes it — a user creates the file by hand. An entry recorded for a different repository is ignored, not inherited. |
| `~/.openviking/agent-integrations/<client>/` | The runtime the installer assembled for Cursor, TRAE, TRAE CN, ZCode and Antigravity; CodeBuddy Code and Qoder CLI keep a local marketplace under it. |
| `~/.openviking/hook-state/<client>/<session>.json` / `.lock` | Per-session hook state (capture cursor, recall and profile bookkeeping) and the lock serializing one session's hooks. |
| `~/.openviking/logs/<client>-hooks.log` | JSONL hook + proxy log (`cc-hooks.log` for Claude Code); written only when `OPENVIKING_DEBUG=1` or `debug: true`. |
| `~/.openviking/pending/` | Offline write queue (retryable failures only); drained at session start after `/health` passes. |

## Config resolution

| Field | Order |
|---|---|
| url | `OPENVIKING_URL` → `OPENVIKING_BASE_URL` → `ovcli.conf url` → `ov.conf server.url` → `http://{server.host\|127.0.0.1}:{server.port\|1933}` |
| api_key | `OPENVIKING_BEARER_TOKEN` → `OPENVIKING_API_KEY` → `ovcli.conf api_key` → `ovcli.conf plugin.<harness>.apiKey` → `ovcli.conf plugin.apiKey` → `ov.conf <harness>.apiKey` → `ov.conf server.root_api_key` |
| account / user | `OPENVIKING_ACCOUNT` / `OPENVIKING_USER` → `ovcli.conf account/user` → `ovcli.conf plugin.<harness>.accountId/userId` → `ovcli.conf plugin.accountId/userId` → `ov.conf <harness>.accountId/userId` |
| peer | `OPENVIKING_PEER_ID` → registry → `config.local.json` → `config.json` (`peer.id`) → `ovcli.conf plugin.<harness>.peerId` → `ovcli.conf plugin.peerId` → `ovcli.conf actor_peer_id/peer_id` → `ov.conf <harness>.peerId` → derived per `peer.source` unless `OPENVIKING_WORKSPACE_PEER=0` |
| peer.source | `OPENVIKING_PEER_SOURCE` → registry → `config.local.json` → `config.json` → `ovcli.conf plugin.<harness>.peerSource` → `ovcli.conf plugin.peerSource` → `ov.conf <harness>.peerSource` → `git` |
| auth mode | `OPENVIKING_AUTH_MODE` → `ovcli.conf plugin.<harness>.authMode` → `ovcli.conf plugin.authMode` → `ov.conf <harness>.authMode` → `ov.conf server.auth_mode` → `trusted` when account/user are set, else `api_key` |
| tuning | env → registry → `config.local.json` → `config.json` → `ovcli.conf plugin.<harness>.*` → `ovcli.conf plugin.*` → `ov.conf <harness>.*` → defaults |

The order above is the `auto` mode with a credential variable set, or with no ovcli.conf credentials. When ovcli.conf names a url, key, identity or peer and no credential variable is set, the chain is pinned to that file: the credential variables are skipped, `api_key` still falls back through the `plugin` keys and `ov.conf <harness>.apiKey`, and account/user stop at the `plugin` keys. `OPENVIKING_CREDENTIAL_SOURCE=cli` forces that; `=env` reads the variables only, and neither file. The doctor prints the effective mode as `credential source`, and the MCP proxy resolves this same chain.

Only trailing slashes are stripped from the url; no scheme check, no path normalisation. `https://api.vikingdb.cn-beijing.volces.com/openviking` (the Volcengine-hosted OpenViking Service) is a legitimate path prefix; `/api/v1` or `/mcp` suffixes are not.

`peer.source` decides the derivation. `git` (the default) is the template list `["{git_remote}", "{git_root}"]`: the normalized origin URL (`git@github.com:volcengine/OpenViking.git` → `github.com-volcengine-openviking`, userinfo dropped so an embedded token can never reach the id), else the repository root path (the legacy `[^A-Za-z0-9] → -` rule), else nothing — outside a git repository no peer is sent at all, and what is remembered there goes to the user-level space. `{cwd}` is still a variable but sits in no default chain, and `{dir}` is the workspace root's directory name, empty when the directory is not a workspace. `cwd` is that legacy rule alone (the pre-git behaviour); `none` sends no peer, which is also what `OPENVIKING_WORKSPACE_PEER=0` means. Anything else is a template, or a list of templates tried in order (`"git-{git_remote}"`, `["team-{dir}", "{cwd}"]`); a template naming an empty variable falls through to the next. Derivation is filesystem-only — no `git` subprocess — so it survives a missing `git` and a dubious-ownership refusal; worktrees converge through `commondir`, a submodule keeps its own identity, and `$HOME` and `/` are never workspace roots. Every clone of one repository shares one peer; a fork's origin differs, so it stays separate, and `gh pr checkout` does not change origin.

Peers minted before this need no migration: the old cwd-derived id is recomputed locally, `peer_scope: "all"` already sweeps it, and under `"actor"` recall asks it separately. Doctor prints it as `previous peer`. Across the workspace layers lists union rather than replace, with a leading `"!reset"` dropping what the lower layers contributed; unknown keys are kept and ignored, and a file declaring a version other than `1` is skipped with a warning.

Sent headers: `Authorization: Bearer <key>`, `X-OpenViking-Account/User` (trusted mode only), `X-OpenViking-Actor-Peer`, `User-Agent: openviking-memory-<client>/<version>`. The integrations never send `X-API-Key`. The open-source server still accepts it (and prefers it when both are sent), so a gateway that injects one shadows the key here; the Volcengine-hosted OpenViking Service accepts Bearer only.

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

Key format: `base64url(account).base64url(user).base64url(secret)` (three segments, identity readable). Other shapes are rejected as `401 Invalid API Key` — there is no "wrong account" error.

`role: root` keys are allowed only on `/api/v1/system/status`, `/api/v1/admin/*`, `/api/v1/observer/*`, `/api/v1/tasks/*` and a few system paths; everything the integrations use (`/api/v1/sessions`, `/api/v1/search`, `/api/v1/fs`, `/mcp`) answers `403 ROOT API keys cannot access tenant-scoped data APIs in api_key mode`.

## Error strings

Server (REST): `{"status":"error","error":{"code":…,"message":…}}`

| HTTP | code | message | Meaning |
|---|---|---|---|
| 401 | UNAUTHENTICATED | `Missing API Key when resolving identity.` | No credential reached the server (gateway stripped `Authorization`, or `bearer` lowercase) |
| 401 | UNAUTHENTICATED | `Invalid API Key` | Unknown/revoked key, or a well-formed key for a non-existent account/user |
| 403 | PERMISSION_DENIED | `ROOT API keys cannot access tenant-scoped data APIs …` | Root key used as the integration's key |
| 403 | PERMISSION_DENIED | `Requires role: …` | Key valid, role too low for that route |
| 400 | INVALID_ARGUMENT | `Trusted mode requests must include X-OpenViking-Account …` | Trusted mode without account/user |
| 412 | FAILED_PRECONDITION | `User deletion is in progress` | The user is being deleted |

`/mcp` directly: `406 Not Acceptable: Client must accept both application/json and text/event-stream`; `400` plain text `Invalid Content-Type header`; `401/403` as JSON-RPC `-32001` with the server message; `404` → no MCP endpoint at that url.

The integration's MCP proxy (what the host shows for a failing tool call):

| JSON-RPC | Message | Meaning |
|---|---|---|
| `-32001` | `OpenViking MCP authentication failed (HTTP 401\|403). Check ~/.openviking/ovcli.conf or OPENVIKING_API_KEY …` | Credentials; `data.serverMessage` carries the server text. |
| `-32001` | `OpenViking MCP request failed. Check the configured URL (<mcpUrl>) …` | Transport: `data.cause` = `fetch failed` (refused/DNS/TLS), `This operation was aborted` (timeout), a JSON `SyntaxError` (garbage SSE body). The url in the message is the one actually used. |
| `-32002` | `OpenViking MCP upstream returned HTTP <n>.` | Any other status; HTML in `data.serverMessage` means the url is not an OpenViking endpoint |
| `-32003` | `OpenViking MCP upstream returned an empty response` | 2xx with blank/non-JSON body — captive portal or proxy interstitial |

Hook log stages worth grepping: `health_check` (connectivity), `push_turns` / `capture_write` / `pending_enqueue` (capture), `recall_context_assembled` / `recall_search_summary` / `recall_injection_built` (recall), `mcp-proxy` `start` (resolved `mcpUrl`, `hasApiKey` = present, not valid), `uncaught` (crash). Ordinary failed proxy requests are not logged — the JSON-RPC error on stdout is the artifact.

## Local server (loopback url only)

| Path | What |
|---|---|
| `~/.openviking/ov.conf` (or `OPENVIKING_CONFIG_FILE`, then `/etc/openviking/ov.conf`) | The server's config. The doctor checks the copy the integration resolves for plugin-only keys; a server started with another `--config` runs from that file instead. |
| `<storage.workspace>/` | Data: `viking/` (content), `vectordb/context/` (index), `_system/queue/queue.db`. `storage.workspace` defaults to `./data` relative to the server's cwd. |
| `<workspace>/log/openviking.log` | Only with `log.output: "file"`. Default is stdout (terminal / tmux / nohup file / `journalctl -u openviking` / `docker logs openviking`). Time-rotated as `openviking.log.YYYY-MM-DD`. |
| docker | Container `openviking`, image `ghcr.io/volcengine/openviking`, `~/.openviking` mounted at `/app/.openviking` (config, ovcli.conf and data). `docker exec openviking openviking-server doctor` works. |

Process: `<python> -E …/bin/openviking-server` — match `openviking-server` in the command line, never the process name (`Python`). Default bind `127.0.0.1:1933`; `--host`/`--port` override `server.host`/`server.port`. The port is bound only after initialization finishes, so "connection refused" during startup is normal; `/health` 200 says nothing about embedding or VLM. A server that exits at startup prints the reason in its own terminal — running `openviking-server` in the foreground is the quickest way to see it. It is the user's process: ask before stopping or restarting it.

`GET /ready` (no auth, 200 or 503): `{"status":"ready"|"not_ready","checks":{"agfs":{"status":…,"checks":{"filesystem":…}},"vectordb":…,"api_key_manager":…,"embedding":…,"ollama":…}}`. `ok`, `not_configured` and `not_supported` count as healthy; `embedding` is a real embed call with a 10s cap. `503 {"status":"not_ready","reason":"initializing"}` while booting; 404 on servers that predate the endpoint. The official docker image answers every route with `503 {"status":"pending_initialization", "fix":[…]}` until it has an ov.conf.

Startup failures (printed by the server; exit 1 unless noted):

| Text | Cause |
|---|---|
| `OpenViking configuration file not found.` | No ov.conf at any resolved path |
| `Unknown config field '…' in OpenVikingConfig` / `Extra inputs are not permitted` | Unknown key — including a top-level `<harness>` block and `server.url`, which only the integrations read |
| `SECURITY: server.auth_mode='dev' requires server.host to be localhost` | Dev mode (no `auth_mode`, no `root_api_key`) on a non-loopback bind |
| `Invalid server.root_api_key: empty string is not allowed` | `""` instead of `null` |
| `Another OpenViking process is already using the data directory` | Two servers on one workspace (exit 3, `Application startup failed. Exiting.`) |
| `EmbeddingRebuildRequiredError` / `embedding dimension (…) does not match current configuration` | Embedding model changed on an existing workspace (exit 3) |
| `[Errno 48] / [Errno 98] Address already in use` | Port taken — `lsof -nP -iTCP:1933 -sTCP:LISTEN` |
| `FATAL: AUTHENTICATION HEALTH CHECK FAILED` | OIDC/LDAP backend unreachable |

Runtime signatures in the server log: `Dimension mismatch` (config), `Dense vector dimension mismatch` (writes dropped), `Credential … failed with auth` and `Backup VLM also failed` (VLM key), `Embedding circuit breaker is open` (provider down, messages re-queued).

`openviking-server doctor` (same as `ov doctor`) checks Config, Python, Native Engine, AGFS, Authentication, Embedding (live probe), VLM (key presence only), Ollama, VikingBot and Disk. Text only, exit 1 on FAIL, needs the server's Python environment; the bare Rust `ov` binary (npm / cargo install) reports `unknown command`.

## Symptom catalogue

| Symptom | Likely cause | Detect | Fix |
|---|---|---|---|
| Worked until an edit to `ovcli.conf` | JSON broken by the edit | doctor "cannot be parsed" | Fix JSON |
| Edits to `ovcli.conf` or `ov config switch` have no effect | Env var or rc-file export wins (`credential source: env`) | doctor "← env"; `env \| grep OPENVIKING_` | Remove the export |
| Recall empty on a healthy server | Wrong key (401 reads as no results), wrong user space, peer scope, threshold | `/health` with key; `/api/v1/system/status` → `result.user` | Fix key; `OPENVIKING_PEER_ID`; lower `OPENVIKING_SCORE_THRESHOLD` |
| Recall empty after moving/renaming the repo | Mostly gone: the peer follows `origin`, so a move, rename, worktree or fresh clone keeps it. What still changes identity is a repository with no origin, which falls back to the root path | doctor `peer … ← workspace (…)` — `{git_remote}` is move-proof, `{git_root}` is not; `previous peer` names the id left behind | `git remote add origin …`, or pin `peer.id` in `.openviking/config.json` / `OPENVIKING_PEER_ID` |
| No project memory in a directory that is not a repository | By design: no `.git` and no `.openviking/config.json` above it means no peer, so those memories are in the user-level space | doctor Workspace section: `not a workspace: …` | Create `.openviking/config.json` there with a `peer.id`, or leave it as it is |
| Recall or capture dead in one repository only | A committed `.openviking/config.json` turns `recall.enabled` / `capture.enabled` off, or pins `peer.id` | doctor Workspace section: `<key> = … ← config.json (workspace)` plus the announcement warning | Override in `.openviking/config.local.json` or `~/.openviking/workspaces/<slot>.json` |
| MCP tools 401 while everything looks reachable | `/health` needs no auth | doctor "api key rejected" | Fix key |
| MCP tools missing | Plugin MCP not loaded, node missing, or `/mcp` not proxied | doctor Plugin install and Connection `/mcp` probe | Fix enablement / node / reverse proxy |
| curl works, the integration says offline | Corporate proxy or private CA; Node ignores both | doctor proxy/TLS hints; `node -e "fetch('<url>/health')"` | `NODE_USE_ENV_PROXY=1` / `NODE_EXTRA_CA_CERTS` in the launching environment |
| `0 memories extracted` / commits never produce memories | VLM missing or failing, or embedding failing on the server | doctor `/ready: embedding`; ov.conf without a `vlm` section; server log `Backup VLM also failed` / `Credential … failed with auth` | Fix vlm/embedding in ov.conf, restart the server |
| "server unreachable" right after editing ov.conf | The server exited at its restart because of the edit | doctor Server health lint; the startup text in the server's terminal | Fix the finding, start it again |
| Recall empty and the index never grows after switching the embedding model | Dimension mismatch — every vector write is dropped | startup `EmbeddingRebuildRequiredError`, or log `Dense vector dimension mismatch` while it still runs | Original model, or a fresh workspace |
| Installer: `Unsupported OS` | Windows | — | Manual install per the host's docs page |

## Claude Code

The doctor is `hosts/claude-code/ov-memory-doctor.mjs`; `scripts/ov-memory-doctor.mjs` hands over to it. If the report says "this script is not running from the registered install", rerun it from the path it prints — Claude Code executes hooks from that copy.

| Path | What |
|---|---|
| `~/.claude/plugins/installed_plugins.json` | Install registry: `plugins["openviking-memory@openviking"][0].installPath/version/lastUpdated`. |
| `~/.claude/plugins/known_marketplaces.json` | Marketplace `openviking` → `source` (`directory` path, `github`, or `url` of the TOS manifest), `installLocation`, `autoUpdate`. |
| `~/.claude/plugins/cache/openviking/openviking-memory/<version>/` | The copy Claude Code actually runs. Keyed by `.claude-plugin/plugin.json` version. |
| `~/.claude/settings.json` | `enabledPlugins`, `statusLine`, optional `env`, legacy `hooks`. |
| `~/.openviking/marketplaces/openviking-claude/` | Directory marketplace older installers generated; a re-run of the installer moves the registration to the TOS URL marketplace. |
| `~/.openviking/memory-plugin-marketplace/` | Unpacked plugin bundle from the release; the marketplace only before Claude Code 2.1.224, and cannot self-update. |
| `~/.openviking/state/` | `last-recall.json`, `last-capture.json`, `last-session-event.json`, `daily-stats.json`, `server-probe.json`, `host-cli-probe.json`, `context-face.json`, `recall-digest.json`, `ws-identity-<hash>.json`. Written by Claude Code only. |
| `~/.openviking/last_inject.md` | Full text of the last SessionStart injection. |

| Doctor finding | Meaning | Action |
|---|---|---|
| `plugin disabled — every hook exits immediately` | No parseable config, `OPENVIKING_MEMORY_ENABLED=0`, or `claude_code.enabled: false` in ov.conf | Fix the reason it names. Fresh machine: create `~/.openviking/ovcli.conf` with `url` + `api_key` (`chmod 600`). |
| `not registered in installed_plugins.json` / `claude plugin list does not show …` | Plugin never installed, or installed under an old id | Re-run the installer with `--harness claude`. |
| `plugin is disabled in ~/.claude/settings.json` | Installed but `enabledPlugins` is false/missing | `claude plugin enable openviking-memory@openviking`, restart Claude Code. |
| `marketplace 'openviking' … missing directory` / registered as a file | The checkout/archive moved, or a file-type marketplace (fails `marketplace update` with EISDIR) | `claude plugin marketplace remove openviking`, then re-run the installer. |
| `no skills/ directory in this plugin copy` / registered version behind the repo | Stale version-keyed cache; `claude plugin update` is a no-op when the version string did not change | `claude plugin marketplace update openviking && claude plugin uninstall openviking-memory@openviking && claude plugin install openviking-memory@openviking`. |
| `legacy openviking hooks … settings.json` / extra plugin ids / user-scope MCP `openviking` | Pre-2.0 install residue; every hook fires twice, MCP server registered twice | Remove the openviking entries from `.hooks` in `~/.claude/settings.json` (back it up), `claude plugin uninstall <stale id>`, `claude mcp remove openviking -s user`. |
| `last auto-recall … reason=offline/bypass/disabled/short_query` | The hook ran and chose not to inject | `offline` → connection; `bypass` → `OPENVIKING_BYPASS_SESSION*`; `short_query` → prompt shorter than `minQueryLength`; only `no_results`/`filtered_out` mean the search ran. A 401 on the search call also reads as `no_results`. |
| `turns_failed > 0` / `capture payload(s) waiting` | Writes rejected (401/403/404 are dropped, not queued) or server was down (queued, replayed at next SessionStart) | Fix credentials/connection; `OPENVIKING_WRITE_PATH_ASYNC=0` makes the error visible on stderr. |
| `recall is pinned to the legacy /search/recall endpoint` | One 4xx mentioning "mode" pins recall for 6h | `rm ~/.openviking/state/context-face.json`. |

- `OPENVIKING_DEBUG=1` must reach Claude Code's own process — a shell export does not, the `env` block in `~/.claude/settings.json` does. That block is also the place for `PATH`, `NODE_USE_ENV_PROXY` and `NODE_EXTRA_CA_CERTS`. A `subagent-stop transcript_read ENOENT` line in `cc-hooks.log` is harmless noise.
- MCP wiring: `claude mcp list` (slow, 30–60s) must show `plugin:openviking-memory:openviking: node <installPath>/servers/mcp-proxy.mjs - ✔ Connected`; `claude mcp get 'plugin:openviking-memory:openviking'` needs the full quoted name. `/mcp` inside a session reconnects the proxy.
- A capture: take `ov_session_id` from `~/.openviking/state/last-capture.json`. `last-capture.json` also holds `turns_captured`, `turns_queued` (retryable), `turns_failed` (dropped), `pending_tokens`, `commit_count`; `last-recall.json` holds `reason` (`ok`, `no_results`, `filtered_out`, `short_query`, `offline`, `bypass`, `disabled`, `bad_stdin`), `count`, `latency_ms`.
- Statusline: `OV ✓` = `/health` 200 within 1s (says nothing about auth); `OV ⚠ slow` = probe timed out; `OV ✗ offline` = refused/DNS/TLS/non-2xx; `OV ⚡ bypass` = bypass matched; `✗ N dropped` = `turns_failed`. Segments disappear after 30 minutes of inactivity — a bare `OV ✓` means idle, not broken. A blank statusline: `statusLine.command` path gone, `OPENVIKING_STATUSLINE=off`, or the plugin disabled.
- Hook budgets in `hooks/hooks.json`: SessionStart 120s, UserPromptSubmit 60s, Stop and SubagentStop 45s, PreCompact and SessionEnd 30s, SubagentStart 10s, PreToolUse and PostToolUse 5s. A slow prompt by ~10–30s is the local compressor spawning `claude -p` (`OPENVIKING_RECALL_COMPRESS=off` or `server`).
- Updates: GitHub installs → `claude plugin marketplace update openviking && claude plugin update openviking-memory@openviking`; TOS installs on Claude Code 2.1.224+ auto-update in the background, or on demand with `claude plugin update openviking-memory@openviking`; older TOS, archive and dev-checkout installs → re-run the installer.
- `hosts/claude-code/setup.mjs` needs a TTY; on a fresh machine write `ovcli.conf` directly or re-run the installer. Never pipe `claude plugin list` into `grep -q`; capture the output first (early exit under `pipefail` reads as "not installed").
- `OPENVIKING_MEMORY_ENABLED=0` disables the hooks but not the MCP tools; `OPENVIKING_MCP_URL` has no effect on this plugin.

## Codex

The doctor is `hosts/codex/ov-memory-doctor.mjs`; `scripts/ov-memory-doctor.mjs` hands over to it. Codex runs hooks from the plugin cache, so run the doctor from the copy this skill sits in. If the report says `cached plugin X differs from this copy`, rerun from `~/.codex/plugins/cache/openviking/openviking-memory/<newest version>/`.

| Path | What |
|---|---|
| `~/.codex/config.toml` | `[features] hooks` (or legacy `plugin_hooks`), `[plugins."openviking-memory@openviking"] enabled`, `[marketplaces.openviking]` (source, ref), `[hooks.state."openviking-memory@openviking:hosts/codex/hooks.json:<event>:0:0"] trusted_hash` for `session_start`, `user_prompt_submit`, `stop`, `session_end`, `pre_compact`. `CODEX_CONFIG_FILE` relocates it. |
| `~/.codex/plugins/cache/openviking/openviking-memory/<version>/` | The copy Codex runs hooks from. Keyed by `.codex-plugin/plugin.json` version. |
| `~/.codex/.tmp/marketplaces/openviking/` | Git clone of the marketplace; `codex plugin list --json` reports the plugin inside it as the source path. |
| `~/.openviking/codex-plugin-state/recall-compressor-profile.json` | Cached local-compressor detection (`profile.enabled`, `model`, `source`); `OPENVIKING_CODEX_STATE_DIR` relocates it. An older plugin's `<session_id>.json` there is read once as the starting cursor. |
| `~/.openviking/codex-plugin.rc.sh`, `~/.openviking/codex-memory-plugin/runtime/` | Residue of the pre-marketplace installer. The rc script, if still sourced, exports `OPENVIKING_*` and pins credentials to env mode. |

| Doctor finding | Meaning | Action |
|---|---|---|
| `codex plugin list does not show …` | Plugin never installed, or installed under an old id | Re-run the installer with `--harness codex`. |
| `hooks disabled in [features]` / `hooks feature is disabled in Codex` | The applicable hooks feature is disabled | Set `hooks = true` under `[features]` in `~/.codex/config.toml` (or `plugin_hooks = true` for older Codex), restart Codex. |
| `[features] hooks enabled by default` | The live CLI reports hooks enabled; legacy `plugin_hooks` has no effect | Continue with the remaining checks. |
| `[features] hooks is not set` (info) | The CLI probe could not determine the feature state and no legacy flag decides it | Verify with `codex features list`; an unset key alone does not prove hooks are disabled. |
| `[plugins."…"] enabled = false` / `installed but disabled` | Plugin switched off in config.toml | Set `enabled = true`, restart Codex. |
| `hooks disabled in [hooks.state]` | A hook was declined at the trust prompt | Remove `enabled = false` from that `[hooks.state."…"]` section; approve the hook again. |
| `hooks without a trust record yet` | Codex has not yet approved those hooks (fresh install, or `hooks.json` changed on update) | Start a Codex session and accept the hook prompt; nothing is wrong. |
| `marketplace 'openviking' is not registered` / root missing | Marketplace removed or its clone deleted | Re-run the installer. |
| `plugin.json does not declare skills` | Old plugin copy; the bundled skills are not loaded | Update the plugin. |
| `credential source: env` unexpectedly | A stray `OPENVIKING_*` var forces env mode; `ov config switch` and ovcli.conf edits do nothing | Unset the var (check shell rc files and `~/.openviking/codex-plugin.rc.sh` residue). |
| `more than one copy of openviking-memory is installed` | Two plugin ids installed (legacy `openviking-plugins-local`) | `codex plugin remove <stale id>`. |

- Hook timeouts in `hosts/codex/hooks.json`: SessionStart 70s, UserPromptSubmit 130s, Stop 30s, SessionEnd 3s (Codex clamps it there; the hook detaches a worker), PreCompact 60s. A slow prompt is the local compressor (`codex exec`) on each recall; `OPENVIKING_RECALL_COMPRESS=0` tests that.
- Edit `~/.codex/config.toml` only for the keys named above. Disable the plugin with `codex plugin remove openviking-memory@openviking` or `enabled = false`.
- Updates: GitHub/TOS git marketplaces → `codex plugin marketplace upgrade openviking` (keeps the pinned ref; the installer re-registers with the current ref); local directory marketplaces, or an upgrade that reports up to date while the bug persists → re-run the installer.
- `hosts/codex/setup.mjs` needs a TTY; on a fresh machine write `ovcli.conf` directly or re-run the installer. `~/.openviking/state/*.json` and `cc-hooks.log` belong to Claude Code, not Codex.

## Links

- Source: <https://github.com/volcengine/OpenViking>
- Docs index: <https://docs.openviking.ai/llms.txt>
