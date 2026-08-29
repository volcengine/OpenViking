---
name: ov-memory-doctor
description: >
  Diagnose and fix the OpenViking memory plugin for Codex on this machine: the
  plugin install (marketplace, config.toml enablement, hook trust records, MCP
  server), the client configuration (ovcli.conf / ov.conf / OPENVIKING_* env —
  which one wins, what the API key claims) and the connection to the OpenViking
  server (reachability, 401/403, /mcp). Use whenever memory "isn't working":
  no <openviking-context> block appears, recall is always empty, turns are not
  captured, MCP memory tools are missing or fail, 401/403 errors, right after
  installing/updating the plugin or switching servers/keys, or when the user
  asks for the plugin's status. Trigger phrases include "memory not working",
  "check OpenViking", "plugin status", "记忆没生效", "插件状态", "连不上
  OpenViking", "recall 为空", "401", "0 memories
  extracted". When the server runs on this machine (loopback url) it also
  checks the port, plugin-only keys in ov.conf that stop the server from
  starting, and `GET /ready`; everything else server-side stays with
  `openviking-server doctor`.
---

# OpenViking Memory Doctor (Codex)

Troubleshooting for the OpenViking memory plugin. Three things go
wrong on a user's machine, each silently:

- **Install** — marketplace registration, `[plugins."openviking-memory@openviking"]`
  and `[features] plugin_hooks` in `~/.codex/config.toml`, per-hook trust
  records, the stdio MCP proxy. When these are wrong, hooks never run and
  nothing is logged anywhere.
- **Configuration** — `~/.openviking/ovcli.conf`, `~/.openviking/ov.conf` and
  `OPENVIKING_*` environment variables. A malformed file reads as "no config"
  and the plugin silently falls back to `http://127.0.0.1:1933` with no key; a
  stray env var silently overrides the file.
- **Connection** — `/health` answers 200 even with a bad key, so everything
  can look reachable while every real request 401s.

When the resolved url is loopback, the server runs on this machine and the
doctor adds a **Server health** section: whether anything listens on the
port, plugin-only keys in `ov.conf` (`claude_code`, `codex`, `server.url`)
that make the server refuse to start, and `GET /ready` — the server's own
per-subsystem verdict (agfs, vectordb, api keys, embedding, ollama). For a
remote server only `/ready` is probed. Everything else on the server side —
config validation, live embedding probe, native engine, disk — is
`openviking-server doctor`, run in the server's Python environment.

## Step 1 — run the doctor

The script ships inside the plugin. Codex runs hooks from the plugin cache,
so run the doctor from there (newest version directory):

```bash
PLUGIN_DIR=$(ls -d ~/.codex/plugins/cache/openviking/openviking-memory/*/ | sort -V | tail -1)
node "$PLUGIN_DIR/scripts/ov-memory-doctor.mjs"
```

If that directory does not exist, fall back to the marketplace copy reported by
`codex plugin list --json` (`.installed[] | select(.pluginId == "openviking-memory@openviking") | .source.path`),
or to a source checkout's `examples/codex-memory-plugin`.

Options: `--json` (machine-readable), `--offline` (skip network probes),
`--timeout <ms>` (per probe, default 5000), `--no-color`.

The report has six sections — Environment, Plugin install, Configuration,
Connection, Server health, Recent activity — and a Summary listing every ✗/⚠
with a fix.
Read the whole report before acting: one root cause usually shows up in
several sections (a bad key → "api key rejected" + "system/status → 401").

The API key is never printed in full. Three-segment keys are shown as
`account=<a> user=<u> secret=abcd…wxyz` (the first two segments are base64url
identity, decoded so the user can see which account/user the key claims);
legacy keys as `abcd…wxyz (64 chars)`. Keep it that way in anything you show
the user.

## Step 2 — map findings to causes

Work top-down; fix the first ✗ and rerun before chasing the next.

