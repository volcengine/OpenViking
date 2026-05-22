---
name: install-openviking-memory
description: >
  Install and configure the OpenViking long-term memory plugin for OpenClaw via natural conversation.
  Once installed, the plugin automatically captures important facts from chats and recalls relevant
  context before each reply (auto-capture + auto-recall, cross-session). Covers prerequisites check,
  plugin install through OpenClaw's plugin manager first, with ov-install only as a backup path,
  wizard-based configuration, slot activation,
  gateway restart, verification, plus multi-tenant root-key support, multi-instance, and uninstall.
  Trigger when the user says any of: "install OpenViking", "set up memory", "configure memory plugin",
  "add long-term memory", "connect to OpenViking server", "RAG", "semantic memory",
  "帮我装 OpenViking", "配置记忆插件", "安装记忆功能", "接入 OpenViking", "我有一台 OpenViking 服务器".
  The user does NOT need to know any CLI commands — the agent runs everything and only asks for a few values.
  This skill assumes the OpenViking server is already running. If the server is not ready, the skill
  tells the user to contact their admin or set it up via the OpenViking docs — it does NOT install the server.
version: 2.0.1
metadata:
  openclaw:
    requires:
      bins:
        - node
        - openclaw
  emoji: "🦣"
  homepage: "https://github.com/volcengine/OpenViking"
tags:
  - install
  - setup
  - memory
  - openviking
  - rag
  - long-term-memory
---

# Install OpenViking Memory — Agent Installation & Reference Skill

You are about to help a user install and configure the OpenViking memory plugin for OpenClaw.
The user does NOT know how to use a terminal. You will collect a few values through natural conversation, then run **all** commands yourself using your shell tool.

**Read this entire document before starting. Follow every step in order. Do NOT skip steps.**

This skill is split into two halves:

- **Part 1 (STEP 0–10): Natural-language installation walkthrough.** Use this on first install or reconfigure.
- **Part 2: Reference.** Tools, config schema, multi-tenant, multi-instance, daily ops, uninstall, error recovery. Read on demand.

> **Server scope.** This skill **does not** install the OpenViking server itself. It assumes the server is already running locally on `127.0.0.1:1933` or on another machine. If the user has no server, see "Server not ready" handling in STEP 5.

---

# Part 1 — Natural-Language Installation Walkthrough

## STEP 0: Detect Language

If the user's first message contains Chinese characters, respond in **Chinese** throughout.
Otherwise respond in **English**.
All user-facing messages below have (CN)/(EN) variants — use the matching one.

Do NOT show this step to the user.

---

## STEP 1: Detect Operating System (silently)

Run this command and remember the result. Do NOT show it to the user.

```bash
uname -s 2>/dev/null || echo WINDOWS
```

- Output contains `Darwin` → `OS=mac`
- Output contains `Linux` → `OS=linux`
- Output is `WINDOWS` or the command fails → `OS=windows`

---

## STEP 2: Check Prerequisites (silently)

Run silently:

```bash
node -v
openclaw --version
```

**If `node` is missing:**

> (CN) 你的系统没有安装 Node.js。OpenClaw 和 OpenViking 插件需要 Node.js >= 22。请先安装 Node.js，然后再回来找我。
> (EN) Node.js is not installed. OpenClaw and the OpenViking plugin require Node.js >= 22. Please install Node.js first, then come back.

**Stop. Do NOT continue.**

**If `openclaw` is missing:**

> (CN) 你的系统没有安装 OpenClaw。请先安装 OpenClaw（>= 2026.4.8），然后再回来找我。
> (EN) OpenClaw is not installed. Please install OpenClaw (>= 2026.4.8) first, then come back.

**Stop. Do NOT continue.**

If both exist, proceed to STEP 3 silently.

---

## STEP 3: Greet and Ask for 3 Values

Send this message:

> (CN) 好，我来帮你接入 OpenViking 长期记忆。装好之后，我会自动记住对话里的重要信息，下次聊也能回忆起来。
>
> 我需要 3 条信息，不知道的可以问你的管理员：
> 1. **OpenViking 服务地址** —— 例如 `https://ov.example.com` 或 `http://192.168.1.100:1933`，本机服务可以直接说"本机"
> 2. **API Key** —— 用来鉴权；服务没开认证可以说"没有"
> 3. **Agent 标识前缀**（可选） —— 用于区分多个 agent 的记忆命名空间，留空就用默认
>
> 先告诉我服务地址吧？

