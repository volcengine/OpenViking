---
name: ov-memory-doctor
description: >
  Diagnose and fix the OpenViking memory plugin on this machine: the plugin
  install (marketplace, enablement, hooks, MCP server), the client
  configuration (ovcli.conf / ov.conf / OPENVIKING_* env — which one wins, what
  the API key claims) and the connection to the OpenViking server
  (reachability, 401/403, /mcp). Use whenever memory "isn't working": no
  <openviking-context> block appears, recall is always empty, captures don't
  land, MCP memory tools are missing or fail, 401/403 errors, the statusline
  says offline, right after installing/updating the plugin or switching
  servers/keys, or when the user asks for the plugin's status. Trigger phrases
  include "memory not working", "check OpenViking", "plugin status", "记忆没生效",
  "插件状态", "连不上 OpenViking", "recall 为空", "401", "0 memories
  extracted". When the server runs on this machine (loopback url) it also
  checks the port, plugin-only keys in ov.conf that stop the server from
  starting, and `GET /ready`; everything else server-side stays with
  `openviking-server doctor`.
allowed-tools: Bash(node ${CLAUDE_PLUGIN_ROOT}/scripts/ov-memory-doctor.mjs *)
---

# OpenViking Memory Doctor

Client-side troubleshooting for the OpenViking memory plugin. The plugin has
three moving parts and each fails silently in its own way:

- **Install** — marketplace registration, plugin enablement, the nine hooks,
  the stdio MCP proxy. When these are wrong, hooks never run and nothing is
  logged anywhere.
- **Configuration** — `~/.openviking/ovcli.conf`, `~/.openviking/ov.conf`
  and `OPENVIKING_*` environment variables, resolved as env → ovcli.conf →
  ov.conf → defaults. A malformed file reads as "no config", which silently
  disables the plugin; a stray env var silently overrides the file.
- **Connection** — `/health` answers 200 even with a bad key, so the statusline
  can be green while every real request 401s.

When the resolved url is loopback, the server runs on this machine and the
doctor adds a **Server health** section: whether anything listens on the
port, plugin-only keys in `ov.conf` (`claude_code`, `codex`, `server.url`)
that make the server refuse to start, and `GET /ready` — the server's own
per-subsystem verdict (agfs, vectordb, api keys, embedding, ollama). For a
remote server only `/ready` is probed. Everything else on the server side —
config validation, live embedding probe, native engine, disk — is
`openviking-server doctor`, run in the server's Python environment.

## Step 1 — run the doctor

```bash
node ${CLAUDE_PLUGIN_ROOT}/scripts/ov-memory-doctor.mjs
```

Options: `--json` (machine-readable), `--offline` (skip network probes),
`--timeout <ms>` (per probe, default 5000), `--no-color`.

