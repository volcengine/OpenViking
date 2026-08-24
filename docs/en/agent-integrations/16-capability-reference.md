# Integration Capability Reference

## Reading guide

| What you want to know | Where to look |
|---|---|
| Which tools an agent can call autonomously per harness | [§1.1](#_1-1-active-tool-surface-agentic-calls) Active tool surface + [§2.1](#_2-1-server-side-mcp-tool-surface) (MCP surface) + the profile cards |
| How memory archiving behaves under different shutdown methods | **[§3.3.3](#_3-3-3-shutdown-method-×-harness-outcome-matrix) Shutdown path × harness end-state matrix** |
| Whether auto-recall includes `session_id`, and its impact | [§3.2.2](#_3-2-2-decision-matrix) / [§3.2.3](#_3-2-3-profile-opening-injection) |
| How to enable recall digests, and the workload distribution between server and client | [§3.2.5](#_3-2-5-recall-digest) |
| Type boundaries for `forget` and delete operations | [§3.5](#_3-5-type-boundaries-for-writes-and-deletes) |
| Which environment variables apply to specific harnesses | [§3.1.4](#_3-1-4-configuration-layers) Configuration layering + "Config" on each profile card |
| Whether the server provides an automatic commit fallback | [§2.3](#_2-3-server-side-session-and-commit-semantics) |
| The complete `ov` CLI command set | [§5](#_5-ov-cli-command-reference) |
| How to integrate a custom agent with OpenViking | [§6](#_6-custom-agent-integration-guide) |
| Installation, configuration, and troubleshooting for specific integrations | The integration's own page (linked in the first line of each profile card in [§4](#_4-harness-profile-cards)) |

---

# 1. Capability overview

## 1.1 Active tool surface (agentic calls)

- **MCP-based harnesses (claude-code, codex/trae-cli, cursor, trae/trae-cn, zcode, opencode) share an identical active tool surface comprising 15 tools.** The server centrally defines these tools. The plugin reads `~/.openviking/ovcli.conf` via its proxy and establishes a connection to the server-defined MCP tools.

- `trae-cli` means TraeCode CLI 2.0 (2.0 only). It is installed via a `codex` plugin alias and maintains format compatibility with `codex`. Therefore, it is consolidated into the `codex` row in the matrices below.

| harness | tool surface | tools (enabled by default) | search memory | search resource | search skill | write memory | write resource | write skill | delete type boundary |
|---|---|---|---|---|---|---|---|---|---|
| claude-code | MCP passthrough | 15 | ✅ | ✅ | ✅ | ✅ | ✅ | ❌¹ | no type distinction² |
| codex / trae-cli | MCP passthrough | 15 | ✅ | ✅ | ✅ | ✅ | ✅ | ❌¹ | no type distinction² |
| cursor | MCP passthrough | 15 | ✅ | ✅ | ✅ | ✅ | ✅ | ❌¹ | no type distinction² |
| trae / trae-cn | MCP passthrough | 15 | ✅ | ✅ | ✅ | ✅ | ✅ | ❌¹ | no type distinction² |
| zcode | MCP passthrough | 15 | ✅ | ✅ | ✅ | ✅ | ✅ | ❌¹ | no type distinction² |
| opencode | MCP passthrough (host adds an `openviking_` prefix) | 15 | ✅ | ✅ | ✅ | ✅ | ✅ | ❌¹ | no type distinction² |
| dsh | MCP passthrough (`@deepseek-ai/dsh-mcp-client` → the shared stdio proxy; host adds an `mcp__openviking__` prefix) | 15 | ✅ | ✅ | ✅ | ✅ | ✅ | ❌¹ | no type distinction² |
| pi | native registration (7 × `viking_*`) | 7 (registration runs preflight checks⁴) | ✅ | ✅ | ✅ | ✅ `viking_remember` | ✅ `viking_add_resource` (URL only) | ❌ | no type distinction; delete by query needs score>0.8³ |
| openclaw | native registration (15 × `memory_*`/`ov_*` and friends) | 15 (14 on by default⁵) | ✅ `memory_recall` | ✅ `ov_search` (both scopes by default) | ✅ `ov_search` | ✅ `memory_store` | off by default⁵ | ✅ `add_skill` | memory-only allowlist + auto-delete only for a single candidate with score≥0.85 |
| hermes | native registration (6 × `viking_*`) | 6 (all on once the provider is active) | ✅ | ✅ | ✅ | ✅ `viking_remember` (writes the file directly, no extraction) | ✅ multi-protocol ingest (HTTP/Git/SSH/local file/directory zip) | ❌ | memory-only + `.md` leaf check |
| ov CLI | CLI commands | ~40 command groups | ✅ `ov find` | ✅ `ov find` | ✅ `ov find` | ✅ `ov add-memory` | ✅ `ov add-resource` | ✅ `ov add-skill` | `ov rm` executes directly (TUI deletion asks for confirmation and blocks root/scope deletes) |

¹ MCP `write` can target `viking://resources`, `viking://user`, or `viking://agent`. Adding skills via MCP is not yet supported; use the openclaw `add_skill` tool, `ov add-skill` CLI, or the REST API instead.
² MCP `forget` does not differentiate between memory, resource, and skill types. However, the storage layer protects namespace roots: deletion requests for bare `viking://`, `viking://user`, and `viking://agent` are rejected. See [§3.5](#_3-5-type-boundaries-for-writes-and-deletes).
³ For `viking_forget` on pi: the `recursive` flag is strictly set to false (directories are never deleted), and deleting by query requires a match score > 0.8.
⁴ The `pi` harness registers its tools only if the session bypasses `bypassPatterns`, `client.health()` passes, and `ensureSession` succeeds (`index.ts:66-113`). If the health check fails, no tools are registered for that session.
⁵ The `add_resource` tool in openclaw requires a double opt-in before activation.

**Skill addition/deletion boundaries**: Skills can be added via the openclaw `add_skill` tool (enabled by default), the `ov add-skill` CLI command, or the REST API. Deletion operates across four tiers, detailed in [§3.5](#_3-5-type-boundaries-for-writes-and-deletes).

## 1.2 Automatic hook surface (driven by the harness)

| harness | how it plugs in | auto-recall | recall carries session_id | digest (client)* | profile injection | takes over host compaction | offline compensation (pending queue) | statusline |
|---|---|---|---|---|---|---|---|---|
| claude-code | 9 hooks + MCP proxy + slash + statusline + skill | ✅ | ✅ | ✅ local `claude -p` / server-side rewrite (auto by default) | ✅ (10000) | ❌ (PreCompact only commits) | ✅ | ✅ |
| codex / trae-cli | 4 hooks + MCP proxy + skill | ✅ | ✅ | ✅ local `codex exec` (on by default) | ✅ (10000) | ❌ | ❌ no on-disk queue (the cursor stays put and the next turn resends) | ❌ |
| cursor | 7 hooks + MCP proxy + rule + skill | ✅ | ✅ | ❌ | ✅ (6000) | ❌ | ✅ | ❌ |
| trae / trae-cn | 4 hooks + MCP proxy | ✅ | ✅ | ❌ | ✅ (6000) | ❌ | ✅ | ❌ |
| zcode | 4 hooks + MCP proxy | ✅ | ✅ | ❌ | ✅ (6000) | ❌ | ✅ | ❌ |
| opencode | 7 plugin hooks + MCP proxy | ✅ | ✅ | ❌ | ✅ (10000) + repo list into the system prompt | ❌ (commits once before and once after compacting) | ✅ | ❌ (toast instead) |
| dsh | native Cordis plugin (same process) + MCP proxy + skill | ✅ | ✅ | ❌ | ✅ (10000, once per session) | ❌ | ✅ | ❌ |
| pi | native extension (8 events) | ✅ | ✅ | ❌ | ✅ (10000, rebuilt into systemPrompt every turn) | ✅ **takeover** (on by default) | ✅ | ✅ |
| openclaw | context-engine plugin (`ownsCompaction:true`) | ✅ | ❌ (goes through `/find`, which has no session_id field) | ❌ | ❌ | ✅ **full ContextEngine takeover** | ❌ failed turns are not replayed | ❌ |
| hermes | native MemoryProvider plugin | ✅ | partial (preferred `search/search` path only; the degraded `/find` path drops it) | ❌ | ❌ (static tool-guidance block) | ❌ | ✅ in-process queue (never written to disk) | ❌ |
| ov CLI | one-shot commands | ❌ (`ov find/search` are explicit commands) | — (`ov search --session-id` is an explicit argument) | ❌ | ❌ | ❌ | ❌ | ❌ |

\* This column indicates whether the client performs its own local compression of recall results. On the server side, the context retrieval API provides digest capabilities equally to all callers via the `rewrite` parameter (see [§3.2.5](#_3-2-5-recall-digest)).

**Current status of `session_id`**: With the exception of openclaw (whose `/find` endpoint lacks this field) and the degraded path in hermes, auto-recall on all harnesses explicitly carries a `session_id`. This behavior is enforced by a cross-plugin regression test (`examples/memory-plugin-shared/recall-session-wiring.test.mjs:16-39`).

## 1.3 Grouping by form

- **Full suite** (hook automation + MCP tool surface + surrounding UX): claude-code and codex (`trae-cli` is installed via an alias and is included here).
- **Thin hook** (sharing `agent-hook-runtime`, meaning core behaviors are essentially identical, with differences limited to host events and thresholds): cursor, trae/trae-cn, and zcode.
- **Plugin event**: opencode (offers the richest host event surface; `dispose` handles shutdown).
- **Native in-process**: dsh (Cordis), pi (extension + compaction takeover), openclaw (full ContextEngine takeover), and hermes (MemoryProvider).
- **Tool**: ov CLI (all operations are explicit calls; no automatic background actions).
- **Non-coding**: Open WebUI (tool server), LangChain (SDK library), the Agent Plugins portable package (an MCP + skill spec bundle), generic direct MCP, log ingestion, and Helper (desktop).

---

# 2. Shared capability core

The individual harness sections (profile cards) focus exclusively on differences and specific implementations. All shared capabilities and universal behaviors are documented once in this section.
## 2.1 Server-side MCP tool surface

These tools are defined on the server side, and future updates will be centrally published there. Harnesses only need to proxy the MCP to obtain the latest `~/.openviking/ovcli.conf`.

| # | Tool | What it does | Key parameters (definition line) |
|---|---|---|---|
| 1 | `find` | Fast semantic search requiring no session context | `query, target_uri="", limit=10, min_score=0.35, level, context_type` (`:259`) |
| 2 | `search` | Deep search, featuring optional `session_id` integration and intent analysis | The session is only loaded if the server has `retrieval.enable_intent` enabled (defaults to true) (`:285`, `:302-304`) |
| 3 | `read` | Read the full text of one or more `viking://` files | Uses a concurrency semaphore of 10; a single failure yields `(nothing found at <uri>)` rather than raising an exception (`:389`) |
| 4 | `list` | List a directory (function name `ls`, explicitly registered as `list`) | `recursive=False` (`:423`) |
| 5 | `tree` | Recursive directory tree | `level_limit=3, node_limit=1000, include_abstract=False` (`:449`) |
| 6 | `remember` | Write long-term memory | Internally creates a one-shot session (`mcp-store-<uuid12>`) and immediately calls `commit_async` (`:504-523`). This is the only commit entry point on the MCP surface, as there is no explicit commit tool. |
| 7 | `write` | Write a `viking://` file | `mode=replace\|append\|create`; `replace` falls back to `create` if not found. New files must use an extension from the allowlist: `.md .txt .json .yaml .yml .toml .py .js .ts`. Writable domains are limited to `resources/user/agent`; directories like `skills/`, `peers/`, `privacy/`, and `sessions/` under the user root are read-only. Existing `.abstract.md` / `.overview.md` sidecars may be body-updated, but public APIs cannot create them (`:529`; `content_write.py:60-81`) |
| 8 | `edit` | Exact string replacement | Supplying an empty `old_string`, finding zero matches, or finding multiple matches without `replace_all` will raise an error and leave the file content unchanged (`:569`) |
| 9 | `add_resource` | Resource ingestion (remote URL / signed upload of a local file / Connector) | `watch_interval` is defined in minutes (0 disables watching). The local-path branch generates a signed upload URL (default TTL of 600s), and ingestion triggers automatically post-upload without requiring a subsequent API call (`:723-947`) |
| 10 | `list_watches` | List watch subscriptions; not yet supported on the commercial edition | Returns an error string if the scheduler is not running (`:958`) |
| 11 | `cancel_watch` | Cancel by `to_uri`; not yet supported on the commercial edition | Deliberately does not expose pause/resume/trigger/update (`:990`) |
| 12 | `grep` | Regex content search | Multiple patterns run concurrently (semaphore of 10), `node_limit=10` (`:1032`) |
| 13 | `glob` | Filename glob | `node_limit=100` (`:1084`) |
| 14 | `forget` | Permanently deletes a URI (unrecoverable) | `recursive=False` by default; type boundaries in [§3.5](#_3-5-type-boundaries-for-writes-and-deletes) (`:1110-1117`) |
| 15 | `health` | Health check | No parameters (`:1123`) |

Supporting mechanisms:

- **Portable schema rewriting** (`:1149-1218`): At module import time, every tool's `anyOf` / `$ref` is flattened into a plain type to ensure compatibility with clients that only support the OpenAPI 3.0 subset (such as Gemini). Runtime validation still relies on the original Python signature (for instance, `read` advertises an array schema but continues to accept a bare string). All MCP clients receive the exact same server-produced schema; there are no client-specific variants.
- **Identity middleware** (`:149-233`): Shares `resolve_identity` with REST endpoints. It reads headers in the following order: `x-api-key`, `authorization`, `x-openviking-account`, `x-openviking-user`, and `x-openviking-actor-peer`. If absent, the account and user fields fall back to `"default"`.

## 2.2 The memory-plugin-shared layer

The `examples/memory-plugin-shared/lib/` directory contains 18 `.mjs` modules and serves as the single source of truth for all JS-based harnesses. These are consumed in two ways:

1. **Vendoring (copying)**: The `sync.mjs` script distributes these modules to 7 targets, prefixing every file with `// GENERATED FROM ... DO NOT EDIT.`. Because of this added line, a vendored copy's line number will be exactly one line greater than the library source (keep this in mind when cross-referencing line numbers). The distribution breakdown is as follows: 17 modules each for claude-code, codex, and opencode (the "HARNESS 13" + `mcp-proxy-core` + `mcp-proxy-config` + `async-writer` + `batch-send`); 15 for dsh (the "HARNESS 13" + the two `mcp-proxy-*` modules it needs for the stdio proxy); 13 for pi; all 19 for zcode; and 5 for agent-plugins. At the current HEAD, every target has zero drift from the original library source.
2. **Direct import via relative path (no copying)**: Integrations like cursor, trae, and trae-cn directly `import "../../memory-plugin-shared/lib/..."`. To ensure this works, the installer copies the package alongside the shared library into `~/.openviking/agent-integrations/{<client>,memory-plugin-shared}/`, preserving the relative folder layout. At runtime, these harnesses share this directory, meaning reinstalling any one of them will overwrite the shared directory wholesale.

Core modules at a glance (detailed further in the per-dimension sections):

| Module | Responsibility | Consumers |
|---|---|---|
| `recall-core.mjs` | Handles recall request construction, three-tier degradation, and local fallback ranking/injection | All JS-based harnesses |
| `agent-hook-runtime.mjs` | All-in-one "thin hook" runtime handling 19 configuration environment variables, session ID derivation, cross-process locking, fetching, and commits | cursor / trae / trae-cn / zcode |
| `mcp-proxy-core.mjs` | stdio ↔ streamable-HTTP MCP proxy core | All MCP-based integrations + agent-plugins |
| `pending-queue.mjs` | On-disk offline queueing and replay at session start | cc / cursor / trae×2 / zcode / opencode / dsh / pi |
| `batch-send.mjs` | Executes writes in batches of 100, handles per-message degradation on 404/405 errors, and queues the leading contiguous prefix | cc / codex / opencode + the agent-hook family |
| `profile-inject.mjs` | Injects the profile and available-memory index at session start | 9 harnesses (all but openclaw / hermes) |
| `recall-compress-core.mjs` | Manages the recall compression prompt, URI edit-distance repair, and caching | claude-code |
| `capture-utils.mjs` | Handles message normalization, injection back-flow guarding, and capture filtering | codex / opencode / dsh / pi |
| `credentials.mjs` | Credential resolution chain (see [§3.1.3](#_3-1-3-credential-systems)) | All JS-based |
| `session-model.mjs` | Session ID prefix derivation and bypass globbing | All JS-based |
| `async-writer.mjs` | Detaches the write path (drains stdin → spawn → approve → write → unref). Falls back to synchronous writing if spawn fails | cc / codex / zcode |
| `workspace-peer.mjs` | Converts the `cwd` into an actor peer (replacing every non-alphanumeric character with `-`) | All JS-based |
| `uri-guard.mjs` / `agent-uri-guard.mjs` | Intercepts cases where `viking://` is incorrectly treated as a local path | Used in each harness's `PreToolUse` or `tool.execute.before`-style hooks |
| `plugin-config.mjs` | Reads the `plugin` section of `ovcli.conf` | claude-code / codex |
| `setup-wizard.mjs` | Interactively writes to `ovcli.conf` | cc, codex, opencode, and pi expose an entry point for this |
| `retryable.mjs` | Handles retryability checks: allows status codes 0, 408, 429, and ≥500, as well as 409 if `error.details.retryable===true`. Standard 4xx errors (including 401/403) are not retried | All JS-based |

## 2.3 Server-side session and commit semantics

- **Implicit session creation**: Plugins generally do not call `POST /sessions` explicitly (with `dsh` being the only exception, as it sends a creation request carrying only `session_id`). Instead, the server creates the session using `auto_create=True` upon receiving the first `POST /sessions/{id}/messages(/batch)` request. On the recall side, calling `_load_session(auto_create=True)` under `mode="context"` also creates a session, meaning the very first recall action will initialize the session on the server.
- **Two-phase commit**: `POST /sessions/{id}/commit` returns only after Phase 1 (archiving) has completed synchronously. Phase 2 (memory extraction) runs as a background task, returning a `task_id`. The server default for `keep_recent_count` is **0** (meaning it archives everything, leaving no live tail).
- **Server-side auto-commit is disabled by default, and the session creation paths used by plugins never enable it**:
  - `memory.session_auto_commit.default_enabled = false` and `idle_enabled = false` (`memory_config.py:15-16`). Auto-commit is disabled for sessions lacking a storage policy (`session_service.py:637-638`). Furthermore, the idle scanner isn't even instantiated when `idle_enabled=false` (`core.py:440-448`), effectively acting as a double gate.
  - The `auto_create` path triggered by `POST /messages` accepts no policy arguments; only `POST /sessions` (creation) and `PATCH /sessions/{id}/config` can set the `auto_commit_policy`.
  - Currently, no plugin sends an `auto_commit_policy`. Among first-party clients, only the **ov CLI** does this (`ov session new --auto-commit-policy-json` / `--no-auto-commit`, `ov session config set`).
  - When a policy is explicitly enabled, the server defaults are: `pending_token_threshold=150000` (strictly greater than), `message_count_threshold=100`, `idle_timeout_seconds=86400`, `keep_recent_count=0`, and `min_commit_interval_seconds=0`. Note that this set of server defaults operates independently of the plugin clients' configuration (which is typically 20000/10).
- **Consequence**: As it stands, every auto-commit relies on threshold logic implemented individually by each client ([§3.3](#_3-3-session-and-commit-lifecycle)), with no server-side fallback. If a process dies abnormally, any lingering pending messages are only archived and extracted when a subsequent commit is manually triggered in that same session.
- **Externalized tool output**: The server sets `tool_output_externalization.enabled=True` with `threshold_chars=20000` (`server/config.py:257-258`). Clients commonly raise `captureToolMaxChars` to 1000000 purely as a fallback mechanism, since the actual truncation and externalization occur on the server. Externalized results are then referenced via `tool_output_ref` (for context, openclaw provides three dedicated tools specifically for reading these references).
- **Server-side recall timeout fuses**: Configured via `retrieval.recall_intent_timeout_s=5.0` (for query expansion), `recall_rewrite_timeout_s=30.0` (for digests, [§3.2.5](#_3-2-5-recall-digest)), and `enable_intent=true`. Client timeout budgets are derived directly from these two timeout values ([§3.2.4](#_3-2-4-timeout-and-budget-chain)).

---

# 3. Dimensions in detail
## 3.1 Integration forms, installation, and configuration

### 3.1.1 Decision matrix

| Harness | Integration Form | Install Channel | Session ID Prefix/Format | Config Source | Standalone Setup Wizard |
|---|---|---|---|---|---|
| claude-code | CC plugin (marketplace): 9 hooks + MCP proxy + slash + statusline + skill | One-line `install.sh --harness claude` (supports both the modern plugin path and the legacy `claude mcp add` compatibility path) / manual marketplace / TOS mirror | `cc-<CC session_id verbatim>`; subagents use `…__subagent-<agent_id>` | env + ovcli.conf `plugin.claude_code` + ov.conf `claude_code` | ✅ `scripts/setup.mjs` |
| codex | Codex plugin (marketplace): 4 hooks + MCP proxy + skill | One-line `--harness codex` / `codex plugin marketplace add` (the TOS channel uses dumb-HTTP git to ensure remote updates continue working) | `cx-<safeId>` (deterministically derived, without reading state) | env + ovcli.conf `plugin.codex` + ov.conf `codex` | ✅ |
| trae-cli | **Installed as a codex plugin alias** (TraeCode CLI 2.0, 2.0 only; a Codex-family CLI: uses the `traecli` binary and `~/.trae/traecli.toml` config; its capability surface is identical to codex) | One-line `--harness trae-cli` (reuses the codex install flow; marketplace commands run against the targeted binary, e.g., `traecli plugin marketplace add`) | Same derivation rule as codex | Same as codex (env + ovcli.conf `plugin.codex` + ov.conf) | ✅ (same as codex) |
| cursor | Config-driven (writes `~/.cursor/hooks.json`+`mcp.json`) + rule + skill | One-line `--harness cursor` | `cu-<conversation_id>` | env only | ❌ (shares the installer TUI) |
| trae / trae-cn | Config-driven (`~/.trae{,-cn}/hooks.json` + platform-specific mcp.json) | One-line `--harness trae,trae-cn` | `tr-` / `trcn-` | env only | ❌ |
| zcode | Config-driven (merged into `~/.zcode/cli/config.json`, forcing `hooks.enabled=true`) | One-line `--harness zcode` | `zc-<sess_…>` | env only | ❌ |
| opencode | npm plugin `@openviking/opencode-plugin` (its config hook dynamically injects the MCP entry) | One-line `--harness opencode` (uses npm registration with a proxy snapshot fallback) / manual npm / from source | `oc-<id>`; subagents use `oc-<parent>__subagent-<child>` | `openviking-config.json` (searched at 4 levels) + env | ✅ |
| dsh | In-process Cordis plugin (`cordis.patch.yml` plugin group) | Unified installer (asks for the profile, default `web`), or `dsh plugin --profile web add @openviking/dsh-memory-plugin` | `dsh-<session.id as-is>`; each subagent is assigned its own session | cordis patch config + 4 env vars (credentials: patch > env; behavior toggles: env > patch) | ❌ |
| pi | Native pi extension (loaded from a directory, with TypeScript transpiled on the fly via jiti) | One-line `--harness pi` (including `pi install` registration) | `pi-<piSessionId>` | `config.json` (credential fields are resolved via the shared credential chain) + env | ✅ |
| openclaw | context-engine plugin (`ownsCompaction:true`) + 15 tools + 5 slash + 4 hooks + HTTP routes | ClawHub: run `openclaw plugins install clawhub:@openviking/openclaw-plugin` alongside `openclaw openviking setup` / npm installer / TOS offline bundle | A UUID is lowercased as-is, otherwise `sha256(sessionKey)`; `memory_store` temporary sessions use `memory-store-<ts>-<rand>` | `plugins.entries.openviking.config` in `openclaw.json` (strictly validated: unknown keys or invalid values force the plugin into setup-only mode) + a few env vars | ✅ `openclaw openviking setup` (interactive/non-interactive + key role probing + version compatibility check) |
| hermes | Hermes bundled MemoryProvider (ships with Hermes; no plugin installation required) | Run `hermes memory setup openviking` (interactive curses wizard), or manually run `config set memory.provider openviking` + `.env` | Hermes generates `%Y%m%d_%H%M%S_<hex6>`; the plugin uses it verbatim | `.env` (`OPENVIKING_*`) or linked ovcli.conf (`use_ovcli_config` mode clears the 5 corresponding variables from .env) + config.yaml | ✅ (multi-level menu) |
| ov CLI | Native Rust binary | npm `@openviking/cli` / `uv tool install openviking` / cargo / GitHub Releases | Does not manage its own session (`ov chat` defaults to the machine-uid) | `ovcli.conf` (multiple profiles) + a few env vars | ✅ `ov config` (TUI wizard) |

### 3.1.2 Unified installer

The unified install script (`examples/memory-plugin-shared/install.sh`, 3424 lines) supports ten harness IDs: `claude, codex, cursor, trae, trae-cn, trae-cli, zcode, opencode, pi, dsh`. (Note that `openclaw` uses its own distribution channel, while `trae-cli` reuses the `codex` install flow, as detailed in [§3.1.1](#_3-1-1-decision-matrix)). Key highlights:

- **Interactive prompts:** Two distributions (`--dist github|tos`) and three sources (`--source remote|archive|dev`) are available. When executed via `bash <(curl …)`, it reads input directly from `/dev/tty` to ensure prompts remain interactive.
- **Usage:** In the official documentation, the canonical one-line command omits the `--harness` flag, which launches a TUI multi-select menu. However, the setup-helper forwarding scripts bundled with each plugin append the `--harness` flag automatically.
- **Idempotent merging:** Hooks and MCP entries are identified by the `OPENVIKING_INTEGRATION_ID` marker. This ensures stale entries are pruned and new ones are appended without affecting third-party configurations. Writes are atomic: the script creates a `.bak` backup, writes to a temporary file, and then renames it over the target with `0600` permissions.
- **Credential wizard:** This writes to `~/.openviking/ovcli.conf`. Users can select from three targets (local `http://127.0.0.1:1933` / Volcengine Cloud `https://api.vikingdb.cn-beijing.volces.com/openviking` / custom). If a configuration already exists, the script displays the current values first and asks whether to keep or reconfigure them, masking the API key for security.
- **Uninstallation:** The `--uninstall` flag covers `cursor`, `trae`, `trae-cn`, and `zcode`, and also cleans up any legacy trae-cli hook config. The Codex-format and host-managed plugins (`claude`, `codex`, `trae-cli`, `opencode`, and `pi`) are removed via their respective host's plugin management systems.
- **Post-installation self-check:** This includes grepping the configuration, running `node --check`, and executing a smoke test with `OPENVIKING_MEMORY_ENABLED=0`.
- **Node.js requirement:** The installer enforces a minimum version of Node 18+.

### 3.1.3 Credential systems

Four parallel credential-resolution systems coexist within the codebase, each utilizing its own environment variable names and authentication headers. When troubleshooting, your first step should be identifying which system is currently in use:

| Family | Consumers | URL Env | Key Env | Identity Env | Auth Header |
|---|---|---|---|---|---|
| **A. Shared JS core** (`credentials.mjs`) | claude-code / codex (including trae-cli) / cursor / trae×2 / zcode / opencode / pi / dsh / agent-plugins | `OPENVIKING_URL` → `OPENVIKING_BASE_URL` | `OPENVIKING_BEARER_TOKEN` → `OPENVIKING_API_KEY` | `OPENVIKING_ACCOUNT` / `OPENVIKING_USER` / `OPENVIKING_PEER_ID` | `Authorization: Bearer`. Note: codex's four hook scripts also send an `X-API-Key` compatibility header. |
| **B. openclaw** (its own `config.ts`) | openclaw | `OPENVIKING_BASE_URL` → `OPENVIKING_URL` | `OPENVIKING_API_KEY` (supports SecretRef env/file) | `OPENVIKING_ACCOUNT_ID` / `OPENVIKING_USER_ID` (note the `_ID` suffix here) | `X-API-Key`. (When pointing to OV Cloud, note that it actually authenticates using Bearer). |
| **C. hermes** (Python) | hermes | `OPENVIKING_ENDPOINT` | `OPENVIKING_API_KEY` | `OPENVIKING_ACCOUNT` / `OPENVIKING_USER` / `OPENVIKING_AGENT` (= actor peer) | Sends both `X-API-Key` and `Bearer`. When a key is present, it omits tenant headers by default (if the server rejects the call with a trusted error, it appends them and retries once). |
| **D. ov CLI** (Rust) | ov | Primarily the conf file | conf | `--account/--user/--actor-peer-id` | `X-API-Key`. Toggles between LDAP Basic and OIDC Bearer based on `auth_mode`; an `api_key` containing two or more `.` characters automatically receives a Bearer header as well (JWT fallback). |

Family A resolves credentials in the following order (refer to individual profile cards for other families):

1. The resolution mode is controlled by `OPENVIKING_CREDENTIAL_SOURCE` (aliased as `_CREDENTIALS_SOURCE`), accepting values of `env|cli|auto` (defaults to `auto`).
2. **`auto` prioritizes environment variables**: If any environment credential field is present, the entire resolution process relies on the environment. Only when all environment fields are empty, and an `ovcli.conf` file exists with credential fields, does the system fall back to the configuration file (meaning the key, account, user, and peer are then exclusively sourced from the file).
3. **baseUrl**: Resolves via environment variables → `ovcli` `url` → `ov.conf` `server.url` → `http://{server.host|127.0.0.1}:{server.port|1933}` (where `0.0.0.0` normalizes to `127.0.0.1`), with a final fallback to `http://127.0.0.1:1933`.
4. **apiKey**: Resolves via `BEARER_TOKEN` → `API_KEY` → `ovcli` `api_key` → `ov.conf` `codex.apiKey` → `server.root_api_key`.
5. **mcpUrl**: Resolves via `OPENVIKING_MCP_URL` (when outside CLI mode) → `${baseUrl}/mcp`.
6. **Common request headers**: `Authorization: Bearer` + `X-OpenViking-Account/User/Actor-Peer` + `User-Agent: openviking-memory-<harness>/<version>`.

**Workspace peer** (applies to all of Family A + agent-plugins): If no explicit `peerId` is provided and `OPENVIKING_WORKSPACE_PEER≠0`, the peer is derived from the current working directory (cwd). Every non-alphanumeric character in the path is replaced with a hyphen (`-`) (e.g., `/Users/x/Dev/OpenViking` becomes `-Users-x-Dev-OpenViking`), and this value is sent as the `X-OpenViking-Actor-Peer`. The server validates this header and returns a `400` error if it contains `/` or `\`. For `openclaw`, the peer is derived from `peer_role`/`peer_prefix` (note that if `peer_role=person`, sender information must be available, otherwise tool calls will fail). The `hermes` peer defaults to `OPENVIKING_AGENT` (defaulting to `hermes`).

### 3.1.4 Configuration layers

| Config Layer | Applies To | Notes |
|---|---|---|
| env `OPENVIKING_*` | Per family, see above; behavior knobs are listed on each profile card | The only layer that spans every JS-based integration. |
| ovcli.conf `plugin` section (`plugin.claude_code` / `plugin.codex` / shared scalars) | claude-code / codex | `plugin.<x>` entries named after any other harness are ignored. Note: `ov config add/edit` rewrites the entire file from the Rust Config struct, thereby dropping any `plugin` sections it does not recognize; however, `ov config switch` simply copies bytes and remains unaffected. |
| ov.conf harness sections (`claude_code.*` / `codex.*`) | claude-code / codex (legacy fallback) | |
| The harness's own config file | opencode `openviking-config.json`, pi `config.json`, dsh cordis patch, openclaw `openclaw.json`, hermes `config.yaml`+`.env` | |

**Quick scope reference** (these settings only take effect on the specified harnesses):

- `OPENVIKING_COMMIT_TURN_THRESHOLD`: cursor only (`trae`, `trae-cn`, and `zcode` commit on every Stop and ignore this threshold).
- `OPENVIKING_WRITE_PATH_ASYNC`: claude-code / codex / zcode.
- Recall digest settings (`OPENVIKING_RECALL_COMPRESS`, `OPENVIKING_RECALL_REWRITE`, and their companions): claude-code / codex (note that the server-side `rewrite` parameter is available to all callers, see [§3.2.5](#_3-2-5-recall-digest)).
- `OPENVIKING_RECALL_DEDUP_TURNS`, `OPENVIKING_RECALL_QUERY_EXPANSION`: claude-code / codex.
- ovcli.conf `plugin` section: claude-code / codex.
## 3.2 Automatic recall and injection

### 3.2.1 Mechanism foundation: one shared pipeline, two server-side paths

Recall for the JS-family harnesses is managed through a three-level degradation chain within `recall-core.mjs`:

1. **Context face**: Calls `POST /api/v1/search/search` with `mode:"context"` and `purpose:"coding"`. Its core design principle is "declare intent only, leave the mechanism to the server." Parameters like `quotas`, `max_tokens`, `query_expansion`, and `rewrite_max_bullets` are transmitted only if explicitly configured by the user (indicated by a sentinel field); otherwise, server defaults are applied.
2. **Legacy `/recall`**: If the context face request returns a 400 or 422 error and the response body contains marker fields like `extra`, `mode`, or `unexpected`, the server is identified as an older version. A 6-hour negative cache is then written locally to `~/.openviking/state/context-face.json`. Because this is a machine-wide shared file, once one harness flags it, every JS-family harness on that machine will bypass the context face stage. The call then degrades to the deprecated `/api/v1/search/recall` endpoint. If `peer_scope` is rejected, it retries once without that parameter.
3. **Raw find fallback**: Concurrently requests `viking://~/memories` and `viking://~/skills` by calling `POST /search/find` twice. (Note: resources are deliberately excluded from automatic recall; resource documents are fetched by the model invoking `search` itself). The client then re-ranks the results locally (using weight rules: leaf +0.12, time intent +0.10, preference intent +0.08, and lexical overlap ≤0.2), deduplicates them, and fills up to the client token budget. The `recallTokenBudget`, `recallMaxContentChars`, and `recallPreferAbstract` configurations take effect *only* at this level. Under the context face, the injection budget is dictated by the server's `max_tokens` (which defaults to 1600).

On the server side, `session_id` handling diverges into two distinct execution paths:

- **Path A: `mode="context"`** (utilized by the context face and the `/recall` preset). This path manages query expansion and the cross-turn deduplication ledger. Query expansion requires passing three gates: `retrieval.enable_intent` must be enabled (default is true) → the session must be materialized (meaning the `messages.jsonl` file exists) → and either `latest_archive_overview` or `current_messages` must be non-empty. Following expansion, the original query always ranks first, followed by a maximum of 3 appended planned queries. The ledger (`.recall_log.json`) applies a cooldown to URIs whose bodies have already been sent, lasting for `dedup_turns`. If a turn "sent only the URI and not the body," that record bypasses the cooldown. Similarly, nothing is recorded if the digest determines the memory is `no_relevant`.
- **Path B: `mode="list"`** (the default behavior when the mode is omitted). In this path, `IntentAnalyzer` completely replaces `typed_queries` (meaning the original query is not guaranteed to survive), bypassing both the ledger and the original-query baseline. Callers that land in this path include codex's second-level degradation `searchScope`, hermes's `viking_search(mode="deep")`, and the preferred prefetch path. Although they carry a `session_id`, they do not benefit from the context face's query expansion or deduplication features.

**Three key notes on `dedup_turns`:**
① **Server default:** The server default for the context face is **0**. The familiar default of "5" actually originates from the `recall-core.mjs` client fallback and the `/recall` preset (the latter applying only when a `session_id` is present). Therefore, a third-party application hitting the API directly without the shared library must explicitly send `dedup_turns` to enable cross-turn deduplication, even if a `session_id` is provided.
② **Turn counting:** A "turn" counts individual messages, not full conversation rounds (since `_resolve_turn` relies on `total_message_count`). For a harness that pushes user and assistant messages simultaneously, the default of 5 roughly equals 1-2 actual conversation rounds.
③ **Edge cases with auto-settings:** If `autoCapture=0` and `autoRecall=1`, the message count remains at 0, meaning the ledger clock never advances. As a result, URIs whose bodies were previously sent remain cooled down for the entire session. To disable deduplication entirely (e.g., for claude-code or codex), use `OPENVIKING_RECALL_DEDUP_TURNS=0`.

### 3.2.2 Decision matrix

| harness | trigger | query construction | session_id | server path | injection format / location | digest (client)* |
|---|---|---|---|---|---|---|
| claude-code | every `UserPromptSubmit` | prompt verbatim, trimmed | ✅ `cc-` | A (context face) | `<openviking-context>` → `hookSpecificOutput.additionalContext` | ✅ local/server (default auto, [§3.2.5](#_3-2-5-recall-digest)) |
| codex / trae-cli | every `UserPromptSubmit` (hard 120s deadline for the whole hook) | prompt verbatim | ✅ `cx-` (derived deterministically, no state read) | A; second-level degradation searchScope lands in B | `<openviking-context source="auto-recall" format="digest">` | ✅ local `codex exec` ([§3.2.5](#_3-2-5-recall-digest)) |
| cursor | `beforeSubmitPrompt` | prompt verbatim; deduped by event id and a 500ms window, reusing the cached block for the same promptHash | ✅ `cu-` | A | `additional_context` | ❌ |
| trae / trae-cn | `UserPromptSubmit` | prompt with prior injection blocks stripped (reads `input.prompt` only) | ✅ `tr-`/`trcn-` | A | `additionalContext` | ❌ |
| zcode | `UserPromptSubmit` | three kinds of injection block stripped (including `<system-reminder>`) | ✅ `zc-` | A | `additionalContext` (strict JSON) | ❌ |
| opencode | the user message in every `chat.message` | concatenates non-synthetic text parts; skips recall for the turn if the body already contains `<openviking-context` | ✅ `oc-` | A (timeoutMs=30000) | builds a synthetic part and `unshift`s it to the front of parts | ❌ |
| dsh | `agent/pre-step` waterfall (await next first, then append) | every message in the claimed batch (filtering out its own injected content) | ✅ `dsh-` | A | appended to the end of `decision.messages` via `createUserMessage` (source: plugin/openviking-memory) | ❌ |
| pi | queued during `before_agent_start`; retrieval runs inside the `context` event (this turn's prompt gets this turn's memories) | prompt verbatim | ✅ `pi-` (omitted before the session exists) | A | prepended to the last real user message (idempotency checked via `<openviking-context`) | ❌ |
| openclaw | context-engine transformContext assemble (7 passthrough gates) | plain text of the last user message, cleaned and cut to 4000 characters | ❌ (`/find` has no such field) | `/find` | prepended into the last user message as `<relevant-memories>` + `Source: openviking-auto-recall` | ❌ |
| hermes | `prefetch` runs synchronously before every API call | raw user input, with two layers of skill scaffolding stripped; skipped under 5 characters | partial (only on the preferred `search/search` path, which lands in B; omitted when degrading to `/find`) | B / find | `<memory-context>` fenced block appended to the current user message (request body only, never written back to storage) | ❌ |

\* As in [§1.2](#_1-2-automatic-hook-surface-driven-by-the-harness), "digest" in this column refers to client-side local compression, while the server digest is available to every caller ([§3.2.5](#_3-2-5-recall-digest)). The `ov` CLI has no automatic recall and is not included in this table.

### 3.2.3 Profile / opening injection

- **Implementation**: `profile-inject.mjs` reads the full `viking://user/<space>/memories/profile.md`, alongside a recursive listing of the `preferences/` and `entities/` directories (`abs_limit=512`). The budget estimation logic is CJK-aware: characters ≥U+3000 count as 1.5 tokens per character, while all others are calculated as characters divided by 4. The profile consumes half of the available budget. If the limit is exceeded, the middle section is elided, preserving "the first 8 lines + the tail." If a directory listing exceeds the limit, a `... +N more` note is appended.
- **Who injects, when, and with what budget**:
  - **claude-code**: On `SessionStart` (all sources, 10000 budget).
  - **codex**: On `SessionStart` (startup/clear/resume, 10000 budget).
  - **cursor / trae×2 / zcode**: On `SessionStart` (6000 budget, 2s debounce).
  - **opencode**: Once per session on the first `chat.message` (10000 budget, deduplicated by an in-process `Set`, subagent sessions skipped). Note that opening injection is attempted only once per session and does not retry in-process after a failure.
  - **dsh**: Posts `profileDelivered` once per session (10000 budget; not re-posted after compaction).
  - **pi**: Injected into the `systemPrompt`, re-assembled for every prompt (10000 budget, always resident).
  - *Note:* `openclaw` and `hermes` do not perform profile injection.
- **Archive injection** (pulls the previous archive summary back upon resume):
  - **claude-code**: `source=resume/compact`, `token_budget=32000`, ≤5 `pre_archive_abstracts`.
  - **codex**: On resume, when the local `ovSessionId` has already been cleared (32000 budget / truncated to 6000 characters).
  - **opencode**: Executed as part B of the opening injection (32000 budget).
  - **pi**: Active in non-takeover mode (32000 budget).
- **Repo context injection**: Unique to `opencode`. It inserts the list of indexed repositories into the system prompt via `experimental.chat.system.transform`.

### 3.2.4 Timeout and budget chain

- **Family A client derivation**: With rewrite, the timeout is `max(timeoutMs, 45000)`; with expansion, it is `max(timeoutMs, 15000)`. The corresponding server-side fuses are 5s (expansion) and 30s (rewrite). By design, the client budget accommodates every server stage, ensuring the client never aborts early and loses the entire response.
- **Actual timeout values**:
  - **claude-code (cc)**: 15s (against a 60s hook budget).
  - **codex**: Recall enforces a hard 120s deadline for the entire hook, plus a 110s compression subprocess.
  - **cursor / trae×2 / zcode**: 15s (against a 20s host hook budget).
  - **opencode / dsh / pi**: 15s (`dsh` blocks the pre-step).
  - **openclaw**: Imposes a 5s hard timeout around the entire recall flow (including a 500ms health precheck). Since the default `recallPreferAbstract=false` means every leaf memory costs one extra read, this budget allows at most 1 find + 6 reads + 1 health check.
  - **hermes**: 4s total / 3s per request (configurable).
- **Injection budget**: The server's `max_tokens` defaults to 1600 (Family A harnesses do not send this by default, allowing the server to dictate the limit). Both `openclaw` and `hermes` utilize a 4000-character budget, opting to "skip an entry that does not fit" rather than truncating it.

### 3.2.5 Recall digest

**Server implementation (available to all callers)**: The context retrieval face (covering REST `mode="context"` and legacy `/recall`) accepts a `rewrite` parameter—which can be `false`, `true`, or `"auto"` (defaulting to `false`)—alongside `rewrite_max_bullets` (defaulting to 6, with a range of 1-20). When enabled, the server leverages the `query_planner` model to rewrite recall results into a digest with citations. (If `rewrite=true` but `query_planner` is unconfigured, it falls back to the main `vlm`; `"auto"` only takes effect if `query_planner` is explicitly configured). The digest features an `OpenViking memory digest:` header followed by bullet points (`- `). Each bullet must be ≤500 characters and must cite a valid `viking://` URI from the hit set (bullets with missing or out-of-range citations are dropped). If the model determines there are no relevant memories, it emits a sentinel value and clears the injection block, ensuring that turn is not recorded in the deduplication ledger. This model call is protected by a fuse (`retrieval.recall_rewrite_timeout_s=30s`). On timeout, it falls back to providing the un-rewritten, rendered block (`rewrite.py:78-141`, `pipeline.py:122-130`, `search.py:195-196`).

**Client-side status**:

- **claude-code**: `recallRewrite` supports four states: `off`, `client`, `server`, and `auto` (defaulting to **auto**). It initially probes for a local compressor via `claude --version` (caching the result for 7 days). If available, compression runs in a local subprocess: `claude -p --model sonnet --effort low --strict-mcp-config`. This subprocess has a 30s timeout, skips compression for inputs under 1500 characters, uses per-digest caching, and force-degrades its environment to prevent recursion. Subprocess failures fall back to the uncompressed block. URIs are snapped back to valid URIs using edit distance, and any irreparably broken bullets are dropped. If no local compressor is found, it sends `rewrite:"auto"`, deferring to the server. Notably, this is the *only* harness actively wired to the server-side rewrite.
- **codex**: The boolean `recallCompress` defaults to **true** and relies entirely on local compression (it does not utilize the server rewrite). The model profile is read from `~/.codex/models_cache.json` (candidates range from `gpt-5.3-codex-spark` to `gpt-5.6-luna`, cached for 7 days). The execution command is `codex --sandbox read-only --ask-for-approval never exec --ephemeral --ignore-user-config --skip-git-repo-check --output-last-message <tmp> -`, with a 110s timeout. If a runtime failure occurs, compression is disabled for the remainder of the session and re-probed at the next `SessionStart`. Outputs are normalized and truncated to 4000 characters. When compression is turned off or fails, a deterministic `fallbackDigest` takes over.
- **Other harnesses**: None of the other harnesses send the `rewrite` flag or compress locally; they simply inject the raw recall block returned by the server. Third-party applications directly calling the API can pass `rewrite` themselves to leverage the server digest.

### 3.2.6 Injection backflow protection

To prevent injected content from being captured a second time, the injection process wraps the content in deterministic tags (like `<openviking-context>`), which the capture mechanism then mechanically strips. Specifically, `capture-utils`' `sanitizeCapturedText` function removes injection blocks, digest blocks, metadata fences, and timestamp prefixes.

**Per-harness specifics:**
- **trae / zcode**: Utilize their own cleaning functions (zcode's strips three distinct types of injection blocks).
- **openclaw**: Strips `<relevant-memories>` twice—once when writing data back during `afterTurn`, and again when constructing the query for the next turn.
- **hermes**: Goes a step further by entirely dropping the `tool_call` and `result` of all three recall-type tools from the sync batch (while retaining write-type tools).
## 3.3 Session and commit lifecycle

### 3.3.1 Mechanism foundations

- **Write path**: The JS family routes all writes through `batch-send.mjs` (endpoint `POST /messages/batch`, capped at 100 messages per batch to match the server's `max_length=100` limit; on a 404/405 error, it gracefully degrades to sending one message at a time). Incremental cursors are implemented per integration (e.g., `cc` and `codex` compute a cursor from the transcript turn index; `cursor` uses `sha256(index+role+content)`; `zcode` relies on the rollout `turn_id`; `opencode` uses an event-stream Map; `dsh` uses an event allowlist; `pi` uses the branch entry watermark; and `hermes` slices by the current turn).
- **Commits are client-triggered** (see [§2.3](#_2-3-server-side-session-and-commit-semantics)): The server does not auto-commit by default. Any "threshold/trigger" condition mentioned in the tables below refers strictly to client-side logic.
- **Differences in `keep_recent_count`** (determining how much of a "live tail" a commit leaves for the host): The server default is 0. Here is what each integration passes: `cc`/`codex` pass 10 on threshold commits; `cursor`, `trae×2`, and `zcode` send an empty body `{}`, meaning 0 (every commit acts as a full archive); `opencode` and `dsh` pass 10; `pi` passes 10 outside takeover mode and 3 within it (locally, this means "keep 3 user turns," but the server interprets it as a raw message count, so fewer messages are actually retained); `openclaw` passes 10 on an `afterTurn` threshold trigger and 0 on `compact`/`reset`/`memory_store` operations; `hermes` always passes 0.
- **Write-path detachment** (`async-writer.mjs`, enabled by default for `cc`/`codex`/`zcode` on `Stop`): The execution flow is drain stdin → spawn detached worker → approve → write payload → unref. *(Note: If spawning fails, approval has not yet occurred, ensuring the synchronous fallback executes exactly once).* The detached worker forms its own process group, immunizing it against terminal signals. This is the crucial mechanism that makes `cc` reliable during shutdown and prevents `zcode` from losing writes upon `Ctrl+C`. **Side effect:** Once detachment is active, `Stop` no longer prints the `appended N turn(s)` notice (to restore this, set `OPENVIKING_WRITE_PATH_ASYNC=0`).

### 3.3.2 Regular commit triggers

| harness | turn-level threshold | explicit / boundary trigger | compaction trigger |
|---|---|---|---|
| claude-code | Stop: `pending_tokens ≥ 20000` (reads the server value), keep 10 | SessionEnd: unconditional; SubagentStop: unconditional (no threshold); SessionStart: replays pending | PreCompact: unconditional (runs synchronously, no detach) |
| codex / trae-cli | Stop: same as above, 20000 / keep 10 | SessionStart(startup\|clear): an active-window heuristic (exactly 1 state within 2min → commit; ≥2 → skip) + an idle-TTL sweep (>30min always commits and cleans up) | PreCompact: full commit (sends an empty body `{}`), then sets `ovSessionId=null` |
| cursor | stop: `capturedSinceCommit ≥ 8` (counted in messages, ~4 Q&A turns; purely client-side counting), keep 0 | sessionEnd: registered (but never reached in practice, see [§3.3.3](#_3-3-3-shutdown-method-×-harness-outcome-matrix)) | preCompact: unconditional |
| trae / trae-cn | Every Stop with content commits (no threshold), keep 0 | — | None (no PreCompact event upstream) |
| zcode | Same as trae (every Stop commits, keep 0; the rollout incremental cursor advances conservatively, so any missed turns are caught up on the next Stop in the same session) | — | None (no PreCompact event upstream) |
| opencode | `session.idle` path: after the flush runs, `pending_tokens ≥ 20000` must hold before it commits, keep 10 | `session.deleted` / `session.error`: forced commit; dispose: forced commit | Fires once before `experimental.session.compacting` and once after `session.compacted` (so one host compaction = two commits) |
| dsh | `turn/end`: `pending_tokens ≥ 20000` (30s timeout), keep 10 | Teardown (see [§3.3.3](#_3-3-3-shutdown-method-×-harness-outcome-matrix)) | None (does not listen for compaction events) |
| pi (takeover on by default) | `onTurnSynced`: when the locally estimated `pendingTokens ≥ 30000` and `lastSeenUserTurns > 3`, runs commitAndAdvance (keep 3; the overview polls 15 times at 2s intervals, and if it returns empty, the boundary does not advance, but pendingTokens is zeroed and retried once it has accumulated again) | Run `/viking commit` manually | `session_before_compact` (requires a non-empty `firstKeptEntryId`) |
| pi (takeover off) | After syncBranch runs: server-side `pending_tokens ≥ 20000`, keep 10 | `session_shutdown`: unconditional commit; run `/viking commit` manually | `session_before_compact`: unconditional commit |
| openclaw | afterTurn: `pending_tokens ≥ floor(tokenBudget × 0.5)` (ratio defaults to 0.5, tokenBudget defaults to 128000, making the threshold ~64000), wait=false, keep 10 | `before_reset` (running `/new` `/reset`): wait=true, keep 0; the `memory_store` tool: wait=true, keep 0 | `compact()`: wait=true, keep 0 (Phase2 polls for up to 5 minutes) |
| hermes | No threshold commit — every trigger is a session boundary: `on_session_end` (10s drain; if incomplete, this round aborts the commit), `on_session_switch` (covers `/new`, `/resume`, `/branch`, and compaction forks, async drain budget 65s), gateway cache eviction; `/undo` and in-place compaction do not commit. An idempotency set prevents duplicate commits; keep 0 | atexit fallback | Commits at fork-style compaction boundaries; in-place compaction does not commit |
| ov CLI | None | `ov session commit`; `ov add-memory` always commits in step 3 | — |
| ingest | `pending ≥ 6000` or 5s idle, keep 0; backfill runs `commit_if_needed` at the end of every session | Runs `_flush_all()` on exit | — |
| LangChain | `CommitPolicy.mode` defaults to `never`; the `pending_tokens` mode threshold is 8000; the `always` mode triggers on every record | Up to the caller | — |

### 3.3.3 Shutdown method × harness outcome matrix

Legend: **C** = commits; **C\*** = commits, with a precondition (see notes); **—** = does not commit (messages already POSTed stay in the server's live area: the message bodies are not lost, they wait for a later trigger to archive and extract them); **n/a** = not applicable. The server-side behavior is **—** on every row ([§2.3](#_2-3-server-side-session-and-commit-semantics)).

| harness | normal exit | Ctrl+C | SIGTERM | SIGHUP / close terminal / close window·tab | kill -9 / crash | recovery path |
|---|---|---|---|---|---|---|
| claude-code | **C** (SessionEnd → a detached child process commits, so the user does not wait) | **C** | **C** | **C** (the detached worker forms its own process group and is unaffected by SIGHUP) | **—** | Next Stop over the threshold / `/compact` / next SessionEnd |
| codex / trae-cli | **—** (no SessionEnd-style event upstream) | **—** | **—** | **—** | **—** | The heuristic on the next `SessionStart(startup\|clear)` (exactly 1 active state within 2min → commit) or the 30min idle-TTL sweep; with ≥2 concurrent sessions, the heuristic defers to the TTL sweep |
| cursor | **—** (closing a chat or opening a new chat fires no event) | **—** | **—** | **—** (`sessionEnd` is registered and only fires on window_close, but by then the host has destroyed the shell-exec host, causing the hook to abort before spawn) | **—** | A session ending below the 8-message watermark leaves its tail waiting for later messages in the same session to trigger a commit |
| trae / trae-cn | **—** (no session-end-style event) | **—** | **—** | **—** | **—** | Every Stop has already committed, meaning the most data left to archive equals the last in-flight turn |
| zcode | **—** (no session-end-style event) | **C\*** | **—** | **—** | **—** | C\* precondition: The Stop for that turn had already fired when Ctrl+C arrived (the detached worker finishes writing as usual); every Stop has already committed, and missed turns are recovered by the rollout cursor on the next Stop in the same session |
| opencode | **C\*** (≥1.15.11 `dispose` calls `flushAll({commit:true})`, covering all four shutdown paths; <1.15.11 has no such hook → —) | **C\*** | **C\*** | **C\*** | **—** | C\* precondition: The host shutdown budget is 5s, while a single session takes up to 5s for health + 10s for batch + 30s for commit, and multiple sessions process serially. A commit overrunning the budget is cut off, and the pending queue does not cover this (unsettled fetches are never queued); after a restart, `init()` does not proactively flush leftover sessions |
| dsh | **C** (Cordis teardown triggers one 3s-timeout commit per session, no threshold) | **C** (the first one; a second Ctrl+C force-quits → —) | **C** | **—** (no SIGHUP listener) | **—** | The teardown commit and the threshold commit share a serial write chain, meaning it may not fit inside the 5s process grace period if a slow request precedes it; in the web form, closing the browser tab does not trigger a teardown |
| pi (takeover default) | **—** (`session_shutdown` fires on every shutdown path and is awaited, but the handler persists local takeover state and does not commit) | **—** | **—** | **—** | **—** | The next run accumulating 30000, or a manual `/viking commit` |
| pi (takeover off) | **C** (`await sync.commit()`, failures go to the pending queue) | **C** | **C** | **C** | **—** | — |
| openclaw | **—** (upstream sends an awaited `session_end(reason=shutdown\|restart)`; the handler caches agentId and returns without triggering a commit; `gateway_stop` is not registered; no signal handling) | **—** | **—** | **—** | **—** | Explicit `/new` `/reset` and the ~50% threshold; sessions below the threshold rely on these two paths for archiving |
| hermes | **C** (atexit `_run_cleanup` → 10s flush → on_session_end) | **C** (both interactive and non-interactive trigger atexit) | **C** (`_signal_handler`, grace period defaults to 1.5s → clean exit → atexit) | **C** (SIGHUP follows the same path as SIGTERM) | **—** (atexit does not run) | If the drain does not finish, this round aborts the commit (avoiding a half-written commit); an exit watchdog kills slow commits at 30s |
| ov CLI | n/a (one-shot command) | n/a | n/a | n/a | n/a | No pending queue; just re-run a failed command |
| ingest | **C** (`finally _flush_all`) | **C** (SIGINT → stop) | **C** | **—** (no SIGHUP handler) | **—** | The only write path offering crash recovery: `needs_commit` is persisted in the cursor store, and the subsequent run's reconciliation completes the commit |
| LangChain / Open WebUI / Agent Plugins / generic MCP | **—** (no session lifecycle hooks; the `DELETE /mcp` sent by an MCP proxy on exit only releases the protocol session and does not trigger memory commits) | — | — | — | — | LangChain relies on the caller's `close()`; the in-process pending-commit set disappears alongside the process |

**Three reading notes**:

1. Five integrations commit on a normal exit: `claude-code`, `opencode` (≥1.15.11), `dsh`, `pi` (takeover off), and `hermes`. The rest rely on the recovery mechanisms detailed in the "recovery path" column.
2. No integration commits under `kill -9` — messages already submitted remain in the server's live area and are archived the next time the same session triggers a commit. The server offers a per-session idle fallback ([§2.3](#_2-3-server-side-session-and-commit-semantics)), which the current plugins do not utilize by default.
3. `trae×2` and `zcode`, which commit on every turn, offer the simplest shutdown semantics (the maximum data left to archive equals the last round that never reached Stop), at the cost of a full archive and memory extraction on every Stop (keep 0).

### 3.3.4 pending queue / offline compensation comparison

| harness | mechanism | notes |
|---|---|---|
| cc / cursor / trae×2 / zcode / opencode / dsh / pi | On-disk queue `~/.openviking/pending` (0700/0600) | Only retryable failures are queued (4xx errors, including 401/403, are considered non-retryable and are not queued, though they appear in debug logs); replay runs at session start: ≤50 entries per run, ≤3 attempts per entry, TTL 7 days; `.processing` claims entries atomically, with a 10min stale reclaim; an addMessage failure breaks execution immediately to preserve order |
| codex / trae-cli | No on-disk queue | When the server is unreachable, compensation occurs because the `capturedTurnCount` cursor does not advance, prompting the next Stop to resend the same batch. This works provided the process survives and a subsequent turn occurs |
| openclaw | No local queue | An addSessionMessage failure is caught, and that turn's messages are not replayed |
| hermes | In-process daemon-thread queue | The drain operates on a strict budget (10s/65s); nothing is written to disk |
| LangChain | In-process `_pending_commit_sessions` set | A failed commit is retried automatically during the next record; nothing is written to disk. On partial success, it raises an `OpenVikingPartialWriteError` (carrying `messages_written`, `input_messages_consumed`, and `context_attached`, allowing the caller to slice by position and retry the suffix) — making this the only protocol across all integrations that reports partial success |
| ingest | SQLite cursor store + single-instance lock | The intent is persisted before appending. After a crash, a reconciliation process checks the server's message count to determine if the batch landed—making this the only write path with true crash-recovery semantics |

### 3.3.5 subagent session comparison

| harness | handling |
|---|---|
| claude-code | Offers the most complete isolation: SubagentStart derives a separate `cc-<sid>__subagent-<agent_id>` session, and SubagentStop reads the subagent transcript, pushes it, commits unconditionally, and clears the state |
| codex / trae-cli | No separate session: Subagent output (`agent_message` / `sub_agent_activity`) is folded into the main session's assistant/tool components |
| opencode | `oc-<parent>__subagent-<child>` hangs under the parent namespace; the session-start injection skips subagents (though recall does not); ID derivation is sensitive to event order — when `chat.message` arrives before `session.created`, the `__subagent-` suffix is lost |
| dsh | Each subagent operates as a separate `dsh-<id>` session, preserving no parent-child relationship; N subagents = N profile injections + N separate sessions |
| hermes | `delegate_task` passes `skip_memory=True` → the subagent is disconnected from OV (no session, recall, or tool surface); subtask output is not fed back |
| cursor / trae×2 / zcode / pi / openclaw | No specific subagent handling (anything generating its own session ID becomes its own session; otherwise, it mixes into the main session. `openclaw` can mask this behavior using `bypassSessionPatterns`) |
| ingest | The `claude_code` adapter skips `isSidechain` / `isMeta` records, meaning subagent conversations are not ingested |
## 3.4 Compaction takeover

### 3.4.1 Decision matrix

| harness | Stance on host compaction | Before compaction | After compaction |
|---|---|---|---|
| claude-code | No takeover | PreCompact commits synchronously. This is the only write path that does not detach, as CC rewrites the transcript immediately afterward. | A SessionStart with `source="compact"` re-injects OV's `latest_archive_overview` plus ≤5 abstracts. |
| codex / trae-cli | No takeover | PreCompact backfills uncaptured turns → full commit → `ovSessionId=null`. If the backfill is incomplete, no commit occurs and it is left for retry. There is no PostCompact wiring; it relies instead on the transcript shrinkage observed at Stop for defensive correction. | Injects the archive digest upon resume. |
| cursor / trae×2 / zcode | No takeover | Cursor: `preCompact` commits unconditionally (Trae×2/Zcode lack this upstream event). | — |
| opencode | No takeover | Flush and commit prior to compaction. | Flush and commit again once `session.compacted` fires (two commits in total). |
| dsh | Unaware. It does not listen for compaction events; injection rides on a pre-step user message and shrinks alongside the host's compaction. The profile is not re-sent. | — | — |
| pi | **Two-layer takeover** (on by default, [§3.4.2](#_3-4-2-pi-takeover)) | `session_before_compact`: flush → commit → `pollOverview`. On success, it returns a custom compaction summary that overrides Pi's. On failure, it fails open and falls back to Pi's default compaction. | Calls `resetBoundary` upon success. |
| openclaw | **Full takeover**: `ownsCompaction: true`, the host no longer runs its own summary ([§3.4.3](#_3-4-3-openclaw-contextengine)) | `compact()` = `commit(wait=true, keep 0)` → reads the overview back and utilizes it as the summary. | The main `assemble` rebuilds context with `[Session History Summary]`. |
| hermes | No takeover (The `on_pre_compress` interface is reserved but currently plays no role in compaction summaries.) | A fork-style compaction boundary triggers a commit of the old session, whereas in-place compaction does nothing. | — |

### 3.4.2 pi takeover

- The takeover surface involves rewriting the messages of the `context` event; Pi's native history storage remains untouched. The trigger is token pressure (30000 tokens + keep 3 turns) rather than Pi's native compaction event.
- The replacement process: first locate the boundary, then replace every preceding message with a single synthetic user message, `[OpenViking Session Context]`. The overview within this message is truncated at 3000 tokens. Its timestamp is set to the first kept message minus 1, which stabilizes the provider payload to ensure prompt cache hits.
- The data source is `latest_archive_overview` fetched via `GET /sessions/{id}/context` (polled 15 times at 2-second intervals). This state is persisted in Pi's own branch via the custom entry `ov-takeover`.
- Failure stance: fail-open, reverting to the full history. This occurs under three fallback conditions: a fingerprint mismatch, a history shorter than the boundary, or an unavailable overview.
- Relationship to Pi's native compaction: upon success, `session_before_compact` returns `{compaction: {summary, firstKeptEntryId, …, details: {source: "openviking"}}}` to override Pi's summary. If `firstKeptEntryId` is missing, it falls through to Pi's default compaction behavior.

### 3.4.3 openclaw ContextEngine

- Implements the host's `ContextEngine` interface. The `assemble()` function splits into two branches: `transformContext` (handling recall pre-injection only, protected by 5 passthrough guards) and main `assemble` (calls `getSessionContext(tokenBudget)` → replaces the host's live history with the server response using a four-tier budget split, protected by 3 passthrough guards and a provider-message sanitization pipeline).
- `compact()` executes `commit(wait=true, keep 0)` (utilizing 500ms polling, with Phase 2 capped at 5 minutes) → `latest_archive_overview` becomes the summary, and the last segment of `archive_uri` serves as `firstKeptEntryId`. Note that `customInstructions` and `compactionTarget` are reserved interfaces that currently play no role in the compaction output.
- `ingest()` and `ingestBatch()` are deliberate no-ops; all writes are routed through `afterTurn`.
- If an archive exists, a 20-line "Session Context Guide" is injected via `systemPromptAddition`. This instructs the model to re-read the summary before claiming it has "no information" and to attempt at least two different keyword sets using `ov_archive_search`.

### 3.4.4 pi vs. openclaw takeover

| Dimension | pi takeover | openclaw ContextEngine |
|---|---|---|
| Host contract | Rewrites the messages of a single context hook | Registers a `ContextEngine` with `ownsCompaction: true` |
| Source of truth for history | Pi's local branch | OV server-side `getSessionContext` |
| Trigger | Client-side token threshold (30000) + keep 3 turns | Host invocations of `assemble` or `compact` |
| Compaction output | A single synthetic user message (truncated at 3000 tokens) | The fully rebuilt messages array plus a compaction summary |
| Failure stance | Fail-open, reverting to the full history | Passthrough, reverting to the host's live messages |
| Recall and session | The context interface carries `session_id` | `/find` does not (expansion and ledger are excluded) |

## 3.5 Type boundaries for writes and deletes

### 3.5.1 Write boundary

There are three primary guards on MCP `write` and REST `content/write` (`content_write.py`). First, the writable domain is strictly limited to `viking://resources`, `viking://user`, and `viking://agent`. Second, file extensions for new files must match the whitelist (`.md`, `.txt`, `.json`, `.yaml`, `.yml`, `.toml`, `.py`, `.js`, `.ts`). Third, the four managed subtrees (`skills/`, `peers/`, `privacy/`, `sessions/`) under the user root are designated as read-only (`_USER_MANAGED_SUBTREES`). Existing `.abstract.md` and `.overview.md` sidecars can be body-updated, but public write APIs cannot create them.

### 3.5.2 Delete boundary

**Tier 1: The universal server-side defense (shared by every delete entry point).** The first statement of `VikingFS.rm`, `_ensure_delete_access` (`_access.py:182-229`), enforces five checks: namespace accessibility; ongoing user deletions (returns `FailedPrecondition`); actor-peer hidden views (returns `PermissionDenied`); namespace root protection (bare `viking://`, along with the `viking://user` and `viking://agent` roots, are unconditionally refused); and restricting deletes in `viking://temp` to ROOT only. This tier defends exclusively at the namespace root level and does not differentiate between memory, resource, or skill types. Those type-level distinctions are enforced on the client side by the subsequent three tiers.

**Tier 2: No client-side additions (the MCP surface + dsh / pi / langchain / ov rm).** Here, the differences lie purely in the parameters. In the `viking_forget` implementation for dsh and Pi, `recursive` is hardcoded to false (preventing directory deletion), and semantic query deletions require a score > 0.8. LangChain's `viking_forget` exposes `recursive` as a model-controllable parameter, although the tool is not exposed by default. The CLI command `ov rm -r` explicitly enables recursion without a confirmation prompt, whereas the TUI's `d` key enforces a y/n confirmation and bans the deletion of root or scope directories.

**Tier 3: The two memory-only delete surfaces.**

- In openclaw's `memory_forget`, three regex whitelists restrict deletions strictly to `viking://user/[…/]memories`, `viking://user/<u>/peers/<p>/memories`, and `viking://agent/[…/]memories`. Explicit URIs failing to match these are refused outright. Search-path candidates must pass this same guard first; automatic deletion only proceeds if a candidate is unique and scores ≥ 0.85. Otherwise, candidates are listed so the agent can explicitly select one. The underlying URL always pins `recursive=false`.
- In hermes' `viking_forget`, there are six sequential validations: non-string or empty inputs are refused; a scheme other than `viking://` is refused; queries or fragments are refused; directories or any files not ending in `.md` are refused; the path must match one of four permitted memory path structures and contain at least two additional segments after the `memories` segment (ensuring `memories/` and its category directories are never deleted); finally, the filename must not be `.abstract.md` or `.overview.md`.

**Tier 4: No deletion offered by default (LangChain / Open WebUI).** LangChain's `viking_forget` is only exposed as a tool when configured with `profile="admin"` or `allow_forget=True`. Open WebUI does not offer any deletion tools.

**Add/delete boundary for skills:** The entry points for adding skills are openclaw's `add_skill` (enabled by default), `ov add-skill`, and REST. The delete surfaces that remain entirely read-only for skills (permitting neither addition nor deletion) are openclaw's `memory_forget` and hermes' `viking_forget`. Conversely, the MCP surface, dsh, Pi, and `ov rm` cannot *add* a skill but can *delete* one. This is because addition is blocked by `_USER_MANAGED_SUBTREES` on the write path, whereas deletion succeeds because the delete path does not check that specific constraint.
## 3.6 Degradation and fault tolerance

### 3.6.1 Decision matrix

| harness | When the server is unreachable | Negative cache | HTTP retry | Failure blocks the host |
|---|---|---|---|---|
| claude-code | All hooks catch exceptions → approve (never blocks); at session-start, even pending replays are skipped | context-face 6h + host-cli probe 7d + health 5s | None (relies on pending replay); `peer_scope` degrades once; batch falls back to sequential | No (`uri-guard` deny is by design) |
| codex / trae-cli | All hooks catch exceptions → noop | context-face 6h + compressor runtime_failed (until the next startup) | Same as above (no on-disk pending; resends from the cursor) | No |
| cursor/trae×2/zcode | Fetch errors are swallowed as `status:0`, and catch returns an empty injection; silently skips if the lock isn't acquired within 5s | context-face 6h (no negative cache for unreachable servers; every turn waits the full 15s) | None | No |
| opencode | All paths catch exceptions → WARN; the `event`/`dispose` hooks lack try/catch blocks (non-retryable commit failures bubble up to the host) | context-face 6h only; `/health` is uncached (one round trip per turn) | No synchronous retry; the MCP proxy retries once each for 401/403 and 400/404 | Mostly no (except `event`/`dispose`) |
| dsh | The client swallows all exceptions; `ensureState` failures are not cached (when the server is unreachable, each pre-step makes two 5s health calls) | context-face 6h + an in-process user-space cache that never expires | None; pending queue replays 3 times across processes | Yes (pre-step runs profile+recall serially; session/flush blocks) |
| pi | On health failure, `start()` returns early; subsequent prompts silently retry the connection | context-face 6h | None; pending queue only | Partly (`session_shutdown` is awaited: ~0s with takeover, max 30s without; on a `turn_end` network error, each message waits 10s) |
| openclaw | Client construction never fails; health checks swallow exceptions; recall is skipped if the 500ms precheck fails | No negative cache (one 500ms health precheck per turn) | None (a single fetch); Phase2 polling in commit/afterTurn | No (except when `memory_store` re-raises; `compact()` blocks for up to 5 minutes) |
| hermes | `_client=None` acts as the runtime negative cache (stops retrying in the current process, except for the local auto-start waiter) | No separate structure (`_client=None` handles this) | One retry with a trusted identity, one retry with a fresh sync client, multi-tier degradation; failed commits are not retried | No (a single background worker + per-provider try/except) |
| ov CLI | Mostly exits with 1; `ov status` in table mode always exits with 0; `ov health` exits with 0 even when unhealthy | None | Only one retry on a gateway 401 challenge | n/a (no host) |

### 3.6.2 Common timeouts

General HTTP timeouts are 15000ms (with a 1000ms floor). MCP proxy requests time out at 15000ms, while DELETE requests are pinned to 2000ms. Cross-process lock waits are 5s, becoming stale at 60s; pending `.processing` entries are reclaimed after 10 minutes. Note that the MCP proxy does not register SIGHUP signals (closing the terminal does not send a `DELETE /mcp` request), but since the server runs with `stateless_http=True`, the impact is minimal.

## 3.7 Additional UX comparison

| harness | statusline | slash command | rule/skill | setup wizard | other |
|---|---|---|---|---|---|
| claude-code | ✅ A separate process writes to `settings.json` (rich segments, 1-min TTL) | ✅ `/openviking-memory:ov` (server status + identity + injection provenance) | 1 experience skill | ✅ Line-based Q&A | Diagnostic scripts (`debug-recall`/`debug-capture`); `uri-guard` is not gated by the plugin toggle |
| codex / trae-cli | ❌ | ❌ | 1 experience skill | ✅ | 8-step SOP in `VERIFICATION.md` |
| cursor | ❌ | ❌ | Rule (`alwaysApply`) + skill | ❌ (Shares the installer TUI) | Standalone `uri-guard`, independent of the plugin toggle |
| trae/trae-cn | ❌ | ❌ | None | ❌ | — |
| zcode | ❌ | ❌ | None | ❌ | — |
| opencode | ❌ (Has toasts) | ❌ | None (Deliberately omitted) | ✅ | — |
| dsh | ❌ | ❌ | 1 `openviking-memory` skill (own isolated `ctx.skills` provider) | ❌ | `ctx.provide("openvikingMemory")` allows other Cordis plugins to build upon it |
| pi | ✅ `ctx.ui.setStatus` | ✅ `/viking` `/viking commit` | None | ✅ | `e2e-live.sh` |
| openclaw | ❌ | ✅ 5 commands (`/add-resource`, `/add-skill`, `/ov-search`, `/ov-query-config`, `/ov-recall-trace`) | 3 skills shipped with the plugin | ✅ (Key role detection, version compatibility checks, and a `status` command) | Gateway HTTP routes for visualizing recall traces; feature-gated RPCs; health-check script |
| hermes | ❌ | ❌ | None | ✅ Multi-level curses menus | `hermes memory status` (includes env override lists); `hermes backup` covers `ovcli.conf` |
| ov CLI | ❌ | ❌ (The CLI itself acts as the command) | None | ✅ TUI wizard | Comprehensive help system (63 curated entries); language gating; `ov tui` full-screen file browser (includes in-terminal image previews) |

---

# 4. Harness profile cards

Each card serves as a quick-reference entry point. It records only the facts and differences unique to a specific harness, linking back to the dimension chapters for shared mechanisms. All cards follow the same structure: Form / Capability highlights / Behavior notes / Configuration / Dimension index.

## claude-code

- **Integration docs**: [Claude Code Memory Plugin](./02-claude-code.md)
- **Form**: A Claude Code plugin (marketplace) featuring a four-in-one architecture: 9 hooks, an MCP proxy (passing through 15 tools), a slash command, a statusline, and 1 experience skill. Version 0.4.4.
- **Capability highlights**: The harness with the broadest hook coverage — `SessionStart` (120s) / `UserPromptSubmit` (60s) / `PostToolUse:Read` (5s, the skill-experience hook, off by default) / `PreToolUse:Read|Glob|Grep` (5s, `uri-guard`) / `Stop` (45s) / `PreCompact` (30s) / `SessionEnd` (30s) / `SubagentStart` (10s) / `SubagentStop` (45s). Recall digesting is enabled by default (local `claude -p`, falling back to the server-side rewrite automatically when the local CLI is unavailable, [§3.2.5](#_3-2-5-recall-digest)). Provides full sub-session isolation via `SubagentStart`/`Stop` ([§3.3.5](#_3-3-5-subagent-session-comparison)), along with a statusline, slash command, and `uri-guard`. All shutdown paths except `kill -9` trigger a commit ([§3.3.3](#_3-3-3-shutdown-method-×-harness-outcome-matrix)).
- **Behavior notes**: The session ID format is `cc-<raw_CC_session_id>`, while subagents use `…__subagent-<agent_id>`. `Stop` commits at a threshold of 20000 (keep 10), and `PreCompact` commits synchronously. Automatic recall excludes resources ([§3.2.1](#_3-2-1-mechanism-foundation-one-shared-pipeline-two-server-side-paths)). The incremental cursor is stored in `/tmp` (if cleared by the system, the entire session is pushed again).
- **Configuration**: Configured via environment variables, `ovcli.conf` (`plugin.claude_code`), and `ov.conf` (`claude_code` as per [§3.1.4](#_3-1-4-configuration-layers)), offering roughly 40 tunable parameters. The compressor command and model are hardcoded to `claude`/`sonnet`/`low`/30s.
- **Dimension index**: tool surface [§2.1](#_2-1-server-side-mcp-tool-surface) | recall [§3.2](#_3-2-automatic-recall-and-injection) | commit [§3.3.2](#_3-3-2-regular-commit-triggers)/[§3.3.3](#_3-3-3-shutdown-method-×-harness-outcome-matrix) | compaction [§3.4](#_3-4-compaction-takeover) | degradation [§3.6](#_3-6-degradation-and-fault-tolerance) | UX [§3.7](#_3-7-additional-ux-comparison).

## codex

- **Integration docs**: [Codex Memory Plugin](./04-codex.md)
- **Form**: A Codex plugin (marketplace). Features 4 hooks (`SessionStart` 70s / `UserPromptSubmit` 130s / `Stop` 30s / `PreCompact` 60s), an MCP proxy, and 1 experience skill. Version 0.7.5.
- **Capability highlights**: A local recall-compression pipeline (`codex exec`, [§3.2.5](#_3-2-5-recall-digest)). The `SessionStart` active-window heuristic, combined with an idle-TTL scan, automatically reclaims messages left unarchived by earlier sessions ([§3.3.3](#_3-3-3-shutdown-method-×-harness-outcome-matrix)).
- **Behavior notes**: The session ID format is `cx-<safeId>` (derived deterministically without reading state). Lacks a shutdown hook (upstream provides no `SessionEnd`), so commits rely on the reclaim path during the next startup. Has no on-disk pending queue (while offline, the cursor simply doesn't advance, and the next turn resends to compensate, [§3.3.4](#_3-3-4-pending-queue-offline-compensation-comparison)). Active-window is 120000ms / idle-TTL is 1800000ms (configured via env). `Stop` detaches by default (the `appended N turn(s)` notice is hidden by default).
- **Configuration**: Configured via environment variables, `ovcli.conf` (`plugin.codex`), and `ov.conf` (`codex`). Hooks transmit both `Bearer` and the `X-API-Key` compatibility headers ([§3.1.3](#_3-1-3-credential-systems)).
- **Dimension index**: tool surface [§2.1](#_2-1-server-side-mcp-tool-surface) | recall [§3.2](#_3-2-automatic-recall-and-injection) | commit [§3.3.2](#_3-3-2-regular-commit-triggers)/[§3.3.3](#_3-3-3-shutdown-method-×-harness-outcome-matrix) | degradation [§3.6](#_3-6-degradation-and-fault-tolerance).

## trae-cli (TraeCode CLI 2.0)

- **Integration docs**: [TRAE Memory Integration](./13-trae.md)
- **Form**: TraeCode CLI 2.0 is a Codex-family CLI (binary `traecli`, user config `~/.trae/traecli.toml`, TUI support for `/plugins`, `/skills`, and `/mcp`). OpenViking integrates via a **codex plugin alias install**: using `--harness trae-cli` reuses the Codex installation flow, redirecting only the install parameters (binary, home, and config paths) to TraeCode CLI.
- **Capability surface**: Identical to Codex: 4 hooks, an MCP proxy, an experience skill, local recall compression, active-window/idle-TTL commit reclamation, and resume-archive injection. Refer to the Codex profile card.
- **Version support**: TraeCode CLI 2.0 only. 1.0 and 2.0 are not the same CLI — only 2.0 is Codex-family, and only 2.0 can use the codex plugin alias install. The earlier standalone plugin for 1.0, `examples/trae-cli-memory-hooks` (the `~/.trae/cli/hooks.json` + `[mcp_servers."openviking-memory"]` approach), is deprecated.
- **Dimension index**: Same as the Codex card.

## cursor

- **Integration docs**: [Cursor Memory Integration](./12-cursor.md)
- **Form**: Config-driven (modifies `~/.cursor/hooks.json` and `mcp.json`), featuring an MCP proxy, an always-on rule, and a skill. Includes 7 hooks: `sessionStart` (30s) / `beforeSubmitPrompt` (20s) / `beforeReadFile` (5s) / `beforeShellExecution` (5s) / `stop` (30s) / `preCompact` (30s) / `sessionEnd` (30s). The shared library is loaded via relative imports (no vendoring).
- **Capability highlights**: Features a dual `uri-guard` on `beforeReadFile` and `beforeShellExecution` (independent of the plugin toggle). The rule and skill are installed alongside it.
- **Behavior notes**: The session ID format is `cu-<conversation_id>`. `stop` commits every 8 messages (`commitTurnThreshold=8`, counted in messages, keep 0). `sessionEnd` only fires on `window_close`. By then, the host has already destroyed the shell-exec host, so it practically never runs ([§3.3.3](#_3-3-3-shutdown-method-×-harness-outcome-matrix)) — sessions ending below the 8-message watermark leave their tail to be archived by a later message in the same session ([§3.3.3](#_3-3-3-shutdown-method-×-harness-outcome-matrix)). If the server is unreachable, every turn waits out the full 15s recall timeout.
- **Configuration**: Configured strictly via environment variables (the `plugin` section is ignored).
- **Dimension index**: tool surface [§2.1](#_2-1-server-side-mcp-tool-surface) | recall [§3.2](#_3-2-automatic-recall-and-injection) | commit [§3.3.2](#_3-3-2-regular-commit-triggers)/[§3.3.3](#_3-3-3-shutdown-method-×-harness-outcome-matrix) | degradation [§3.6](#_3-6-degradation-and-fault-tolerance).
## trae / trae-cn (IDE editions)

- **Integration docs**: [TRAE Memory Integration](./13-trae.md)
- **Form**: Config-driven (`~/.trae{,-cn}/hooks.json` + a platform-specific mcp.json) utilizing an MCP proxy. It features 4 hooks: SessionStart(30s) / UserPromptSubmit(20s) / PreToolUse:Read\|Glob\|Grep\|Bash\|RunCommand(5s) / Stop(30s). The shared library is introduced via relative imports, and the MCP server is named `openviking`.
- **Capability highlights**: Features the simplest and most direct behavior of the group. Every Stop that carries content is committed (keep 0). Consequently, upon shutdown, the largest possible backlog is merely the last in-flight turn ([§3.3.3](#_3-3-3-shutdown-method-×-harness-outcome-matrix)).
- **Behavior notes**: trae and trae-cn differ only in their session ID prefixes (`tr-` vs. `trcn-`) and installation paths. The same workload lands in two separate sets of sessions across the two clients, and cross-client sharing occurs via the server-side memory space after extraction rather than by reusing sessions. Note that there is no handling for PreCompact, status lines, skills, or subagents.
- **Configuration**: Environment variables only.
- **Dimension index**: tool surface [§2.1](#_2-1-server-side-mcp-tool-surface) | recall [§3.2](#_3-2-automatic-recall-and-injection) | commit [§3.3.2](#_3-3-2-regular-commit-triggers)/[§3.3.3](#_3-3-3-shutdown-method-×-harness-outcome-matrix).

## zcode

- **Integration docs**: [Community Integrations → ZCode](./08-community-plugins.md)
- **Form**: Config-driven (merged into `~/.zcode/cli/config.json`, forcing `hooks.enabled=true`) utilizing an MCP proxy. It features 4 hooks: SessionStart(30s) / UserPromptSubmit(20s) / PreToolUse:Read\|Glob\|Grep(5s) / Stop(30s). This is the only harness that fully vendors all 18 shared files. Version 0.1.1.
- **Capability highlights**: The rollout file `~/.zcode/cli/rollout/model-io-<sid>.jsonl` serves as the source of truth for increments (a `lastTurnId` diff backfills any missed Stop events). Stop detaches by default, meaning Ctrl+C does not result in lost writes.
- **Behavior notes**: Commits on every Stop (keep 0). The capture path only strips the three types of injection blocks without performing any further text cleanup ([§3.2.6](#_3-2-6-injection-backflow-protection)). The initial capture reads the entire rollout at once, meaning that installing it into a long-running session will produce a single large push.
- **Configuration**: Environment variables only; `OPENVIKING_WRITE_PATH_ASYNC` takes effect for zcode.
- **Dimension index**: tool surface [§2.1](#_2-1-server-side-mcp-tool-surface) | recall [§3.2](#_3-2-automatic-recall-and-injection) | commit [§3.3.2](#_3-3-2-regular-commit-triggers)/[§3.3.3](#_3-3-3-shutdown-method-×-harness-outcome-matrix).

## opencode

- **Integration docs**: [OpenCode Plugin](./10-opencode.md)
- **Form**: Provided as the npm plugin `@openviking/opencode-plugin`, featuring a config hook that injects the MCP entry itself (tools carry the `openviking_` prefix). It exposes 7 plugin hooks: config / event / tool.execute.before / experimental.chat.system.transform / chat.message / experimental.session.compacting / dispose. Version 0.2.4.
- **Capability highlights**: The `dispose` hook covers all four standard shutdown paths (on hosts ≥1.15.11). The repository list is injected into the system prompt ([§3.2.3](#_3-2-3-profile-opening-injection)). It boasts the richest host event surface of any integration, with `session.idle`, `compacted`, `deleted`, and `error` each carrying their own specific semantics.
- **Behavior notes**: Features a `commitTokenThreshold` of 20000 (positive values only; 0 falls back to the default) and a commit timeout of 30000ms. A single host compaction equates to two commits. Within the `dispose` hook's 5-second host budget, a slow commit spanning several sessions might be cut short, and the pending queue does not cover this scenario ([§3.3.3](#_3-3-3-shutdown-method-×-harness-outcome-matrix)). On host versions below 1.15.11, the `dispose` hook is unavailable, meaning shutdowns do not trigger a commit. Leftover sessions are not flushed upon restart. The opening injection is attempted once per session ([§3.2.3](#_3-2-3-profile-opening-injection)). Finally, directory matching in `bypassSessionPatterns` does not apply to opencode, as the input does not carry a current working directory (cwd).
- **Configuration**: Handled via `openviking-config.json` (searched at 4 levels) + environment variables.
- **Dimension index**: tool surface [§2.1](#_2-1-server-side-mcp-tool-surface) | recall [§3.2](#_3-2-automatic-recall-and-injection) | commit [§3.3.2](#_3-3-2-regular-commit-triggers)/[§3.3.3](#_3-3-3-shutdown-method-×-harness-outcome-matrix) | subagent [§3.3.5](#_3-3-5-subagent-session-comparison).

## dsh (DeepSeek Harness)

- **Form**: This is the only in-process, native Cordis plugin (`export function apply`). It natively registers 7 `viking_*` tools (`viking_search/read/browse/remember/forget/add_resource/archive_expand`) and communicates directly via REST. It features 4 events: agent/session-start (emit) / agent/pre-step (waterfall) / session/event / session/flush. Version 0.1.0.
- **Capability highlights**: `ctx.provide("openvikingMemory")` allows other Cordis plugins to build upon it. Pre-step injection is transmitted as a user message, aligning with the `complete:true` rendering mode of the DSH persona.
- **Behavior notes**: The unified installer covers dsh and asks which profile to install into (default `web`, overridable with `--dsh-profile`); npm is the bundle's only distribution channel, so the github/tos choice does not apply and every mode except `dev` installs the published package; `dev` packs the checkout first, because `dsh plugin` forwards to pnpm and a linked source tree cannot resolve the dsh peers the bundle imports. The teardown commit is allocated 3 seconds with no threshold, and it does not trigger on SIGHUP or a consecutive Ctrl+C ([§3.3.3](#_3-3-3-shutdown-method-×-harness-outcome-matrix)). Compaction remains invisible to the plugin: injected content shrinks alongside the host's compaction, and the profile is not re-injected. Each subagent is assigned its own session ([§3.3.5](#_3-3-5-subagent-session-comparison)). The tool surface is the server's own MCP surface, reached through the same stdio proxy the other integrations use and published under `mcp__openviking__*`, so a server upgrade adds tools without a bundle release; the trade-off is that the proxy runs once per profile, so tool calls carry a process-level actor peer and `remember` is not session-scoped (recall, capture, and commit still resolve a peer per session). The bundle also ships the shared `openviking-memory` skill. Additionally, `uri-guard` matches tool names without normalizing case.
- **Configuration**: Configured via cordis patch + 4 environment variables. For credentials, the patch overrides env vars; for behavior toggles, env vars override the patch.
- **Dimension index**: tool surface [§1.1](#_1-1-active-tool-surface-agentic-calls) | recall [§3.2](#_3-2-automatic-recall-and-injection) | commit [§3.3.2](#_3-3-2-regular-commit-triggers)/[§3.3.3](#_3-3-3-shutdown-method-×-harness-outcome-matrix) | degradation [§3.6](#_3-6-degradation-and-fault-tolerance).

## pi (pi Coding Agent Extension)

- **Integration docs**: [pi Coding Agent Extension](./11-pi.md)
- **Form**: Operates as a native pi extension (loaded from a directory and dynamically transpiled from TS by jiti). It registers 7 native `viking_*` tools and uses direct REST communication (since pi lacks MCP support). It features 8 events + a `/viking` command. Version 0.1.0.
- **Highlights**: Features takeover compaction (enabled by default, [§3.4.2](#_3-4-2-pi-takeover)). Employs a two-stage recall system: it queues at `before_agent_start` and performs synchronous retrieval during the context event, ensuring the current turn's prompt receives its corresponding memories. It also includes a status line. The `session_shutdown` event triggers across all shutdown paths and is properly awaited.
- **Behavior**: When takeover is enabled, exiting does not trigger a commit. Instead, the handler persists local state, and archiving waits either for the next resumed run to hit the threshold or for a manual `/viking commit` command ([§3.3.3](#_3-3-3-shutdown-method-×-harness-outcome-matrix)). The takeover threshold is set to 30000 tokens while keeping the last 3 turns (keep 3, which the server interprets as a message count). When takeover is disabled, the threshold is 20000 tokens (keep 10), and exiting triggers an unconditional commit. Tool registration requires `health` and `ensureSession` to be established first ([§1.1](#_1-1-active-tool-surface-agentic-calls)). The `viking_add_resource` tool exclusively accepts HTTP URLs (the guard resides on the server). If takeover is off, resuming via `pi -c` will re-report the entire branch.
- **Config**: Behavior toggles are managed in `config.json` + environment variables (credentials always pass through the credential chain, [§3.1.3](#_3-1-3-credential-systems)). Note that `bypassPatterns` uses prefix matching rather than globs.
- **Dimension index**: tool surface [§1.1](#_1-1-active-tool-surface-agentic-calls) | recall [§3.2](#_3-2-automatic-recall-and-injection) | takeover [§3.4.2](#_3-4-2-pi-takeover) | commit [§3.3.2](#_3-3-2-regular-commit-triggers)/[§3.3.3](#_3-3-3-shutdown-method-×-harness-outcome-matrix).

## openclaw

- **Integration docs**: [OpenClaw Plugin](./03-openclaw.md)
- **Form**: Represents the only full ContextEngine takeover (`ownsCompaction:true`). Features 15 native tools (14 enabled by default) + 5 slash commands + 4 hooks + Gateway HTTP routes + feature-gate RPC. It operates entirely remotely. Version 2026.6.18.
- **Highlights**: Retrieval is split into two non-overlapping entry points by default: `memory_recall` searches memory, while `ov_search` targets resources and user skills (though either can cross over if explicit parameters are provided). The `add_skill` tool is enabled by default, and `memory_forget` is whitelisted exclusively for memory ([§3.5](#_3-5-type-boundaries-for-writes-and-deletes)). Three tool-result tools read server-side externalized outputs (safeguarded across sessions). It features complete ContextEngine takeover ([§3.4.3](#_3-4-3-openclaw-contextengine)), and the setup wizard actively probes the key's role while verifying version compatibility.
- **Behavior**: Recall invokes `/find` without a session ID. Consequently, there is no expansion or deduplication ledger, meaning the same memory might be repeatedly injected during a long session. Shutdowns do not trigger a commit; archiving relies on an explicit `/new` or `/reset` command alongside an approximate 50% threshold ([§3.3.3](#_3-3-3-shutdown-method-×-harness-outcome-matrix)). There is no local pending queue, so failed turns are not replayed. The `compact()` function can block for up to 5 minutes. By default, recall issues one additional read per leaf memory (`recallPreferAbstract=false`). Configuration validation is exceptionally strict: any unknown key or invalid value immediately forces the plugin into a setup-only mode.
- **Config**: Configured via `plugins.entries.openviking.config` in `openclaw.json` alongside a few environment variables. Utilizes the `X-API-Key` auth header ([§3.1.3](#_3-1-3-credential-systems)). The commit threshold is controlled by `commitTokenThresholdRatio` (default 0.5), and the recall character budget is 4000.
- **Dimension index**: tool surface [§1.1](#_1-1-active-tool-surface-agentic-calls) | recall [§3.2](#_3-2-automatic-recall-and-injection) | ContextEngine [§3.4.3](#_3-4-3-openclaw-contextengine) | deletion [§3.5](#_3-5-type-boundaries-for-writes-and-deletes) | commit [§3.3.2](#_3-3-2-regular-commit-triggers)/[§3.3.3](#_3-3-3-shutdown-method-×-harness-outcome-matrix).

## hermes (Nous Research)

- **Integration docs**: [Hermes Agent](./05-hermes.md)
- **Form**: Implemented as a MemoryProvider bundled directly with Hermes (a single-file Python implementation of 3725 lines, shipped alongside Hermes). It connects directly via `httpx`, eliminating the need for additional plugin installations. It provides 6 tools and over 10 lifecycle hooks (including `prefetch`, `sync_turn`, `on_session_end`, `on_session_switch`, and `on_memory_write`). The baseline is release `e12626b3` (equivalent to brew 2026.7.7.2).
- **Highlights**: Boasts the most comprehensive resource-ingestion surface: `viking_add_resource` supports HTTP, Git, SSH, `file://`, temporary uploads of local files, and zip-packing local directories for upload (automatically skipping symlinks and out-of-tree files). The `viking_remember` tool writes memory files directly, bypassing session commits or extractions. A local server can be initialized on demand; if the configured local endpoint is unreachable, it is automatically launched via `subprocess.Popen openviking-server`. It supports retries with an injected trusted identity. Commits are guaranteed to complete whether triggered by a normal exit, Ctrl+C, SIGTERM, or SIGHUP ([§3.3.3](#_3-3-3-shutdown-method-×-harness-outcome-matrix)).
- **Behavior**: During recall, only the preferred `search/search` path carries a session ID and routes to path B (`mode="deep"`). Conversely, the `auto` and `fast` modes call `/find` without a session ID ([§3.2.2](#_3-2-2-decision-matrix)). The `queue_prefetch` hook is implemented synchronously without warm-up. Subagents configured with `skip_memory=True` will not interact with OpenViking ([§3.3.5](#_3-3-5-subagent-session-comparison)). This integration lacks profile injection, a status line, and slash commands. Commits strictly follow a "keep 0" policy; if the queue drain does not finish cleanly, the commit is skipped for that round. Additionally, the in-process queue is never written to disk. Session IDs follow the `%Y%m%d_%H%M%S_<hex6>` format. Recall parameters are strictly defined: 6 results, a 0.15 threshold, a 4000-character budget, and a 4-second total timeout. Memory URIs are formatted as `viking://~/peers/{agent}/memories/{subdir}/mem_<uuid12>.md`. The shutdown sequence incorporates a 1.5-second SIGTERM grace period alongside a 30-second exit watchdog. Finally, a complementary path (`openviking-server ingest hermes`) enables offline replays, though this is disabled by default ([§7](#_7-appendix-non-coding-integrations-at-a-glance) E).
- **Config**: Configured through `OPENVIKING_ENDPOINT` (not `_URL`), 8 `OPENVIKING_RECALL_*` environment variables, and `config.yaml`. In `use_ovcli_config` mode, the corresponding variables in `.env` are cleared.
- **Dimension index**: tool surface [§1.1](#_1-1-active-tool-surface-agentic-calls) | recall [§3.2](#_3-2-automatic-recall-and-injection) | commit [§3.3.2](#_3-3-2-regular-commit-triggers)/[§3.3.3](#_3-3-3-shutdown-method-×-harness-outcome-matrix) | deletion [§3.5](#_3-5-type-boundaries-for-writes-and-deletes).
## ov CLI

- **Integration docs**: [Deployment Guide → CLI](../guides/03-deployment.md#cli)
- **Form**: A native Rust binary that wraps the server's REST API into a command-line interface. It operates without host events, automatic recall, or compaction takeover.
- **Highlights**: The only first-party client capable of sending an `auto_commit_policy` (`ov session new --auto-commit-policy-json`, `ov session config set`). It also offers multi-profile management, admin tools, privacy controls, snapshot management, and TUI capabilities unavailable in any plugin (see [§5.3](#_5-3-capabilities-only-the-cli-has)).
- **Dimension index**: See [§5](#_5-ov-cli-command-reference) for the comprehensive command reference.

---

# 5. ov CLI Command Reference

`ov` (internally identified as `openviking` in clap) is a Rust-based HTTP client. Because all core capabilities reside on the server, the CLI's role is strictly to assemble parameters, pack and upload local files, render output, and manage multiple profiles. It is a standalone tool rather than a harness integration—meaning it does not handle host events, automatic recall, or compaction takeover. This chapter details its complete command surface for using OV directly (either manually or via scripts) and highlights features exclusive to the CLI (such as the TUI, multi-profile management, admin tools, `--sudo`, privacy controls, and snapshots).

> Version note: This guide reflects the HEAD source (with the HEAD tag at `cli@0.4.14`); notable differences from older versions, such as 0.4.10, are explicitly mentioned. Please note that `ov doctor` is not included in the native Rust binary. Instead, the Python wrapper installed via pip intercepts `argv[1]=="doctor"` and routes it to `openviking_cli.doctor`, reading the server's `ov.conf` rather than `ovcli.conf`. The pure Rust `ov` binary distributed via npm/cargo does not include this subcommand.

## 5.1 Command tree

Doc-comment prefixes like `[Data]`, `[Interactive]`, `[Admin]`, or `[Experimental]` only influence the help menu rendering and do not affect runtime behavior. For instance, even `[Experimental]` commands are available by default.

**Writing data**: `add-resource` (supports local files/directories/URLs/Git/sitemaps/RSS; pick one target from `--to`/`--parent`/`-p`; manifest mode via `-m`; Connector via `--add-type`; note that specifying a local path with watch>0 triggers an error) | `add-skill` | `write` (`--content` and `--from-file` are mutually exclusive) | `mkdir` | `rm` (aliases: del/delete; executes without a confirmation prompt; use `-r` for explicit recursion) | `mv` (alias: rename) | `set-tags` (hidden at the top level; use `attrs set-tags` instead) | `add-memory` (experimental; executes a sequence of three serial steps: create session → bulk add → commit)

**Reading/retrieval**: `ls` (alias: list) | `tree` (`-L` defaults to 3) | `stat` / `attrs get` | `read` / `abstract` / `overview` (L2/L0/L1 respectively) | `get` (download) | `find` (requires at least one non-empty query or `--image`; `--image` accepts local paths, data URIs, HTTP URLs, and viking:// schemas) | `search` (experimental; adds `--session-id` on top of find capabilities) | `grep` | `glob`

**Skills**: `skills add` (supports local paths, Git, and GitHub tree URLs; use `-s` to select or `*` for all; prompts for interactive confirmation) | `skills list/find/show/update/remove` | `skills validate` (the only fully offline command)

**Sessions/memory**: `session new` (`--auto-commit-policy-json` and `--no-auto-commit` are mutually exclusive—this is the only first-party client that sends a policy) | `session list/get/delete` | `session get-session-context` (`--token-budget` defaults to 128000) | `session get-session-archive` | `session add-message(s)` | `session config set` (modifies mutable configurations) | `session commit`

**Import/export & snapshots**: `export` / `backup` / `import` / `restore` (.ovpack formats) | `snapshot commit/restore/show/log/diff/ignore-*` (workspace snapshots; rollbacks are achieved by committing forward)

**Privacy**: `privacy categories/list/get/versions/version/activate/upsert` (offers `--key-<name>` syntactic sugar)

**Status/observability**: `health` (exits with 0 even when healthy=false) | `status` (table mode always exits with 0) | `observer {queue,vikingdb,models,retrieval,filesystem,system}` | `wait` | `task status/cancel/list` | `task watch {ls,show,rm,pause,resume,update,trigger}` | `version` (probes the server utilizing its own 3-second timeout)

**Config/interactive**: `config` (launches a TUI wizard with a 5-item menu, including User Management) | `config show` (always outputs compact JSON) | `config validate` | `config list/switch/add/edit/delete` (Agent-facing; exit codes 2-6 carry semantic meaning) | `config add ov-service` (cloud) / `config add custom` | `language` (alias: lang) | `tui` (provides a full-screen file browser, in-terminal image previews, a vector view, and deletion confirmations) | `chat` (launches VikingBot with a 300-second timeout) | `compile` (organizes material via a Skill; `--wait` enables local polling)

**Administration (mostly ROOT/`--sudo`)**: `admin create-account/list-accounts/delete-account/set-role/migrate/register-user/list-users/remove-user/regenerate-key` | `system wait/status/health/consistency` | `system crypto init-key` (generates a 32-byte root key entirely locally with 0600 permissions) | `system backend sync-status/sync-retry` | `reindex` (`--mode` defaults to vectors_only; `--wait` defaults to true, making it the only CLI command with this default behavior) | `doctor` (exclusive to the Python wrapper)

## 5.2 Global options and unique mechanisms

- `-o/--output table|json` (defaults to `output` in the config file) | `-c/--compact` (defaults to true) | `--account`/`--user`/`--actor-peer-id` | `--sudo` (utilizes the `root_api_key`; permitted only for admin, system, reindex, task status, and task list commands) | `--profile` (hidden).
- **Multi-profile**: The active profile is stored at `~/.openviking/ovcli.conf`, with alternative profiles named `ovcli.conf.<name>`. The `switch` command performs a direct byte copy (preserving the `plugin` section). Conversely, `add` and `edit` rewrite the file via serde, which drops any keys unrecognized by the Rust Config structure (including the `plugin` section, as detailed in [§3.1.4](#_3-1-4-configuration-layers)).
- **Language gate**: A display language must be configured before executing any commands (if unconfigured in a non-interactive environment, the CLI exits with code 2). As of the HEAD version, `--help` bypasses this gate (unlike version 0.4.10, which lacked this exemption), though `--version` still requires a configured language to run.
- **Three JSON output shapes, categorized by command group**: Under compact mode, standard commands emit `{"ok":true,"result":…}` upon success, a bare payload when `-c false` is passed, and `{"ok":false,"error":…}` upon failure. The configuration command family outputs `{"status":"ok","result":…}`. When a profile is active, `"profile":[…]` is appended to the response. These variations require careful handling when parsing outputs in scripts. Additionally, `echo_command` defaults to true and is not suppressed by `-o json` (meaning the first line of stdout will typically be `cmd: …`).
- **Environment variables**: `OPENVIKING_CLI_CONFIG_FILE`, `OPENVIKING_UPLOAD_MODE` (local/shared), `OPENVIKING_ASSETS_CREDENTIALS_FILE`, `OPENVIKING_LANG`/`LC_*`/`LANG`, along with `VIKINGBOT_ENDPOINT`/`VIKINGBOT_API_KEY`/`OPENVIKING_URL` for chat functionality.

## 5.3 Capabilities only the CLI has

The following operations are inaccessible via the plugin surface and remain exclusive to the CLI/TUI: multi-profile switching and the configuration wizard, the complete `admin` account and user management suite, root operations via `--sudo`, `privacy` policy CRUD and versioning, data movement commands (`snapshot`/`backup`/`restore`/`export`/`import`), index rebuilds via `reindex`, root key generation via `system crypto init-key`, interactive browsing with `ov tui`, VikingBot-dependent features like `ov chat` and `ov compile`, and `ov session config set` for explicitly defining the `auto_commit_policy` (this serves as the only first-party entry point capable of enabling server-side auto-commits).

---

# 6. Custom agent integration guide

If your preferred agent or harness is not among the 11 listed previously, you can integrate it using one of three methods, arranged below from lowest to highest implementation effort.

## 6.1 Integration path × capabilities you get

| Path | Effort | Agent-initiated tool surface | Auto recall/capture hooks | Session/commit | Compaction takeover |
|---|---|---|---|---|---|
| ① [Direct MCP connection](./06-mcp-clients.md) | Minutes (fill in one config block) | ✅ All 15 tools | ❌ The model calls them itself | Only `remember` creates a temporary session | ❌ |
| ② HTTP API / SDK / [LangChain](./07-langchain-langgraph.md) | Hours (requires code) | Flexible (call REST as needed) | Custom implementation | Custom implementation (or use the LangChain middleware) | ❌ |
| ③ Reuse shared-core / the [Agent Plugins portable package](./15-agent-plugins.md) | Days (requires hook adapters) | ✅ 15 tools (through the MCP proxy) | ✅ Full recall/capture/commit/pending set | ✅ | Depends on which events you wire up |

## 6.2 Path ①: Direct MCP connection (recommended starting point)

Any MCP-capable agent simply needs to point its `mcpServers` configuration to the server's `/mcp` endpoint (refer to [MCP Clients](./06-mcp-clients.md) to locate this configuration for each client). Doing so instantly unlocks all 15 tools ([§2.1](#_2-1-server-side-mcp-tool-surface)). The minimal configuration looks like this:

```json
{
  "mcpServers": {
    "openviking": {
      "url": "http://127.0.0.1:1933/mcp",
      "headers": {
        "Authorization": "Bearer <api_key>",
        "X-OpenViking-Account": "<account>",
        "X-OpenViking-User": "<user>",
        "X-OpenViking-Actor-Peer": "<workspace-peer>"
      }
    }
  }
}
```

The last three headers are optional; however, omitting them disables workspace peer isolation and tenant routing. For `stdio`-only clients, you can utilize the portable proxy (Path ③) to bridge `stdio` to streamable HTTP. This path provides a pure tool surface—meaning it does not include automatic recall, capture, or commits (unless the model explicitly invokes the `remember` tool).

## 6.3 Path ②: Programmatic integration

- **Direct REST**: Trigger recall via `POST /api/v1/search/search` (note that expansion and deduplication require `mode:"context"` alongside a `session_id`, as per [§3.2.1](#_3-2-1-mechanism-foundation-one-shared-pipeline-two-server-side-paths); pass `rewrite` to generate a server-side digest, see [§3.2.5](#_3-2-5-recall-digest)). Write data using `POST /api/v1/sessions/{id}/messages/batch` (supports up to 100 messages per batch with `auto_create`). Finalize sessions via `POST /api/v1/sessions/{id}/commit`, and retrieve content using `GET /api/v1/content/read` and related endpoints. To enable server-side auto-commits, explicitly pass the `auto_commit_policy` during `POST /api/v1/sessions`, or modify it later via `PATCH /{id}/config` ([§2.3](#_2-3-server-side-session-and-commit-semantics)).
- **LangChain / LangGraph SDK** (`pip install langchain-openviking`): The `OpenVikingContextMiddleware` provides `wrap_model_call` (which injects recalled content into `<openviking_context>`) and `after_agent` (handling capture and commit actions according to the `CommitPolicy`, which defaults to `never`). Within this group, this is the only out-of-the-box automatic recall solution that includes both session management and a token budget. Its fault-tolerance strategy is to retry read-only methods once and never retry writes. Partial successes raise an `OpenVikingPartialWriteError`, allowing you to retry a specific slice based on `input_messages_consumed`. See [§7](#_7-appendix-non-coding-integrations-at-a-glance) B for more details.
- **Open WebUI** (OpenAPI tool server): Running `python -m openviking_openwebui` launches a standalone process. By adding the resulting Tool Server URL to Open WebUI, you gain access to 7 tools (note that deletion and hooks are unsupported). See [§7](#_7-appendix-non-coding-integrations-at-a-glance) A for more details.
## 6.4 Path ③: Reuse a Reference Implementation for Automatic Hooks

If you need the full spectrum of automation—recall, capture, commit, and pending state management—there is no need to build it from scratch. Consider studying and reusing one of these two existing implementations:

- **`examples/memory-plugin-shared/lib/`** (Node): This provides a complete set of core modules. These include `recall-core` (three-tier degradation recall), `profile-inject`, `capture-utils` (message normalization and injection-echo protection), `pending-queue` (offline replay), `batch-send`, `mcp-proxy-core` (stdio↔HTTP proxy), `session-model` (session ID derivation), and `credentials`. To build a lightweight integration harness, you only need to implement an adapter layer that maps host lifecycle events to these modules. For example, `agent-hook-runtime.mjs` is a ready-made, all-in-one runtime shared by Cursor, Trae, and zcode. Wiring up a new host is usually just a matter of parsing its stdin JSON field names.
- **The Agent Plugins 1.0 Portable Package** (under `agent-plugins/`): This offers a standardized portable format comprising `plugin.json`, the `skills/` directory, and `mcp.json` (stdio→HTTP proxy). It intentionally excludes automatic hooks—instead, recall and persistence rely on a skill that teaches the model to invoke the tools autonomously. This design makes it highly suitable for clients that follow the Agent Plugins specification for direct loading. Additionally, `plugin.test.mjs` defines spec-conformance checks (such as schema URL validation, naming rules, ensuring no secrets in static headers, and preventing `mcp.json` references from escaping the plugin root), which you can use as a linting baseline when packaging your own plugin.

**Three conventions you must follow** (to ensure behavior remains consistent with existing harnesses): ① The recall call site must forward the `session_id`, which enables server-side expansion and cross-turn deduplication (see [§3.2.1](#_3-2-1-mechanism-foundation-one-shared-pipeline-two-server-side-paths)); ② Do not allow the adapter's own timeout to override the deadline dictated by the helper; ③ You must arrange a commit path at shutdown. Otherwise, any remaining conversation tail that falls below the threshold will remain unarchived until a subsequent trigger occurs (see [§3.3.3](#_3-3-3-shutdown-method-×-harness-outcome-matrix)). If the host does not provide a shutdown event, rely on the server-side idle fallback (enable `memory.session_auto_commit.idle_enabled` on the server and pass down a per-session policy). These three rules are exactly what `recall-session-wiring.test.mjs` enforces using cross-plugin regexes.

---

# 7. Appendix: Non-Coding Integrations at a Glance

| Integration | Form | Tool Surface | Session/Commit | Fault Tolerance | Default State |
|---|---|---|---|---|---|
| **A. [Open WebUI](./08-community-plugins.md)** | Standalone FastAPI OpenAPI tool server | 7 local OpenAPI routes (`ov_search`/`ov_recall_memories`/`ov_add_memory`/`ov_list_memories`/`ov_read_resource`/`ov_add_resource`/`ov_session_status`), no deletion tool | No session concept | Most lightweight: bare `httpx`, no retries or negative caching; `/health` only echoes the config and does not probe OpenViking. | Inactive until the process is explicitly started. |
| **B. [LangChain/LangGraph](./07-langchain-langgraph.md)** | Python SDK adapter layer (retriever/tools/store/middleware/recorder) | `create_openviking_tools()` provides 12 StructuredTools (`viking_forget` is not in the agent profile by default) | `thread_id`/`session_id` come from the caller; `CommitPolicy` defaults to `never` | Most robust in this group: read-only methods automatically retry once, writes never retry (to prevent duplicates), and partial successes raise a structured exception that can be retried as a slice. | Requires explicit construction before taking effect. |
| **C. [Agent Plugins 1.0](./15-agent-plugins.md)** | Portable package: `plugin.json` + `skills/` + `mcp.json` (stdio→HTTP proxy) | MCP passthrough for all 15 tools; intentionally excludes hooks (recall relies on a skill instructing the model). | Only `remember` creates a temporary session | Retries handled at the MCP proxy layer (401/403 triggers credential swap, 400/404 triggers re-initialization; max 1 retry each). | Active as soon as loaded by the client. |
| **D. [Direct MCP Connection](./06-mcp-clients.md)** | No local components, connects straight to `/mcp` | Same as C (15 tools) | Same as C | Depends entirely on the client. | `/mcp` is always on. |
| **E. [Log Ingestion](./09-log-ingestion.md)** | `openviking-server ingest` CLI (runs on the machine hosting the logs, importing them in reverse) | None (write-only, no recall) | Session ID `{prefix}__{harness}__{sanitized native id}`; commit token 6000 / idle 5s / keep 0 | The only integration featuring crash recovery: utilizes a SQLite cursor store, single-instance locking, and a reconciliation process to verify batch delivery. | Disabled by default on two levels (`ingest.enabled` and each harness's individual `enabled` flag are both false); adapters available for claude_code, codex, hermes, opencode, openclaw, and cursor. |
| **F. [OpenViking Helper](./14-openviking-helper.md)** | Closed-source desktop app | — | — | — | Outside the scope of this codebase. |

---

*Installation, configuration, and troubleshooting for each integration are governed by their respective integration pages. In the event of a discrepancy between this summary and the individual page, the individual page takes precedence.*

## See also

- [Agent Integrations Overview](./01-overview.md)
- [MCP Clients](./06-mcp-clients.md)
- [MCP Integration Guide](../guides/06-mcp-integration.md)
- [Retrieval API](../api/06-retrieval.md)
- [Sessions API](../api/05-sessions.md)
- [Authentication](../guides/04-authentication.md)