> (EN) I'll set up OpenViking long-term memory for you. Once configured, I'll automatically remember important info from our chats and recall it later.
>
> I need 3 things (ask your admin if unsure):
> 1. **OpenViking server URL** — e.g. `https://ov.example.com` or `http://192.168.1.100:1933`. For a local server, just say "local".
> 2. **API Key** — for auth. Say "none" if the server has no auth.
> 3. **Agent prefix** (optional) — used to namespace memories across agents. Leave blank for default.
>
> What's the server URL?

---

## STEP 4: Collect Values

Collect 3 values through natural conversation. Be flexible: if the user gives several at once, parse them all. If they correct something, accept the new value.

### 4a. `BASE_URL` (REQUIRED)

- "local" / "本机" / "localhost" → use `http://127.0.0.1:1933`.
- `ov.example.com` without protocol → prepend `https://`.
- Strip trailing `/`, `/health`, or `/api`.
- After normalization must start with `http://` or `https://`.
- If the user says they don't know, ask them to check with the admin or look at how the server was started. **Do NOT make up a URL.**

### 4b. `API_KEY` (OPTIONAL)

> (CN) API Key 是什么？服务没开认证就直接说"没有"。
> (EN) What's the API Key? Say "none" if the server has no auth.

- "no" / "none" / "没有" / "空" / empty → `API_KEY=""` (you will skip the flag later).
- Otherwise store as-is.

### 4c. `AGENT_PREFIX` (OPTIONAL)

> (CN) 想给这个 agent 一个记忆前缀吗？留空就用默认。只能用字母、数字、`_`、`-`。
> (EN) Want to set an agent prefix? Leave blank for the default. Letters, digits, `_`, `-` only.

- Empty / "default" / "默认" → leave unset (plugin defaults to `""`).
- Otherwise validate against `/^[A-Za-z0-9_-]+$/`. If invalid, ask again.

### 4d. (Conditional) Multi-Tenant Root-Key Fields

Only ask for these if STEP 7 detects a root key (`Root API key detected. Missing: --account-id, --user-id`). Don't ask up front.

- `ACCOUNT_ID`
- `USER_ID`

See **Reference: Multi-Tenant** for what these mean.

---

## STEP 5: Pre-flight Connectivity Check (silently)

Tell the user briefly:

> (CN) 我先测一下能不能连上服务……
> (EN) Let me test the connection to your server...

Run:

**If OS=windows:**

```powershell
try { (Invoke-WebRequest -Uri "BASE_URL/health" -TimeoutSec 10 -UseBasicParsing -ErrorAction Stop).StatusCode } catch { $_.Exception.Response.StatusCode.value__ }
```

**If OS=mac or OS=linux:**

```bash
curl -sS -o /dev/null -w "%{http_code}" --connect-timeout 10 "BASE_URL/health"
```

Replace `BASE_URL` with the actual value.

| Status | Meaning | Action |
|---|---|---|
| `200` | Server reachable, no auth on `/health` | Proceed to STEP 6. |
| `401` / `403` | Server reachable but `/health` requires auth | Proceed to STEP 6 — the wizard's key probe will sort it out. |
| `000` / timeout / connection refused | Server unreachable | **Server-not-ready handling** below. |
| Anything else | Unexpected | Show status code to the user, go back to STEP 4a. |

### Server-not-ready handling

This skill **does not install or operate the OpenViking server**. If the user's server is unreachable, present the situation honestly and offer two paths:

> (CN) ❌ 我连不上 `BASE_URL`。可能是：
> 1) 服务还没启动 —— 请联系你的 OpenViking 服务管理员把它起起来；如果是你自己负责，请参考 OpenViking 官方文档（`https://github.com/volcengine/OpenViking`）的 server 启动指引。
> 2) 地址不对 —— 你可以重新告诉我正确的地址。
> 3) 网络不通（防火墙 / VPN / 内网）—— 你确认一下网络。
>
> 也可以选择"先把配置写下来"，等服务起来就自动生效，要这么办吗？