| Doctor finding | Meaning | Action |
|---|---|---|
| `ovcli.conf cannot be parsed` / `no usable config` | Trailing comma/comment; the plugin treats the file as absent and uses the localhost default | Fix the JSON. Fresh machine: create `~/.openviking/ovcli.conf` with `url` + `api_key`, `chmod 600`. |
| `codex plugin list does not show …` | Plugin never installed, or installed under an old id | Re-run the one-line installer (`bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) --harness codex`; add `--dist tos` where GitHub is blocked). |
| `[features] plugin_hooks is not set/false` | Codex never runs plugin hooks | Add `plugin_hooks = true` under `[features]` in `~/.codex/config.toml`, restart Codex. |
| `[plugins."…"] enabled = false` / `installed but disabled` | Plugin switched off in config.toml | Set `enabled = true`, restart Codex. |
| `hooks disabled in [hooks.state]` | A hook was declined at the trust prompt | Remove `enabled = false` from that `[hooks.state."openviking-memory@openviking:hooks/hooks.json:<event>:0:0"]` section; approve the hook again. |
| `hooks without a trust record yet` | Codex has not yet approved those hooks (fresh install or `hooks.json` changed on update) | Start a Codex session and accept the hook prompt; nothing is wrong. |
| `marketplace 'openviking' is not registered` / root missing | Marketplace removed or its clone deleted | Re-run the installer. |
| `plugin.json does not declare skills` | Old plugin copy; `$ov-memory-doctor` and the other bundled skills are not loaded | Update the plugin. |
| `cached plugin X differs from this copy` | The doctor was run from a checkout while Codex runs another version | Rerun from the cache path (Step 1). |
| `node is not on PATH` / node < 18 | Hooks and `.mcp.json` run the bare command `node` | Put node on PATH for the environment that launches Codex. |
| `credential source: env` unexpectedly | A stray `OPENVIKING_*` var forces env mode; `ov config switch` and ovcli.conf edits do nothing | Unset the var (check shell rc files and `~/.openviking/codex-plugin.rc.sh` residue). |
| `OPENVIKING_MEMORY_ENABLED has no effect` | That switch is Claude Code only | `OPENVIKING_AUTO_RECALL=0` / `OPENVIKING_AUTO_CAPTURE=0`, or `codex plugin remove`. |
| `server unreachable` (refused / dns / timeout / tls) | Wrong url/port, server down, DNS/VPN, private CA | Compare with `curl -sS <url>/health`; curl OK + doctor fails ⇒ proxy or CA issue (Step 3). |
| `base URL ends with /api/v1` or `/mcp`, no scheme, `GET /health → 404` | url shape wrong; Cloud needs the `/openviking` prefix | Fix `url` to the API root. |
| `api key rejected` then `system/status → 401 Invalid API Key` | Key invalid/revoked/for another deployment | Get the key re-issued; check nothing overrides ovcli.conf ("← env"). |
| `using the ROOT api key` / `403 ROOT API keys cannot access tenant-scoped data APIs` | `api_key` fell through to `ov.conf server.root_api_key`, or the root key was pasted | Use a user/admin key. |
| `plugin auth mode 'trusted' differs from the server's 'api_key'` | account/user set in ovcli.conf ⇒ the plugin sends identity headers, which an `api_key` server ignores; data lands under the key's identity | Remove account/user (or set `OPENVIKING_AUTH_MODE=api_key`), or use a key for that identity. |
| `server is in trusted mode and needs account + user` | Server identity comes from headers | Set `account` and `user` in ovcli.conf. |
| `recall/capture timeout exceeds the hook budget` | Codex kills the hook before the request finishes | Lower `OPENVIKING_RECALL_TIMEOUT_MS` / `OPENVIKING_CAPTURE_TIMEOUT_MS`. |
| `POST /mcp tools/list → 404/406/502/504` while /health is fine | Reverse proxy not forwarding `/mcp`, rewriting `Accept`, or buffering SSE | Fix the proxy (`proxy_buffering off`, forward `/mcp`, pass `Authorization`). |
| `proxy variables set` + curl works but hooks fail | Node's fetch ignores `HTTP(S)_PROXY`; no CA handling | `NODE_USE_ENV_PROXY=1` or `NODE_EXTRA_CA_CERTS=<ca.pem>` in the environment that launches Codex. |
| `no session has ever captured a turn` / idle sessions piling up | Stop hook runs but writes fail, or commits fail | Fix the Connection findings; state files are kept and replay when the server is back. |
| `MCP proxy last started against <other url>` | The proxy is a long-lived process; a changed url only takes effect after restart | Restart Codex. |
| `ov.conf has a top-level 'claude_code' block` / `'codex' block` / `server.url is rejected` | Plugin-only keys in the server's own config; the server refuses to start at its next restart (`Unknown config field` / `Extra inputs are not permitted`) | Move them to ovcli.conf (`plugin.<harness>`, `url`) and delete them from ov.conf. Ignore only if this ov.conf never starts a server. |
| `nothing listens on port … — the server is not running` | Server down or never started; a stale `.openviking.pid` means it died | Start it (`openviking-server`; first time `openviking-server init`) in a terminal and read the startup output. Ask before restarting a server the user runs. |
| `/ready: embedding → error …` | The running server cannot call its embedding provider: recall searches nothing, commits extract nothing | Fix `embedding.*` (api_key/api_base/model) in ov.conf and restart; `openviking-server doctor` prints the provider's reply. |
| `/ready: vectordb → …` / `/ready: agfs → …` | Storage broken: disk full, two servers on one workspace, corrupted index | Server log; stop the duplicate; free disk. |
| `server is still initializing (503 /ready)` | First start downloads a local embedding model, or init is slow | Wait and rerun; if it never finishes, the server log. |
| `docker container is up but has no ov.conf` | Official image started without a config mount (every request 503s) | Mount `~/.openviking` at `/app/.openviking`, or `docker exec -it openviking openviking-server init`. |

