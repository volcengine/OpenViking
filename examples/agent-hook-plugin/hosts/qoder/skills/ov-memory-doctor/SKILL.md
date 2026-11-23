---
name: ov-memory-doctor
description: >
  Diagnose and fix the OpenViking memory integration on this machine: the install (hooks, MCP server, skills), the client configuration (ovcli.conf / ov.conf / OPENVIKING_* env) and the connection to the OpenViking server (reachability, 401/403, /mcp). Use whenever memory "isn't working": no <openviking-context> block, empty recall, captures not landing, missing or failing MCP memory tools, 401/403, right after installing or updating the integration or switching servers/keys, or when the user asks for its status. Triggers: "memory not working", "check OpenViking", "plugin status", "记忆没生效", "插件状态", "连不上 OpenViking", "recall 为空", "401".
---

# OpenViking Memory Doctor

Troubleshooting for the OpenViking memory integration in Cursor, TRAE, TRAE CN, ZCode, Antigravity, Kimi Code, CodeBuddy Code and Qoder CLI. Three things go wrong on a user's machine, each silently:

- **Install** — the hooks and the MCP server the installer wrote into the host's configuration (Cursor, TRAE, ZCode) or shipped as a host plugin (Antigravity, Kimi Code, CodeBuddy Code, Qoder CLI), the runtime assembled beside them, and the skills. When these are wrong, hooks never run and nothing is logged anywhere.
- **Configuration** — `~/.openviking/ovcli.conf`, `~/.openviking/ov.conf` and `OPENVIKING_*` environment variables, resolved as env → ovcli.conf → ov.conf → defaults. A malformed file reads as "no config" and the integration silently falls back to `http://127.0.0.1:1933` with no key; a stray env var silently overrides the file.
- **Connection** — on a self-hosted server `/health` answers 200 even with a bad key, so everything can look reachable while every real request 401s. On OpenViking Cloud the gateway authenticates `/health` and `/ready` too, so the same probe answers 401 without a key and says nothing about the url.

When the resolved url is loopback, the server runs on this machine and the doctor adds a **Server health** section: whether anything listens on the port, plugin-only keys in `ov.conf` that make the server refuse to start, and `GET /ready`, the server's own per-subsystem verdict. For a remote server only `/ready` is probed. Everything else on the server side is `openviking-server doctor`, run in the server's Python environment.

## Step 1 — run the doctor

Run the copy installed for the host this session runs in:

| Host | Command |
|---|---|
| Cursor | `node ~/.openviking/agent-integrations/cursor/scripts/ov-memory-doctor.mjs` |
| TRAE | `node ~/.openviking/agent-integrations/trae/scripts/ov-memory-doctor.mjs` |
| TRAE CN | `node ~/.openviking/agent-integrations/trae-cn/scripts/ov-memory-doctor.mjs` |
| ZCode | `node ~/.openviking/agent-integrations/zcode/scripts/ov-memory-doctor.mjs` |
| Antigravity | `node ~/.openviking/agent-integrations/antigravity/scripts/ov-memory-doctor.mjs` |
| Kimi Code | `node "${KIMI_CODE_HOME:-$HOME/.kimi-code}/plugins/managed/openviking-memory/agent-integrations/kimicode/scripts/ov-memory-doctor.mjs"` |
| CodeBuddy Code | `node ~/.openviking/agent-integrations/codebuddy/marketplace/openviking-memory/agent-integrations/codebuddy/scripts/ov-memory-doctor.mjs` |
| Qoder CLI | `node ~/.openviking/agent-integrations/qoder/marketplace/openviking-memory/agent-integrations/qoder/scripts/ov-memory-doctor.mjs` |

With `OPENVIKING_HOME` set, the copies live under it instead of `~/.openviking`. Options: `--json` (machine-readable), `--offline` (skip network probes), `--timeout <ms>` (per probe, default 5000), `--no-color`.

The report has seven sections — Environment, Plugin install, Configuration, Workspace, Connection, Server health, Recent activity — and a Summary listing every ✗/⚠ with a fix. Read the whole report before acting: one root cause usually shows up in several sections (a bad key → "api key rejected" + "system/status → 401").

The API key is never printed in full. Three-segment keys are shown as `account=<a> user=<u> secret=abcd…wxyz` (the first two segments are base64url identity, decoded so the user can see which account/user the key claims). Other shapes are shown as `abcd…wxyz (N chars, unsupported format)`. Keep it that way in anything you show the user.

## Step 2 — map findings to causes

Work top-down; fix the first ✗ and rerun before chasing the next.