> (EN) ❌ Cannot reach `BASE_URL`. Likely cause:
> 1) **Server isn't running** — please ask your OpenViking admin to start it. If you own the server, follow the OpenViking official docs (`https://github.com/volcengine/OpenViking`) to start it. **This skill does not install or run the server.**
> 2) **Wrong URL** — give me the correct URL.
> 3) **Network blocked** (firewall / VPN / private network) — please verify connectivity.
>
> Or I can save the config now (`--allow-offline`) so it will activate automatically once the server is up. Want me to do that?

If the user fixes the URL → back to STEP 4a.
If the user wants `--allow-offline` → remember `ALLOW_OFFLINE=true` and continue to STEP 6.
If the user gives up / cannot fix → stop here. Do NOT continue with a broken state.

---

## STEP 6: Install the Plugin

The plugin can be installed two ways. **Always try Path A first.** Use Path B only as a backup when Path A fails because ClawHub is unavailable, rate-limited, or authentication blocks anonymous install. For version conflicts, dependency errors, or other non-registry failures, stop and show the user the error instead of switching paths silently.

### Path A — Primary: `openclaw plugins install` (uses ClawHub)

Tell the user:

> (CN) 现在开始装插件……
> (EN) Installing the plugin now...

Run:

```bash
openclaw plugins install clawhub:@openviking/openclaw-plugin
```

Trigger fallback to Path B only if the output contains any of these strings:

- `429`
- `rate limit` / `rate-limited` / `Too Many Requests`
- `not logged in` / `please log in` / `please login` / `unauthorized` / `401` / `403` together with `clawhub`
- `ETIMEDOUT` / `ECONNRESET` on a `clawhub`-related host
- generic message indicating the registry refused an anonymous client

Before falling back, also try the explicit registry prefix once:

```bash
openclaw plugins install clawhub:@openviking/openclaw-plugin
```

If the install **succeeds**, jump to STEP 7.

If both attempts fail with one of the fallback-eligible errors above, go to Path B. If the failure is a version conflict, missing dependency, package validation error, or another non-registry error, stop and show the last 30 lines to the user.

### Path B — Backup: `ov-install` (bypasses ClawHub)

Tell the user:

> (CN) ClawHub 现在好像被限流、不可用，或者当前账号不能安装。我改用备用路径，通过 npm 下载并部署插件包。
> (EN) ClawHub looks rate-limited, unavailable, or blocked for this account. I'll use the backup path and install the plugin package from npm.

Run the installer with `npx` (no global install needed):

```bash
npx -y openclaw-openviking-setup-helper@latest --base-url BASE_URL [--api-key API_KEY] [--agent-prefix AGENT_PREFIX] [--account-id ACCOUNT_ID] [--user-id USER_ID]
```

Build the flag list according to what the user gave you:

- Always pass `--base-url BASE_URL`.
- Pass `--api-key API_KEY` only if `API_KEY` is non-empty.
- Pass `--agent-prefix AGENT_PREFIX` only if the user gave one.
- `--account-id` / `--user-id` only if the root-key path requires them.

`ov-install` will, in one shot:
1. Download the `@openviking/openclaw-plugin` package from npm into a temporary staging dir.
2. Copy the package into the OpenClaw `extensions/` dir and install plugin dependencies.
3. Register the plugin in `openclaw.json` (via `openclaw plugins enable` or direct write).
4. Run `openclaw openviking setup --json --base-url … [--api-key …]` for the user.
5. Return a non-zero exit if setup needs explicit `--allow-offline` or `--force-slot` consent.

This means **STEP 7 is effectively done by `ov-install`**. After `ov-install` exits 0, jump straight to **STEP 9** (gateway restart) and **STEP 10** (verify).

If `ov-install` exits non-zero, capture the last 30 lines of its output, show them to the user, and stop. Don't retry blindly.

---

## STEP 7: Configure (only on Path A — Path B did this for you)

Run the setup wizard non-interactively. Build flags from collected values:

```bash
openclaw openviking setup --base-url BASE_URL --json [--api-key API_KEY] [--agent-prefix AGENT_PREFIX] [--account-id ACCOUNT_ID] [--user-id USER_ID] [--allow-offline] [--force-slot]
```

Rules:

- `--base-url BASE_URL` is **required** under `--json`. Without it, the wizard prints `--json requires --base-url for non-interactive mode`.
- `--api-key` only if `API_KEY` is non-empty.
- `--agent-prefix` only if the user gave one. Use **`--agent-prefix`**, not `--agent-id` (deprecated and removed).
- `--account-id` / `--user-id` only after STEP 7 root-key detection (see below).
- `--allow-offline` only if the user explicitly approved it in STEP 5.
- `--force-slot` **never** in the first attempt. Add only after the user confirms (see slot_blocked handling below).

### Parse the JSON output

The wizard prints a single JSON object:

```json
{
  "success": true | false,
  "action": "configured" | "existing" | "error" | "slot_blocked",
  "config": { "mode": "remote", "baseUrl": "...", "apiKey": "...", "agent_prefix": "...", "accountId": "...", "userId": "..." },
  "health": { "ok": true, "status": 200 },
  "keyProbe": { "keyType": "user_key" | "root_key" | "none", "ok": true },
  "slot": { "ok": true, "owner": "openviking" },
  "error": "..."
}
```

### Decision matrix

| Condition | Action |
|---|---|
| `success: true` and `action: "configured"` or `"existing"` | Done. Proceed to STEP 9. |
| `success: false` and `action: "slot_blocked"` | **Slot conflict — see below.** |
| `success: false` and `error` contains `"Server unreachable"` | Connectivity broke between STEP 5 and STEP 7. Offer `--allow-offline`; if accepted, retry. Otherwise back to STEP 4a. |
| `success: false` and `error` contains `"Root API key detected"` and `"Missing: --account-id, --user-id"` | **Root-key path — see below.** |
| `success: false` and `error` contains `"Invalid API key"` / `keyProbe.keyType: "none"` with `ok: false` | API key wrong. Back to STEP 4b. |
| `success: false` and any other `action: "error"` | Show `error` to the user, stop. Do NOT pretend success. |

### Slot conflict (`slot_blocked`)

The error text looks like:

> `contextEngine slot is owned by "<other-plugin>". Config was saved but slot was NOT changed. Use --force-slot to replace.`

**Do NOT silently use `--force-slot`.** Ask the user:

> (CN) 你的 OpenClaw 当前 contextEngine 槽被 `<other-plugin>` 占着。如果用 OpenViking 替换它，`<other-plugin>` 就不再生效。要替换吗？
> (EN) Your `contextEngine` slot is currently owned by `<other-plugin>`. Activating OpenViking will disable it. Replace?

If the user agrees, retry the same setup command with `--force-slot` appended. If they decline, tell them config has been saved but the slot is unchanged, and stop.

### Root-key path

The error text looks like:

> `Root API key detected. Missing: --account-id, --user-id. Provide both to enable multi-tenant scoping.`

Ask the user:

> (CN) 你给的是 root 级 API Key，需要再补两个值才能用：账户 ID（accountId）和用户 ID（userId）。这两个一般是 OpenViking 服务管理员配的，不知道就问他们。
> (EN) Your API key is a root key, which needs two more values: `accountId` and `userId`. Both come from your OpenViking server admin — ask them if unsure.

After collecting, retry the setup command with `--account-id` and `--user-id` appended.

---

## STEP 8: (Reserved — done inside STEP 7 or by `ov-install`)

The setup wizard already wrote `plugins.entries.openviking.config.*` and (if successful) set `plugins.slots.contextEngine = "openviking"`. There is no separate STEP 8 — go to STEP 9.

---

## STEP 9: Restart the Gateway

```bash
openclaw gateway restart
```

If it fails, try once more with:

```bash
openclaw gateway --force
```

If both fail:

> (CN) Gateway 没能自动重启。请你手动跑一下 `openclaw gateway restart`。重启完告诉我，我来验证。
> (EN) Gateway didn't restart cleanly. Please run `openclaw gateway restart` manually, then tell me when it's done so I can verify.

Wait ~3 seconds before STEP 10.

---

## STEP 10: Verify

```bash
openclaw openviking status --json
```

Expected output:

```json
{
  "configured": true,
  "slotActive": true,
  "health": { "ok": true },
  "config": { "baseUrl": "...", "agent_prefix": "..." }
}
```

### Decision

- `configured: true` **and** `slotActive: true` **and** `health.ok: true` → **success.** Send the success message below.
- `configured: true` but `slotActive: false` → if you previously skipped `--force-slot`, ask the user about replacing now and retry STEP 7 with `--force-slot`. Otherwise it's a gateway restart issue — back to STEP 9.
- `configured: true` but `health.ok: false` → server became unreachable. Tell the user; the config will start working as soon as the server is back.
- `configured: false` → setup didn't actually persist. Show full status JSON to the user and stop.