More symptoms, exact error strings and log stage names: [reference.md](reference.md).

## Step 3 — targeted checks (only when the report is not conclusive)

Prove hooks run at all: put `OPENVIKING_DEBUG=1` in the environment that
launches Codex, run one turn, then read `~/.openviking/logs/codex-hooks.log`
(JSONL; grep `"error"`). An absent or unchanged log after a full turn means the
hooks were not spawned — a `plugin_hooks` / trust / node problem, not a server
problem. `~/.openviking/logs/cc-hooks.log` belongs to the Claude Code plugin.

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

Prove a capture landed: the state file for the session in
`~/.openviking/codex-plugin-state/<session_id>.json` carries `ovSessionId`
(`cx-<session_id>`) and `capturedTurnCount`; `GET $URL/api/v1/sessions/cx-<session_id>`
(or `ov session get cx-<session_id>`) should show `total_message_count ≥` that
count, and `commit_count > 0` proves extraction ran.

Second opinion from the CLI: `ov health` and `ov config validate` read the
same `~/.openviking/ovcli.conf` (they show account/user/role for the key).
`ov config list` reveals whether a different profile was switched in with
`ov config switch` — that retargets the plugin too, unless env vars win.

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
- Edit `~/.codex/config.toml` only for the specific keys named above.
- After changing `url`, installing or updating the plugin: restart Codex.
  Hooks re-read config per invocation, the MCP proxy does not.
- Updates: GitHub/TOS git marketplaces → `codex plugin marketplace upgrade openviking`
  (keeps the pinned ref; the installer re-registers with the current ref);
  local directory marketplaces → re-run the installer.
- Confirm the fix by rerunning the doctor, then by observing an
  `<openviking-context>` block on the next prompt.

## Documentation

- Source and issues: <https://github.com/volcengine/OpenViking> — plugin code lives under `examples/`, the shared installer under `examples/memory-plugin-shared/`.
- Documentation index (LLM-friendly): <https://docs.openviking.ai/llms.txt>; the harness integration pages under it cover install paths, configuration keys and known limitations.
- `https://api.vikingdb.cn-beijing.volces.com/openviking` is the Volcengine-hosted OpenViking Service (OpenViking Cloud): the path prefix is part of the base url, it accepts `Authorization: Bearer` only, and its keys are issued from the Volcengine console rather than by a self-hosted admin.

## Rules

- Never print a full API key or the raw contents of `ovcli.conf`; the
  doctor's masked forms are the limit.
- `scripts/setup.mjs` needs a TTY and, on older plugin versions, an existing
  `ovcli.conf`; on a fresh machine write the file directly or re-run the installer.
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
- Do not read `~/.openviking/state/*.json` for a Codex verdict — those files
  are written by the Claude Code plugin.