| Doctor finding | Meaning | Action |
|---|---|---|
| `plugin files missing` / `the shared runtime is not assembled beside this integration` | A partial or interrupted install; the first hook dies on a missing module and the host reports nothing | Re-run the installer (`curl -fsSL https://openviking.ai/install \| bash -s -- --harness <client>`). |
| `<hooks file> not found` / `has no OpenViking hooks` | The integration was never installed for this host, or the host's config was rewritten without the OpenViking entries | Re-run the installer for this host. |
| `<hooks file> cannot be parsed` | The host's config is not valid JSON; the installer refuses to overwrite a file it cannot read | Fix the JSON with the user, then re-run the installer. |
| `an installed hook names a script that is not on disk` / `the MCP server points at a proxy that is not on disk` | The runtime directory was moved or deleted after install | Re-run the installer. |
| `<host> has not loaded this plugin` | The plugin is assembled on disk but the host does not list it as installed and enabled. CodeBuddy Code, Qoder CLI: the host CLI was not on PATH at install time, or the plugin was uninstalled or disabled in the host. Kimi Code: its entry in `plugins/installed.json` is missing or disabled | If the plugin was disabled, re-enable it in the host; the installer keeps that choice. Otherwise re-run the installer, for CodeBuddy Code and Qoder CLI with the host CLI on PATH. |
| `ZCode's hook runner is off` | `hooks.enabled` is not `true` in `~/.zcode/cli/config.json`, so no hook fires | Re-run the installer, or set `hooks.enabled = true`. |
| `no 'openviking' MCP server` / `<mcp file> not found` | Hooks may work, but the memory tools are not wired up | Re-run the installer. |
| `the installer wrote no MCP server` (info) | Antigravity already had an `openviking` server in `~/.gemini/config/mcp_config.json`, and the installer left it as the only one | Nothing to fix; MCP findings then concern that server, not the plugin. |
| `the MCP server is tagged for <other client>` | The proxy reads its configuration under another client's name | Re-run the installer for this host. |
| `skills missing from …` | A skill was removed, or the installer kept a same-named directory it did not write | Re-run the installer; move the unmanaged directory aside first if the user agrees. |
| `this copy was installed for <other client>` | The doctor was run from another host's copy | Run the command for this host from Step 1. |
| `ovcli.conf cannot be parsed` / `no usable config` | Trailing comma/comment; the file is treated as absent and the localhost default applies | Fix the JSON. Fresh machine: create `~/.openviking/ovcli.conf` with `url` + `api_key`, `chmod 600`. |
| `node is not on PATH` / node < 18 | Hooks and the MCP server run `node`; GUI launches often lack nvm/volta shims | Put node on PATH for the environment that launches the host. |
| `server unreachable` (refused / dns / timeout / tls) | Wrong url/port, server down, DNS/VPN, private CA | Compare with `curl -sS <url>/health`; curl OK + doctor fails ⇒ proxy or CA issue (Step 3). |
| `base URL ends with /api/v1` or `/mcp`, no scheme, `GET /health → 404` | url shape wrong; Cloud needs the `/openviking` prefix | Fix `url` to the API root. |
| `/health is authenticated at this deployment` (info) / `api key rejected on /health → 401` | OpenViking Cloud gates `/health` and `/ready` at the gateway | The info line needs no fix. The ✗ means the gateway rejected this key: use one issued for that deployment. |
| `api key rejected` then `system/status → 401 Invalid API Key` | Key invalid/revoked/for another deployment | Get the key re-issued; check nothing overrides ovcli.conf ("← env" in the Configuration section). |
| `using the ROOT api key` / `403 ROOT API keys cannot access tenant-scoped data APIs` | `api_key` fell through to `ov.conf server.root_api_key`, or the root key was pasted | Use a user/admin key. |
| `server is in trusted mode and needs account + user` | Server identity comes from headers | Set `account` and `user` in ovcli.conf. |
| `configured account/user differ from the key's identity` | In `api_key` mode the server ignores the identity headers; data lands under the key's identity | Remove them or use a key for that identity. |
| `POST /mcp tools/list → 404/406/502/504` while /health is fine | Reverse proxy not forwarding `/mcp`, rewriting `Accept`, or buffering SSE | Fix the proxy (`proxy_buffering off`, forward `/mcp`, pass `Authorization`). |
| `proxy variables set` + curl works but hooks fail | Node's fetch ignores `HTTP(S)_PROXY`; no CA handling | `NODE_USE_ENV_PROXY=1` or `NODE_EXTRA_CA_CERTS=<ca.pem>` in the environment that launches the host. |
| `one or more injection paths are switched off` | `OPENVIKING_AUTO_RECALL=0` / `OPENVIKING_AUTO_CAPTURE=0` or the matching `plugin.<client>` setting | Switch it back on if the user did not intend it. |
| `no hook state … yet` / `no hook log … debug is on but no hook has run` | The host is not spawning the hooks at all | An install, registration or node problem, not a server problem. |
| `no session has ever captured a turn` | The Stop hook runs but writes fail | Fix the Connection findings; failed writes are queued and replayed at the next session start. |
| `ov.conf has a top-level '<harness>' block` / `server.url is rejected` | Plugin-only keys in the server's own config; the server refuses to start at its next restart | Move them to ovcli.conf (`plugin.<client>`, `url`) and delete them from ov.conf. |
| `nothing listens on port … — the server is not running` | Server down or never started | Start it (`openviking-server`; first time `openviking-server init`). Ask before restarting a server the user runs. |
| `/ready: embedding → error …` | The running server cannot call its embedding provider: recall searches nothing, commits extract nothing | Fix `embedding.*` in ov.conf and restart; `openviking-server doctor` prints the provider's reply. |
| `server is still initializing (503 /ready)` | First start downloads a local embedding model, or init is slow | Wait and rerun; if it never finishes, read the server log. |