### Success message

> (CN) 🎉 全部搞定！OpenViking 长期记忆已经接好了。
>
> 工作方式：
> - **每一轮**：你的对话会被自动归档到 OpenViking session 里
> - **`/compact` 时**：归档消息会被抽取成长期记忆，写到 `~/.openviking/data/` 下
> - **后续会话**：每次回复前我都会自动搜一下相关记忆并带进上下文
>
> 想验证一下吗？跟我聊几句包含可记忆事实的话（例如"我的邮箱是 test@example.com"），然后调一下 `/compact`，再开新对话问"我的邮箱是？"，应该就能召回了。

> (EN) 🎉 All set! OpenViking long-term memory is connected.
>
> How it works:
> - **Every turn**: our messages are auto-archived into an OpenViking session
> - **On `/compact`**: archived messages are extracted into long-term memories under `~/.openviking/data/`
> - **Future sessions**: relevant memories are auto-retrieved and injected before my replies
>
> Want to verify? Tell me a few facts (e.g. "my email is test@example.com"), run `/compact`, then start a new chat and ask "what's my email?" — it should recall.

---

# Part 2 — Reference

## How It Works

The context-engine pipeline has three distinct stages — keep them apart, especially when telling users when memories actually appear in `~/.openviking/data/`:

- **Archive / capture (context-engine `afterTurn`)**: at the end of a user turn, the plugin commits user/assistant messages to the OpenViking session via `POST /api/v1/sessions/.../messages`. This is **archive only** — no memory extraction yet. You'll see session message counts grow on the server, but no new files under `viking://user/.../memories/`.
- **Memory extraction (on `/compact`)**: memory extraction runs when the user invokes OpenClaw's `/compact` command. The server-side extraction pipeline reads the archived session and writes new memories. The plugin's `after_compaction` hook is currently reserved; the extraction itself is driven by the server. **No `/compact`, no new memory files.**
  - `captureMode: "semantic"` (default): server extraction pipeline filters all qualifying text.
  - `captureMode: "keyword"`: only text matching trigger words (e.g. "remember", "preference") is considered.
- **Auto-Recall (context-engine `assemble()`)**: before prompt context is assembled, the plugin queries OpenViking for relevant memories and injects them into context. Recall works even when there are no extracted memories yet — you just won't see anything come back.

**Practical implication for testing**: if you write down a fact and immediately try to recall it without `/compact`, the plugin will only retrieve it as recent session context (archived messages), not as a long-term memory. To verify long-term memory cross-session, you must run `/compact` first.

## Available Tools

These are the plugin tools the agent can call once installed.

### `memory_recall` — Search Memories

| Parameter | Required | Description |
|---|---|---|
| `query` | Yes | Search query text |
| `limit` | No | Maximum number of results (defaults to plugin config) |
| `scoreThreshold` | No | Minimum relevance score 0–1 (defaults to plugin config) |
| `targetUri` | No | Search scope URI (defaults to plugin config) |

Example: user asks "What programming language did I say I like?"

### `memory_store` — Manual Store

| Parameter | Required | Description |
|---|---|---|
| `text` | Yes | Information text to store |
| `role` | No | Session role (default `user`) |
| `sessionId` | No | Existing OpenViking session ID |

Example: user says "Remember my email is xxx@example.com".

### `memory_forget` — Delete Memories

| Parameter | Required | Description |
|---|---|---|
| `uri` | No | Exact memory URI (direct delete) |
| `query` | No | Search query (find then delete) |
| `targetUri` | No | Search scope URI |
| `limit` | No | Search limit (default 5) |
| `scoreThreshold` | No | Minimum relevance score |

Example: user says "Forget my phone number".

## Configuration Schema

These are the keys under `plugins.entries.openviking.config` in `openclaw.json`. The setup wizard / `ov-install` sets the first few; the rest are tunables.