The report has six sections — Environment, Plugin install, Configuration,
Connection, Server health, Recent activity — and a Summary listing every ✗/⚠
with a fix.
Read the whole report before acting: a single root cause usually shows up in
several sections (for example a bad key → "api key rejected" + "system/status
→ 401" + `turns_failed > 0`).

If the report says "this script is not running from the registered install",
rerun it from the path it prints — Claude Code executes hooks from that copy.

The API key is never printed in full. Three-segment keys are shown as
`account=<a> user=<u> secret=abcd…wxyz` (the first two segments are just
base64url identity, decoded so the user can see which account/user the key
claims); legacy keys as `abcd…wxyz (64 chars)`. Keep it that way in anything
you show the user.

## Step 2 — map findings to causes

Work top-down; fix the first ✗ and rerun before chasing the next.

| Doctor finding | Meaning | Action |
|---|---|---|
| `plugin disabled — every hook exits immediately` | No parseable config, `OPENVIKING_MEMORY_ENABLED=0`, or `claude_code.enabled: false` in ov.conf | Fix the reason it names. Fresh machine: create `~/.openviking/ovcli.conf` with `url` + `api_key` (`chmod 600`). |
| `ovcli.conf cannot be parsed` | Trailing comma/comment; the plugin treats the file as absent | Fix the JSON. This is the most common "installed but nothing happens". |
| `not registered in installed_plugins.json` / `claude plugin list does not show …` | Plugin never installed, or installed under an old id | Re-run the one-line installer (`bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) --harness claude`; add `--dist tos` where GitHub is blocked). |
| `plugin is disabled in ~/.claude/settings.json` | Installed but `enabledPlugins` is false/missing | `claude plugin enable openviking-memory@openviking`, restart Claude Code. |
| `marketplace 'openviking' … missing directory` / registered as a file | The checkout/archive moved, or a file-type marketplace (fails `marketplace update` with EISDIR) | `claude plugin marketplace remove openviking` then re-run the installer. |
| `no skills/ directory in this plugin copy` / registered version behind the repo | Stale version-keyed cache; `claude plugin update` is a no-op when the version string did not change | `claude plugin marketplace update openviking && claude plugin uninstall openviking-memory@openviking && claude plugin install openviking-memory@openviking`. |
| `legacy openviking hooks … settings.json` / extra plugin ids / user-scope MCP `openviking` | Pre-2.0 install residue; every hook fires twice, MCP server registered twice | Remove the openviking entries from `.hooks` in `~/.claude/settings.json` (back it up), `claude plugin uninstall <stale id>`, `claude mcp remove openviking -s user`. |
| `node is not on PATH` / node < 18 | Hooks and `.mcp.json` run the bare command `node`; GUI/IDE launches often lack nvm/volta shims | Put node on PATH for the launching environment, or set `PATH` in the `env` block of `~/.claude/settings.json`. |
| `server unreachable` (refused / dns / timeout / tls) | Wrong url/port, server down, DNS/VPN, private CA | Compare with `curl -sS <url>/health`. curl OK + doctor fails ⇒ proxy or CA issue (see Step 3). |
| `base URL ends with /api/v1` or `/mcp`, no scheme, `GET /health → 404` | url shape wrong (paths are concatenated bare); Cloud needs the `/openviking` prefix | Fix `url` to the API root. |
| `api key rejected` (200 from /health with no identity) then `system/status → 401 Invalid API Key` | Key invalid/revoked/for another deployment. A 3-segment key for a non-existent account looks identical. | Get the key re-issued; check no env var shadows ovcli.conf ("← env" in the Configuration section). |
| `using the ROOT api key` / `403 ROOT API keys cannot access tenant-scoped data APIs` | `api_key` fell through to `ov.conf server.root_api_key`, or the user pasted the root key | Create a user key (`POST /api/v1/admin/accounts/<account>/users` with the root key) and put it in ovcli.conf. |
| `server is in trusted mode and needs account + user` / 400 `Trusted mode requests must include…` | Server identity comes from headers | Set `account` and `user` in ovcli.conf. |
| `configured account/user differ from the key's identity` | In `api_key` mode the server ignores `X-OpenViking-Account/User`; data lands under the key's identity | Remove them or use a key for that identity. Explains "I changed the account but nothing changed". |
| `POST /mcp tools/list → 404/406/502/504` while /health is fine | Reverse proxy not forwarding `/mcp`, rewriting `Accept`, or buffering SSE | Fix the proxy (`proxy_buffering off`, forward `/mcp`, pass `Authorization`). |
| `proxy variables set` + curl works but doctor/hook fails | Node's fetch ignores `HTTP(S)_PROXY`; the plugin ships no proxy/CA handling | `NODE_USE_ENV_PROXY=1` or `NODE_EXTRA_CA_CERTS=<ca.pem>` in the environment that launches Claude Code (`env` block of `~/.claude/settings.json`). |
| `last auto-recall … reason=offline/bypass/disabled/short_query` | The hook ran and chose not to inject | `offline` → connection; `bypass` → `OPENVIKING_BYPASS_SESSION*`; `short_query` → prompt shorter than `minQueryLength`; only `no_results`/`filtered_out` mean the search actually ran. Note that a 401 on the search call also reads as `no_results`. |
| `turns_failed > 0` / `capture payload(s) waiting` | Writes rejected (401/403/404 are dropped, not queued) or server was down (queued, replayed at next SessionStart) | Fix credentials/connection; `OPENVIKING_WRITE_PATH_ASYNC=0` makes the error visible on stderr. |
| `MCP proxy last started against <other url>` | The proxy is a long-lived process; a changed url only takes effect after restart | Restart Claude Code or `/mcp` → reconnect. Key rotation self-heals after a 401 if ovcli.conf changed on disk. |
| `recall is pinned to the legacy /search/recall endpoint` | One 4xx mentioning "mode" pins recall for 6h | `rm ~/.openviking/state/context-face.json`. |
| `no hook log … debug is on but no hook has run` | Hooks are not being spawned at all | Registration/enablement/node problem, not a server problem. |
| `ov.conf has a top-level 'claude_code' block` / `'codex' block` / `server.url is rejected` | Plugin-only keys in the server's own config; the server refuses to start at its next restart (`Unknown config field` / `Extra inputs are not permitted`) | Move them to ovcli.conf (`plugin.<harness>`, `url`) and delete them from ov.conf. Ignore only if this ov.conf never starts a server. |
| `nothing listens on port … — the server is not running` | Server down or never started; a stale `.openviking.pid` means it died | Start it (`openviking-server`; first time `openviking-server init`) in a terminal and read the startup output. Ask before restarting a server the user runs. |
| `/ready: embedding → error …` | The running server cannot call its embedding provider: recall searches nothing, commits extract nothing | Fix `embedding.*` (api_key/api_base/model) in ov.conf and restart; `openviking-server doctor` prints the provider's reply. |
| `/ready: vectordb → …` / `/ready: agfs → …` | Storage broken: disk full, two servers on one workspace, corrupted index | Server log; stop the duplicate; free disk. |
| `server is still initializing (503 /ready)` | First start downloads a local embedding model, or init is slow | Wait and rerun; if it never finishes, the server log. |
| `docker container is up but has no ov.conf` | Official image started without a config mount (every request 503s) | Mount `~/.openviking` at `/app/.openviking`, or `docker exec -it openviking openviking-server init`. |

More symptoms, exact error strings and log stage names: [reference.md](reference.md).

## Step 3 — targeted checks (only when the report is not conclusive)

Prove hooks run at all. `OPENVIKING_DEBUG=1` must reach Claude Code's own
process — a shell export does not, the `env` block in `~/.claude/settings.json`
does. Then send one prompt in a new session and read
`~/.openviking/logs/cc-hooks.log` (JSONL; grep `"error"` and
`"hook":"mcp-proxy","stage":"start"`). A `subagent-stop transcript_read ENOENT`
line is harmless noise.

Prove the key and identity by hand (`Bearer` is case-sensitive with exactly one
space):

```bash
URL=<url>; KEY=<key>
curl -sS "$URL/health"                                  # reachability, version, auth_mode
curl -sS -H "Authorization: Bearer $KEY" "$URL/health"  # 200 without account_id/user_id/role ⇒ key invalid
curl -sS -w '\n%{http_code}\n' -H "Authorization: Bearer $KEY" "$URL/api/v1/system/status"  # real 401/403
```

Prove `/mcp` directly (stateless — no initialize needed):

```bash
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$URL/mcp" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H "Authorization: Bearer $KEY" -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

Prove Claude Code's MCP wiring: `claude mcp list` (slow, 30–60s) must show
`plugin:openviking-memory:openviking: node <installPath>/servers/mcp-proxy.mjs - ✔ Connected`;
`claude mcp get 'plugin:openviking-memory:openviking'` needs the full quoted
name. `/mcp` inside a session reconnects the proxy.

Prove a capture landed: take `ov_session_id` from
`~/.openviking/state/last-capture.json` and `GET $URL/api/v1/sessions/<id>`
(or `ov session get <id>`); `total_message_count` should be ≥ the captured
turns, `commit_count > 0` proves extraction ran.

Second opinion from the CLI: `ov health` and `ov config validate` read the
same `~/.openviking/ovcli.conf` (they show account/user/role for the key).
`ov config list` reveals whether a different profile was switched in with
`ov config switch` — that also retargets the plugin.

Recall corpus probe: `node ${CLAUDE_PLUGIN_ROOT}/scripts/debug-recall.mjs "<query>"`
prints config, health and raw `/search/find` hits. It is a connectivity and
corpus check, not a replay of the hook's exact ranking.

Server side (only meaningful when the server runs on this machine):

```bash
curl -sS "$URL/ready"                       # agfs / vectordb / api_key_manager / embedding (live probe, ≤10s) / ollama
openviking-server doctor                    # in the server's own environment: config, native engine, embedding probe, VLM key, disk
lsof -nP -iTCP:1933 -sTCP:LISTEN            # who owns the port
docker logs --tail 100 openviking           # container deployments
```

`log.output` defaults to stdout, so the server log is the terminal/tmux pane
it runs in, the nohup file, `journalctl -u openviking`, or `docker logs`;
with `"file"` it is `<storage.workspace>/log/openviking.log`. A server that
exits at startup prints the reason right there — running `openviking-server`
in the foreground is the quickest way to see it. It is the user's process:
ask before stopping or restarting it.

## Fixing

- Edit `~/.openviking/ovcli.conf` only with the user's agreement; show the
  change, keep unknown keys, keep mode 0600, never echo the key.
- Prefer env vars in the `env` block of `~/.claude/settings.json` for
  machine-wide overrides; a shell rc export only reaches sessions started from
  that shell and silently outranks the file everywhere.
- After changing `url`, installing or updating the plugin: restart Claude
  Code. Hooks re-read config per invocation, the MCP proxy does not.
- Updates: GitHub installs → `claude plugin marketplace update openviking && claude plugin update openviking-memory@openviking`;
  TOS/archive and dev-checkout installs → re-run the installer.
- Confirm the fix by rerunning the doctor, then by observing an
  `<openviking-context>` block on the next prompt.

## Documentation

- Source and issues: <https://github.com/volcengine/OpenViking> — plugin code lives under `examples/`, the shared installer under `examples/memory-plugin-shared/`.
- Documentation index (LLM-friendly): <https://docs.openviking.ai/llms.txt>; the harness integration pages under it cover install paths, configuration keys and known limitations.
- `https://api.vikingdb.cn-beijing.volces.com/openviking` is the Volcengine-hosted OpenViking Service (OpenViking Cloud): the path prefix is part of the base url, it accepts `Authorization: Bearer` only, and its keys are issued from the Volcengine console rather than by a self-hosted admin.

## Rules

- Never print a full API key or the raw contents of `ovcli.conf`; the
  doctor's masked forms are the limit.
- Never run `scripts/debug-capture.mjs` with a live Claude Code session id —
  it overwrites that session's capture cursor and uses an obsolete API flow.
- `scripts/setup.mjs` needs a TTY and, on older plugin versions, an existing
  `ovcli.conf`; on a fresh machine write the file directly or re-run the installer.
- Never pipe `claude plugin list` into `grep -q`; capture the output first
  (early exit under `pipefail` reads as "not installed").
- `/health` returning 200 proves reachability only. Auth is proven by the
  identity fields with a key, or by `/api/v1/system/status`.
- `ov doctor` is `openviking-server doctor`: it validates the server's own
  config and providers in the server's Python environment, makes a live
  embedding call, and says nothing about this plugin. Run it for provider-level
  failures, not first.
- The doctor only reads. Never stop, restart or `kill` the user's server, or
  edit `ov.conf`, without asking.
- Older proxy versions say "check that 'ov serve' is running" — there is no
  such command; the server is started with `openviking-server`.
- `OPENVIKING_MEMORY_ENABLED=0` disables the hooks but not the MCP tools;
  `OPENVIKING_MCP_URL` has no effect on this plugin.