## Step 3 — targeted checks (only when the report is not conclusive)

Prove hooks run at all: put `OPENVIKING_DEBUG=1` in the environment that launches the host, run one turn, then read `~/.openviking/logs/<client>-hooks.log` (JSONL; grep `"error"`). An absent or unchanged log after a full turn means the hooks were not spawned.

Prove the key and identity by hand (`Bearer` is case-sensitive with exactly one space):

```bash
URL=<url>; KEY=<key>
curl -sS "$URL/health"                                  # reachability, version, auth_mode (401 on Cloud: the gateway gates /health)
curl -sS -H "Authorization: Bearer $KEY" "$URL/health"  # 200 without account_id/user_id/role ⇒ key invalid
curl -sS -w '\n%{http_code}\n' -H "Authorization: Bearer $KEY" "$URL/api/v1/system/status"  # real 401/403
```

Prove `/mcp` directly (stateless — no initialize needed):

```bash
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$URL/mcp" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H "Authorization: Bearer $KEY" -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

Prove a capture landed: session state lives in `~/.openviking/hook-state/<client>/`, and the OpenViking session id is the host's own session id behind a prefix (`cu-` Cursor, `tr-` TRAE, `trcn-` TRAE CN, `zc-` ZCode, `ag-` Antigravity, `kc-` Kimi Code, `cb-` CodeBuddy Code, `qd-` Qoder CLI). `GET $URL/api/v1/sessions/<id>` (or `ov session get <id>`) should show the captured messages, and `commit_count > 0` proves extraction ran.

Second opinion from the CLI: `ov health` and `ov config validate` read the same `~/.openviking/ovcli.conf`. `ov config list` reveals whether a different profile was switched in with `ov config switch`, which also retargets the integration.

Server side (only meaningful when the server runs on this machine):

```bash
curl -sS "$URL/ready"                       # agfs / vectordb / api_key_manager / embedding / ollama
openviking-server doctor                    # in the server's own environment: config, native engine, embedding probe, VLM key, disk
lsof -nP -iTCP:1933 -sTCP:LISTEN            # who owns the port
```

## Fixing

- Edit `~/.openviking/ovcli.conf` only with the user's agreement; show the change, keep unknown keys, keep mode 0600, never echo the key.
- Install problems are fixed by re-running the installer for this host, not by editing the host's hook or MCP configuration by hand.
- After changing `url`, installing or updating: restart the host or start a new session. Hooks re-read config per invocation, the MCP proxy does not.
- Confirm the fix by rerunning the doctor, then by observing an `<openviking-context>` block on the next prompt.

## Documentation

- Source and issues: <https://github.com/volcengine/OpenViking> — the integration lives under `examples/agent-hook-plugin`, the shared installer under `examples/memory-plugin-shared/`.
- Documentation index (LLM-friendly): <https://docs.openviking.ai/llms.txt>; the harness integration pages under it cover install paths, configuration keys and known limitations.
- `https://api.vikingdb.cn-beijing.volces.com/openviking` is the Volcengine-hosted OpenViking Service (OpenViking Cloud): the path prefix is part of the base url, it accepts `Authorization: Bearer` only, and its keys are issued from the Volcengine console rather than by a self-hosted admin.

## Rules

- Never print a full API key or the raw contents of `ovcli.conf`; the doctor's masked forms are the limit.
- `/health` returning 200 proves reachability only. Auth is proven by the identity fields with a key, or by `/api/v1/system/status`.
- `ov doctor` is `openviking-server doctor`: it validates the server's own config and providers, and says nothing about this integration. Run it for provider-level failures, not first.
- The doctor only reads. Never stop, restart or `kill` the user's server, or edit `ov.conf`, without asking.