| Field | Default | Description |
|---|---|---|
| `mode` | `"remote"` (forced by plugin) | Always remote in this skill. Don't set manually. |
| `baseUrl` | `http://127.0.0.1:1933` | OpenViking server URL. |
| `apiKey` | — | API key. Optional if server has no auth. |
| `agent_prefix` | `""` | Prefix for routing memories per agent. Letters / digits / `_` / `-`. |
| `accountId` | — | Required when `apiKey` is a root key. |
| `userId` | — | Required when `apiKey` is a root key. |
| `targetUri` | `viking://user/memories` | Default search scope URI. |
| `timeoutMs` | (plugin default) | HTTP timeout for OpenViking calls. |
| `autoCapture` | `true` | Auto-archive turn messages to OpenViking session at `afterTurn` (extraction itself runs on `/compact`, not here). |
| `captureMode` | `"semantic"` | Filter mode used by the server-side extraction pipeline: `semantic` or `keyword`. |
| `captureMaxLength` | `24000` | Max text length per archived turn. |
| `autoRecall` | `true` | Auto-recall and inject memories before reply. |
| `recallLimit` | `6` | Max memories injected per recall. |
| `recallScoreThreshold` | `0.15` | Min relevance score to inject. |
| `recallMaxInjectedChars` | (plugin default) | Hard cap on injected character count. |
| `recallPreferAbstract` | (plugin default) | Prefer abstract memories over raw. |
| `recallTokenBudget` | (plugin default) | Token budget for injected memories. |
| `isolateUserScopeByAgent` | (plugin default) | Multi-tenant scoping toggle. |
| `isolateAgentScopeByUser` | (plugin default) | Multi-tenant scoping toggle. |
| `agentScopeMode` | (plugin default) | Agent scope strategy. |
| `bypassSessionPatterns` | — | Glob patterns for sessions skipped by capture. |
| `ingestReplyAssist` | (plugin default) | Reply-assist ingestion toggle. |
| `emitStandardDiagnostics` | (plugin default) | Verbose diagnostic logs. |
| `logFindRequests` | (plugin default) | Log retrieval requests. |

To change a value:

```bash
openclaw config set plugins.entries.openviking.config.<field> <value>
openclaw gateway restart
```

## Multi-Tenant (Root API Keys)

Some OpenViking deployments use a single **root** API key shared across tenants. In that case the plugin needs both `accountId` and `userId` so it can scope memories correctly. The setup wizard detects this automatically and returns:

```
Root API key detected. Missing: --account-id, --user-id
```

When you see this:

1. Ask the user for both values (they come from the OpenViking admin).
2. Retry STEP 7 with both flags:

```bash
openclaw openviking setup --base-url BASE_URL --api-key API_KEY --account-id ACCOUNT_ID --user-id USER_ID --json
```

A **user key** (issued per tenant) does not need these flags.

## Multi-Instance (`--workdir` / `OPENCLAW_STATE_DIR`)

If the user runs multiple OpenClaw instances (e.g. testing several agents in parallel), each has its own state dir.

To target a non-default instance:

```bash
npx -y openclaw-openviking-setup-helper@latest --workdir ~/.openclaw-second --base-url ... --api-key ...
```

`ov-install` writes a helper env file when the state dir is non-default:

- Unix: `~/.openclaw/openviking.env` containing `export OPENCLAW_STATE_DIR='...'`
- Windows: `~/.openclaw/openviking.env.bat` and `.ps1` setting the same variable

Source it before running `openclaw` commands so they hit the correct state:

**Unix:**
```bash
source ~/.openclaw/openviking.env
openclaw status
```

**Windows (PowerShell):**
```powershell
. "$HOME/.openclaw/openviking.env.ps1"
openclaw status
```

Or pass `--workdir` directly to each `openclaw` invocation (note: not all `openclaw` subcommands honor `--workdir` consistently — when in doubt, prefer the env var).

## Daily Operations

```bash
# Start or restart OpenClaw gateway after config changes
openclaw gateway restart

# Check overall status
openclaw status
openclaw openviking status --json

# Read current OpenViking slot
openclaw config get plugins.slots.contextEngine

# Disable OpenViking memory (keep config, deactivate slot)
openclaw config set plugins.slots.contextEngine legacy
openclaw gateway restart

# Re-enable
openclaw config set plugins.slots.contextEngine openviking
openclaw gateway restart
```

## Uninstall

### Preferred: via OpenClaw plugin manager

```bash
openclaw plugins uninstall openviking
openclaw config set plugins.slots.contextEngine legacy
openclaw gateway restart
```

### If installed through backup `ov-install`

```bash
npx -y openclaw-openviking-setup-helper@latest --uninstall
```

This will:

1. Stop the OpenClaw gateway.
2. Back up `openclaw.json`.
3. Remove all OpenViking plugin config from `openclaw.json` (`plugins.entries.openviking`, `plugins.allow`, `plugins.installs.openviking`, `plugins.slots.contextEngine`).
4. Move the plugin directory to `disabled-extensions/` as backup.
5. Remove the helper env files (`openviking.env`, `.bat`, `.ps1`).

Add `--base-url dummy` for fully non-interactive mode (skips the confirmation prompt).

### Manual

```bash
openclaw gateway stop
openclaw config set plugins.slots.contextEngine legacy
# Then edit ~/.openclaw/openclaw.json and remove openviking from
#   plugins.entries
#   plugins.allow
#   plugins.installs
openclaw gateway restart
```

## Error Recovery Reference

Match against actual stderr / JSON `error` strings.

| Symptom (exact string) | Likely cause | Fix |
|---|---|---|
| `command not found: openclaw` | OpenClaw not installed | Stop. Ask user to install OpenClaw >= 2026.4.8. |
| `command not found: node` / `'node' is not recognized` | Node missing | Stop. Ask user to install Node.js >= 22. |
| `429` / `rate limit` / `Too Many Requests` from `plugins install` | ClawHub throttle | Switch to backup Path B (`ov-install`). |
| `not logged in` / `please log in` from `plugins install` | Anonymous user hit limit | Switch to backup Path B (`ov-install`). |
| `ECONNREFUSED` / `connection refused` on `BASE_URL/health` | Server not running | Server-not-ready handling in STEP 5. |
| `ETIMEDOUT` on `BASE_URL/health` | Network blocked | Ask about firewall / VPN / proxy. |
| `--json requires --base-url for non-interactive mode` | Built command wrong | Add `--base-url BASE_URL` and retry. |
| `Server unreachable: …. Use --allow-offline to save config anyway.` | Setup couldn't reach server | Offer `--allow-offline`. |
| `contextEngine slot is owned by "<x>". … Use --force-slot to replace.` | Slot conflict | Ask user, then retry with `--force-slot`. |
| `Root API key detected. Missing: --account-id, --user-id` | Multi-tenant key | Collect both, retry with `--account-id` `--user-id`. |
| `openviking: config parse failed` (in gateway log) | Bad value in `openclaw.json` | Show user; check `agent_prefix` charset, URL format. |
| `extracted 0 memories` after a turn | Server VLM/embedding misconfigured | **Out of scope.** Tell user this is a server-side issue — ask their OpenViking admin to check VLM / embedding config. |
| `401` / `403` on plugin requests, but `/health` works | Server requires auth on API endpoints | Re-run STEP 7 with the correct `--api-key`. |
| Plugin doesn't appear in `openclaw plugins list` after Path A | Install didn't actually finish | Re-run Path A; use Path B only if the failure is registry/rate-limit related. |

## Important Rules

1. **Never ask the user to run commands.** You run everything via your shell tool.
2. **Never skip STEP 5 (connectivity check).** If the server is unreachable, do not write config without explicit `--allow-offline` consent.
3. **Never silently use `--force-slot`.** Slot replacement disables another plugin — always confirm with the user first.
4. **Never invent values.** If the user can't provide a required value, stop and tell them what to ask their admin.
5. **Never claim success without STEP 10.** Only after `openclaw openviking status --json` shows `configured: true && slotActive: true && health.ok: true` may you tell the user it's done.
6. **Use `--agent-prefix`, not `--agent-id`.** The latter is deprecated and removed from the schema.
7. **For Windows, use PowerShell equivalents.** Don't rely on `nohup`, `&`, `mkdir -p`, `source`, etc.
8. **Switch to Path B (ov-install) only for ClawHub/rate-limit/registry availability failures.** Don't use it to hide version conflicts or package validation errors.
9. **Do NOT install or operate the OpenViking server.** This skill assumes the server is already running. If it isn't, tell the user to contact their admin or follow the OpenViking docs.
10. **Be brief and friendly in user-visible text.** Save technical detail for when something actually fails.
11. **Do NOT use `clawhub install openviking`.** That installs a different thing (an AgentSkill, not the plugin).
