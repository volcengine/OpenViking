# Hermes memory provider: design record

This document records what the Hermes host guarantees to a memory provider, what
this plugin does with each guarantee today, which settings it shares with the
other OpenViking plugins, and which commit-lifecycle behaviours the tests pin.
It is the host contract record required by
[the plugin development spec](../../docs/zh/agent-integrations/18-plugin-development.md#2-接入前先确定宿主契约).

**Baseline.** Plugin 2.0.1 (`plugin.yaml`, `pyproject.toml`) at OpenViking
`b20f192e6`. Host facts are read from Hermes Agent `58d146e961`, the commit the
plugin CI pins (`.github/workflows/hermes-plugin-tests.yml`). Everything below
was first written for the code after the phase 2b module move (OpenViking
`d17f2f70a`), which changed no behaviour, and was brought up to date for plugin
3.0.0, after phases 3-6 (connection snapshot and headers, recall routing, the
upload backlog, MCP tools and the session id prefix). Items marked **Planned**
come from the approved refactor plan and are not implemented yet; section 6
lists them.

**Citations.** `hermes:<path>:<line>` is a path in the Hermes checkout.
`H:<line>` is this directory's `__init__.py`, `S:<line>` is `_setup.py`, and
`core/<module>.py:<line>` is a module of the `core/` subpackage; `:<line>` after
a path is another line of the same file. Test paths are relative to this
directory. Plugin and test citations were first checked at `b20f192e6` and
remapped to `d17f2f70a` after phase 2b moved the code out of `__init__.py` and
`native_memory_mirror.py`; `DEVELOPMENT.md` ("Module map") lists where each
function went. Rows changed for 3.0.0 cite the 3.0.0 code; line numbers in
the other rows can be a few lines off, and the function names next to them
still hold.

**Status.**

- **verified**: the host behaviour was confirmed in the pinned source, and the
  plugin meets it.
- **degraded**: the plugin works, but deviates from the host contract or loses
  something under a stated condition.
- **unsupported**: the plugin does not implement the hook, or the feature does
  not work in that setting.

## 1. Host contract record

### 1.1 Provider hooks

| Hook | Host guarantee | Plugin today | Status |
| --- | --- | --- | --- |
| `is_available` | Called at agent init before `add_provider`; falsy skips the provider and the host shows `unavailable_reason()` (hermes:agent/agent_init.py:1365-1372). Docstring: config and deps only, no network (hermes:agent/memory_provider.py:99-100). The setup picker and `hermes memory status` call it on every discoverable provider (hermes:hermes_cli/memory_setup.py:122-140, :395). | True when an endpoint comes from the scoped env, `memory.openviking.endpoint`, or a linked ovcli profile; no network (H:316-326). `unavailable_reason()` reads the same local config and names the cause: no endpoint configured, or a linked `ovcli.conf` that is missing, unreadable, invalid or has no `url`; it returns `""` when the provider is available (phase 1). | verified |
| `initialize` | Called once, synchronously, during `AIAgent` construction, after `add_provider` (hermes:agent/agent_init.py:1373-1374, hermes:agent/memory_manager.py:896-902). Kwargs come from hermes:agent/agent_init.py:1268-1303; `agent_context` is `cron` or `subagent` only when the platform has that name (:1276). Subagents are built with `skip_memory=True` (hermes:tools/delegate_tool.py:241), so in practice the only non-primary context is cron (hermes:cron/scheduler.py:2437-2439). The api_server reuses an initialized manager across per-turn agent rebuilds without calling `initialize` again (hermes:agent/agent_init.py:1347-1351). | Resolves the profile-bound connection, takes the run lock, probes health (anonymous probes use a 3 s timeout, core/http.py:196-198), may start a local `openviking-server` and a waiter thread (core/connection.py:240-263), starts pending-session recovery threads, and adds a primary provider to the weak atexit registry, registering the atexit hook on the first primary `initialize`; a non-primary provider is removed from it. At exit the hook commits each registered provider's current session within one 20 s budget, below the CLI's 30 s exit watchdog (hermes:hermes_cli/cli_shutdown.py:51-99), caps the wait for the commit lock and each request at the time left, and starts no commit once the budget is spent (phase 1). Writes are disabled for `cron`, `subagent` and `flush` (core/session_writer.py:31, H:432-433). | verified |
| `get_tool_schemas` call order | Called first in `add_provider`, before `initialize`; that call builds the routing table and core tool names are refused (hermes:agent/memory_manager.py:379-428, schemas read at :394). Called again for injection after `initialize_all` (hermes:agent/agent_init.py:1384, hermes:agent/memory_manager.py:115-158, :614-635), only when the `memory` toolset is exposed (:103-112). | Returns the server's MCP tools as `openviking_<server name>` (H:692-693, core/tools.py:236-250). Sources in order: an in-process cache keyed by the connection fingerprint, `$HERMES_HOME/openviking/tools_cache.json` for the same endpoint, a live `tools/list` under a 3 s budget, otherwise an empty list and one warning (core/tool_catalog.py:131-158). Before `initialize` the connection is resolved like `is_available()`. The default set is 11 tools; `extra_tools` adds `write`, `edit`, `add_skill`, `list_watches`, `cancel_watch`. The first answer is remembered and later answers are filtered to it, so the second call returns a subset (core/tools.py:245-250). `initialize` refreshes both caches on a one-shot thread (H:451, core/tools.py:252-274), and setup primes the disk cache (S:467-469). | verified |
| `system_prompt_block` | Joined by `build_system_prompt` (hermes:agent/memory_manager.py:437-441) from hermes:agent/system_prompt.py:528-539, under the same gate as tool injection. The result is cached with the system prompt; the docstring requires static text (hermes:agent/memory_provider.py:116-118). | Static text built from the names `get_tool_schemas()` returns: a header, a note that `viking://` URIs are virtual, and one guidance line per registered tool. No request, and the same text for an empty store, a populated store and an unreachable server. Returns `""` only when no tool is registered (phase 1). | verified |
| `prefetch` | Runs on the turn thread's behalf in a fresh `spawn_context_thread`; the turn joins it for at most 8 s (hermes:agent/memory_manager.py:32, :457-501). After a timeout the result is dropped and later turns skip the provider until the stuck call returns (:471-486). Skipped for trivial prompts (hermes:agent/turn_context.py:876). The result is stamped into the user message's `api_content` and replayed on every later request (hermes:agent/turn_context.py:888-904). | One deadline per call: `min(7.5 s, recall_timeout_seconds)` from the start of `prefetch`, covering the connection check. The session-start block and query recall then run concurrently on one-shot `spawn_context_thread` threads, and each request is clamped to the deadline. A part that misses it is dropped from the turn's result (outcome `timeout`) and the other part is still returned. A request with a later fallback (the context rewrite request, the session-aware list search) keeps 1 s of the budget for it and is skipped when only that is left. With `recall_compress` set to `server` or `auto` and no explicit timeout, both timeouts default to the 7.5 s budget instead of 55 s. The session-start block is claimed per sid under `_session_start_lock` and latched only when it reached a turn's result, so concurrent prefetches of one sid inject it once (phase 1). | verified |
| `queue_prefetch` | Queued on the mem-sync worker after a completed, non-interrupted, non-trivial turn (hermes:run_agent.py:946-947, hermes:agent/memory_manager.py:516-525). | No-op; recall is current-query only (H:531-533). | verified |
| `recall_status` | Read by `describe_recall` right after a non-empty `prefetch_all`, and shown as a status line (hermes:agent/turn_context.py:880-884, hermes:agent/memory_manager.py:503-514). | Each `prefetch` records its outcome per session id under a lock: `pending` while running, then `injected`, `empty`, `timeout`, `unavailable` or `error`. A sequence number stops a call the host already abandoned from overwriting a newer one. `recall_status()` reads the most recently started prefetch and returns `RecallStatus("OpenViking", count)` only when it injected, with `count` the number of query-recall entries (0 when only the session-start block or an entry-less digest was injected); otherwise `None`. `last_recall_outcome(session_id)` exposes the category for diagnostics (phase 1). | verified |
| `sync_turn` | Submitted to one `DaemonThreadPoolExecutor(max_workers=1, thread_name_prefix="mem-sync")` in FIFO order, bound to the caller's contextvars (hermes:agent/memory_manager.py:533-596, `ctx_bound` at :563). `messages` and `turn_author` are passed only if the signature accepts them (:528-531, :548-553). Interrupted turns are never synced (hermes:run_agent.py:918-949). If the executor cannot be created the task runs inline (hermes:agent/memory_manager.py:592-596). Late submissions during shutdown are rejected (:579-582). | Returns immediately. It captures the commit scope, a client clone and the sender peer under `_client_refresh_lock` (H:538-545), then starts one writer thread per turn (H:591-592). Writers serialize on the instance `_writer_commit_lock` (H:572), which does not guarantee turn order. Messages still unsent after the retry and the per-message fallback go to an in-memory backlog keyed by `(sid, connection generation)`, bounded at 2000 messages and 8 MiB; it is sent in order before that sid's next upload or commit, and a turn queues behind an existing backlog (core/session_writer.py:256-344, tests/test_writer_backlog.py). The backlog does not survive the process. **Planned** (D6): upload synchronously on the worker (section 6). | degraded |
| `on_turn_start` | Synchronous on the turn thread before `prefetch`; exceptions suppressed (hermes:agent/turn_context.py:866-872). Only `author_id`, `author_name` and `author_is_bot` are sent, filtered by signature (hermes:agent/memory_manager.py:654-661). | Sets the per-instance `ContextVar` `_turn_peer` (H:257, H:473-474). The prefetch thread and the queued `sync_turn` inherit the value through copied contextvars. | verified |
| `identity_signature` | The gateway calls it on every inbound message, on a memoized instance that was never initialized, and swallows errors (hermes:gateway/run_agent_cache.py:76-95). The values join the agent cache key (:70-71). | Not implemented. The plugin re-resolves its connection on each access instead (core/connection.py:265-341), so a changed credential takes effect on the next call, but it does not rebuild the cached agent. **Planned** (optional): read local config only, cache by file stat. | unsupported |
| `on_session_end` | Several callers on different threads; see 1.3. Compression calls it whether or not the session id rotates, so one conversation can see several calls. | Drains its own writer threads for the current sid for up to 10 s and skips the commit if they are still alive (core/session_writer.py:24, :334-336). Then commits under `_writer_commit_lock` if needed (H:609-612, core/session_writer.py:324-364). Skipped entirely in non-primary contexts. | degraded |
| `on_session_switch` | Called on three kinds of thread; see 1.4. | Rotates `_session_id` synchronously under `_session_state_lock` in every context, including read-only ones (H:632-648). The old sid is committed on a one-off finalizer thread (core/session_writer.py:298-322, H:664-665). `rewound=True` or an unchanged id skips rotation (H:643). Compression resets turn accounting, re-arms the latch on an in-place sid, and re-injects the session-start block (H:649-659). | verified |
| `on_pre_compress` | Called synchronously before compaction (hermes:agent/conversation_compression.py:2950-2987, :3902); v1 providers get raw messages (hermes:agent/memory_manager.py:727-762). With `compression.checkpoint_required` enabled, a provider without checkpoint API v2 makes compaction fail closed (hermes:agent/conversation_compression.py:2958-2980). | Not implemented. Compaction already calls `on_session_end`, which commits the session. Users who enable `compression.checkpoint_required` cannot compact with this provider. | unsupported |
| `on_delegation` | Parent side, synchronous, after `delegate_task` results (hermes:tools/delegate_tool_results.py:330-343). | Not implemented. The delegated task and its result reach the parent transcript as a tool call and result, which `sync_turn` captures. | unsupported |
| `on_memory_write` | Synchronous on the tool thread after a committed built-in `memory` write (hermes:agent/inline_tool_executors.py:151-158 → hermes:agent/memory_manager.py:804-840 → :773-788). Metadata is passed by keyword to this signature (:765-771). | Clones the client, then enqueues to a FIFO mirror worker and returns (H:670-701, core/mirror.py:358-387, :82-125). The worker is started through `spawn_context_thread` (core/mirror.py:120), exits after one idle 50 ms poll or after `shutdown()`, and the next enqueue starts a new one; the exit is decided under the enqueue lock, so no write is lost or reordered across a restart (phase 1). | verified |
| `handle_tool_call` | Dispatched sequentially (hermes:agent/tool_executor.py:1660-1666). Routing uses the table from the first `get_tool_schemas` call; exceptions become `tool_error` (hermes:agent/memory_manager.py:643-652). | Calls `_ensure_client()`, then `_call_openviking_tool` (H:695-701). A name outside the exposed set is refused; `search`, `forget` and `add_resource` go through a local `_prepare_<name>` step; the call itself is one short MCP session on a one-shot `spawn_context_thread` thread with a 15 s budget, and `isError` or a transport failure becomes `tool_error` with the HTTP status (core/tools.py:277-362, core/mcp_bridge.py:480-582). Read-only tools are retried once after a transport failure; write tools are not replayed. | verified |
| `shutdown` | `shutdown_all` drains the worker for at most 5 s, cancels what is left, then calls `shutdown()` in reverse order (hermes:agent/memory_manager.py:31, :848-894). Reached from `shutdown_memory_provider` (hermes:run_agent.py:897-909) and api_server eviction (hermes:gateway/platforms/api_server_memory_sessions.py:113-129). Not called on gateway soft eviction. | Sets `_shutting_down`, drains the mirror for 5 s, joins writer, finalizer and autostart threads for up to 5 s each, removes the provider from the atexit registry and releases the run lock (H:703-722). It does not commit, and an unsent backlog is dropped; data uploaded after the last commit stays pending until the next process recovers it. **Planned**: commit within a 5 s total budget and keep the atexit entry while data remains (section 6). | degraded |
| `backup_paths` | Called by `hermes backup` on a provider that was loaded but not initialized; must not need `initialize()` or the network (hermes:hermes_cli/backup.py:239-252, hermes:agent/memory_provider.py:203-206). | Returns the resolved `ovcli.conf` path (H:240-246). It reads `OPENVIKING_CLI_CONFIG_FILE` from `os.environ` (core/ovcli.py:37-39), and returns the path even when the profile does not link ovcli. | verified |

### 1.2 Host behaviours

| Behaviour | Host guarantee | Plugin today | Status |
| --- | --- | --- | --- |
| Gateway soft eviction | The eviction thread re-enters the owning profile's scope (hermes:gateway/run_agent_cache.py:773-800), calls `commit_memory_session` (→ `on_session_end`), then `release_clients()`, which keeps the memory provider on the agent; `shutdown()` is not called (:802-834, hermes:run_agent.py:951-955). The next turn builds a new agent and a new provider instance (hermes:plugins/memory/__init__.py:192-217). | `on_session_end` commits, and removes the instance from the atexit registry when no writer is running and no turn or pending marker of the current connection generation is left uncommitted; a failed commit keeps it registered, and a later write registers it again. The registry is a `weakref.WeakSet`, so it never keeps an evicted instance alive (phase 1, tests/test_standalone.py `test_soft_eviction_cycles_do_not_grow_exit_registry`). The mirror worker exits after one idle poll, so no plugin thread keeps the evicted instance alive (phase 1, tests/test_native_memory_mirror.py `test_soft_eviction_leaves_no_mirror_thread_and_later_writes_stay_in_order`). By code reading, markers of a still-locked run are not recovered by another instance until the lock is released (core/state_store.py:92-112); no test covers this. | degraded |
| Plugin loader pre-executes sibling modules | The loader registers the package, executes every top-level `*.py` sibling in glob order, then `__init__.py`. A sibling that raises is dropped with a debug log; subdirectories are not pre-executed; a cached module is reused, so module state is process-wide (hermes:plugins/plugin_loader.py:93-135). A user install is named `_hermes_user_memory.openviking__source_<sha16>` (hermes:plugins/memory/__init__.py:81-87). `_setup.py` is the only top-level sibling, and it has no import-time side effects. The rest of the code lives in the `core/` subpackage, which the loader does not pre-execute; `__init__` imports it (phase 2b). `_setup` reaches the package through `sys.modules[__package__]` only at call time (S:22-24), and `__init__` imports it mid-module (H:219). No core module imports `__init__` or `_setup`, and no plugin module registers an `atexit` hook, starts a thread or touches the network at import (tests/test_structure.py, tests/test_core_boundaries.py). Profile discovery and network validation reach `_setup` through the provider's `Deps`, passed to `run_setup` (phase 2a). `__init__` registers no `atexit` hook at import; the hook is registered on the first primary `initialize` (phase 1). The other core modules log through `core/log.py`, named after the plugin package, but the mirror logger name stays hard-coded to `plugins.memory.openviking` (core/mirror.py:33), and the MCP bridge logs as `plugins.memory.openviking.mcp` (core/mcp_bridge.py:35); neither matches a user install's module name. **Planned**: both get their logger from `core/log.py`. | degraded |
| Bundled provider wins on a name collision | Discovery order is bundled, user, project, entry point; the first name seen wins (hermes:plugins/memory/__init__.py:1-8, :95-109, :124-136). `plugins/memory/openviking/` exists at `58d146e961`. | While the bundled copy exists, `$HERMES_HOME/plugins/openviking` is never loaded (README "Install"). The tests point `_MEMORY_PLUGINS_DIR` at an empty directory to load the external copy (tests/conftest.py:25). **Planned** (phase C): catalog cutover. | unsupported |
| `spawn_context_thread` | Returns an unstarted thread that runs under the spawner's contextvars (hermes:agent/memory_provider.py:21-33). Every background job must use it, or it runs without a profile scope (hermes:plugins/AGENTS.md:75-84). | Every plugin thread uses it: writers, finalizers and recovery (core/session_writer.py:195-218), the prefetch parts (core/recall.py:145-146), the autostart waiter, the mirror worker (core/mirror.py:120-124), the tool catalogue refresh (core/tools.py:271), and each MCP session (core/mcp_bridge.py:343, with `spawn=spawn_context_thread` from core/tools.py:147). There is no bare `threading.Thread`. | verified |

### 1.3 `on_session_end` callers

| Caller | Host site | Thread | Worker drained first? |
| --- | --- | --- | --- |
| CLI exit | hermes:hermes_cli/cli_shutdown.py:111-124 → hermes:run_agent.py:897-909 | main thread | yes, `flush_pending(timeout=10)` |
| Gateway teardown | hermes:gateway/run_shutdown.py:1287-1302 | teardown thread | yes, `flush_pending(timeout=10)` |
| One-shot `hermes -q` | hermes:hermes_cli/oneshot.py:628-638 | main thread | no; `on_session_end` runs before `shutdown_all` drains the queued `sync_turn` |
| Compression | hermes:agent/conversation_compression.py:3710 via `commit_memory_session` (hermes:run_agent.py:911-916) | turn thread | no |
| Gateway soft eviction | hermes:gateway/run_agent_cache.py:802-815 | eviction thread | no |
| TUI session lifecycle | hermes:tui_gateway/session_lifecycle.py:394 | TUI thread | no |
| CLI `/new` with history | hermes:agent/memory_manager.py:667-698 | mem-sync worker, same task as the switch | FIFO: runs after queued `sync_turn` tasks |
| api_server eviction | hermes:gateway/platforms/api_server_memory_sessions.py:113-129 | eviction thread | not called; only `flush_pending` and `shutdown_all` |

Consequences today, by code reading: under `hermes -q` the last turn's
`sync_turn` can still be queued when `on_session_end` commits. It is then
uploaded during the `shutdown_all` drain, `shutdown()` does not commit it, and it
stays pending until a later process with the same connection recovers it. On the compression
path `on_session_end` can hold the turn thread for the 10 s writer drain plus a
commit request with the 30 s default timeout.

### 1.4 `on_session_switch` thread kinds

| Trigger | Host site | Thread | Arguments | Plugin result |
| --- | --- | --- | --- | --- |
| `/new` with history | hermes:hermes_cli/cli_session_mixin.py:576-579 → hermes:agent/memory_manager.py:667-698 | mem-sync worker, after `on_session_end` in the same task | `reset=True`, `reason="new_session"` | `on_session_end` commits the old sid; the finalizer then finds it latched |
| `/new` without history | hermes:hermes_cli/cli_session_mixin.py:580-583 | command thread | `reset=True` | rotate; finalizer commits the old sid if it has data |
| `/resume`, `/branch` | hermes:hermes_cli/cli_commands_mixin.py:349-351 | command thread | `reset=False`, `reason` | same as above |
| CLI `/undo` | hermes:hermes_cli/cli_session_mixin.py:811-813 | command thread | same id, `rewound=True` | no rotation |
| TUI `/undo` | hermes:tui_gateway/methods_tools.py:899-901 | TUI method thread | passes `session_key` as the id, `rewound=True` | no rotation, so the unusual id is ignored |
| Compression boundary | hermes:agent/conversation_compression.py:3469-3474, after the commit at :3710 | turn thread | `reset=False`, `reason="compression"`; same id when compacting in place | rotation mode: rotate, old sid already latched. In-place mode: turn count reset and latch re-armed (H:649-659) |
| Compression child adoption | hermes:agent/conversation_compression.py:1719-1723 | turn thread | `reason="compression"` | rotate |

## 2. Knob mapping (plan decision D7)

Hermes does not use the shared knob schema in
`../memory-plugin-shared/lib/config-schema.mjs`. It declares its own
`_CONFIG_SCHEMA` (core/settings.py:28-56), which the host renders in `hermes memory setup` and
the dashboard. Typed settings resolve as scoped env var first, then
`memory.openviking.<key>` in `config.yaml`, then the default, clamped to the
range (core/settings.py:114-137). Connection values resolve as scoped env, linked ovcli
profile, `config.yaml`, default; the API key never comes from `config.yaml`
(core/connection.py:105-133). The shared resolver orders layers as default, files, env
(config-schema.mjs:344). Bare line numbers in the shared-knob column refer to
`config-schema.mjs`.

| Hermes setting and env var | Hermes default, range | Shared knob | Shared default, range, env | Difference |
| --- | --- | --- | --- | --- |
| `endpoint`, `OPENVIKING_ENDPOINT` | `http://127.0.0.1:1933` | none (resolved by `credentials.mjs`) | — | Same meaning. |
| `api_key`, `OPENVIKING_API_KEY` (secret; `.env` or ovcli) | empty | `apiKey` (:45) | empty, no env | Same meaning. |
| `account`, `user`; `OPENVIKING_ACCOUNT`, `OPENVIKING_USER` | `default` | `accountId`, `userId` (:46-47) | empty | Hermes sends them only without an API key, or on a trusted-mode retry (core/http.py:109-144). |
| `agent`, `OPENVIKING_AGENT` | empty | `peerId` (:66) | empty, `OPENVIKING_PEER_ID` | Same header (`X-OpenViking-Actor-Peer`), different env var. |
| `use_ovcli_config`, `ovcli_config_path`, `OPENVIKING_CLI_CONFIG_FILE` | off | none | — | Shared plugins always read `ovcli.conf`; Hermes reads it only when linked, and only its connection fields (core/ovcli.py:52-65, :130-135). |
| `recall_scope`, `OPENVIKING_RECALL_SCOPE` | unset; `shared` \| `peer` | `recallPeerScope` (:85) | `all`; `all` \| `actor` | Unset keeps the pre-preset request shape. `shared` and `peer` with a sender use context mode with `peer_scope` `all` / `actor`; `peer` without a sender uses list search with explicit roots (core/recall.py:225-275). |
| `recall_compress`, `OPENVIKING_RECALL_COMPRESS` | `off`; `off` \| `server` \| `auto` (booleans accepted, core/settings.py:91-96) | `recallCompress` (:111) | `off` (`auto` for Claude Code and Codex), same env | Hermes has no client compressor; `auto` sends `rewrite: "auto"`. Turning it on without explicit timeouts lets both recall timeouts use the whole 7.5 s prefetch budget; a timed-out rewrite request falls back to search without rewrite in the 1 s it left (phase 1). |
| `commit_token_threshold`, `OPENVIKING_COMMIT_TOKEN_THRESHOLD` | 20000; 1000-1000000 | `commitTokenThreshold` (:150) | 20000; 1000-1000000, same env | Identical. |
| `recall_limit`, `OPENVIKING_RECALL_LIMIT` | 6; 1-100 | `recallLimit` (:79) | 10; 1-50, same env | Same env var, different default and range. On the context-mode routes it is split into per-type `quotas` with the shared weights (core/recall.py:34-54). |
| `recall_score_threshold`, `OPENVIKING_RECALL_SCORE_THRESHOLD` | 0.15; 0-1 | `scoreThreshold` (:80) | 0.35; 0-1, `OPENVIKING_SCORE_THRESHOLD` | Different default and env var. |
| `recall_max_injected_chars`, `OPENVIKING_RECALL_MAX_INJECTED_CHARS` | 4000 chars; 100-50000 | `recallMaxTokens` (:87), `recallTokenBudget` (:82) | 1600 / 2000 tokens | Hermes counts characters and sends `max_tokens = chars // 4`, clamped to 64-32000 (core/recall.py:264). |
| `profile_token_budget`, `OPENVIKING_PROFILE_TOKEN_BUDGET` | 6000; 500-50000 | `profileTokenBudget` (:158) | 10000; 500-50000, same env | Same env var, different default. |
| `recall_timeout_seconds`, `OPENVIKING_RECALL_TIMEOUT_SECONDS` | 4.0 s; 0.25-60 | `recallTimeoutMs` (:89) | 120000 ms; 1000-600000 | Seconds vs milliseconds. Hermes caps it at 7.5 s, below the host's 8 s prefetch join (phase 1). |
| `recall_request_timeout_seconds`, `OPENVIKING_RECALL_REQUEST_TIMEOUT_SECONDS` | 3.0 s; 0.25-60 | `timeoutMs` (:50), `recallContextTimeoutMs` (:91) | 15000 ms / 0 | No one-to-one match. |
| `recall_full_read_limit`, `OPENVIKING_RECALL_FULL_READ_LIMIT` | 2; 0-100 | none | — | List-mode recall only; no effect on the context-mode routes. |
| `recall_prefer_abstract`, `OPENVIKING_RECALL_PREFER_ABSTRACT` | false | `recallPreferAbstract` (:84) | true, same env | Same env var, opposite default. List-mode recall only. |
| `recall_resources`, `OPENVIKING_RECALL_RESOURCES` | false | none | — | Controls `context_type` and resource roots. |
| `recall_context_mode`, `OPENVIKING_RECALL_CONTEXT_MODE` | true | none | — | Hermes only. `false` sends the `shared` and sender-scoped `peer` routes back to list search; a transition switch to be removed once context mode is stable. |
| `extra_tools`, `OPENVIKING_EXTRA_TOOLS` | empty; any of `write`, `edit`, `add_skill`, `list_watches`, `cancel_watch` | none | — | Hermes only. The shared plugins expose every server tool; Hermes exposes 11 by default because `write` and `edit` bypass the native memory mirror's registry. |

Fixed in Hermes code, configurable in the shared schema:

| Hermes behaviour | Location | Shared knob |
| --- | --- | --- |
| Commit with `keep_recent_count: 0` | core/session_writer.py:409 | `commitKeepRecentCount` (:151), default 10 |
| Minimum recall query length 5 characters | core/recall.py:18 | `minQueryLength` (:81), default 3 |
| Context-mode `purpose: "coding"` | core/recall.py:262 | none |

Shared knobs with no Hermes counterpart include `enabled`, `autoRecall`,
`autoCapture`, the `capture*` family, the `takeover*`, `resume*` and `skill*`
families, and `debug`. The host already provides the on/off switch
(`memory.provider`) and owns session resumption and compaction.

Because several env var names are shared, a user who exports
`OPENVIKING_RECALL_LIMIT`, `OPENVIKING_PROFILE_TOKEN_BUDGET` or
`OPENVIKING_RECALL_PREFER_ABSTRACT` for another plugin changes Hermes too, with
Hermes's own range and meaning. Hermes reads env through `get_secret`, so under
a multiplexing gateway only the profile's `.env` applies.

**Why `hermes` is not in `HARNESS_KEYS`** (config-schema.mjs:252-264):

1. Hermes is a Python in-process provider and cannot run the JS resolver. It
   never reads the `plugin` or `plugin.hermes` sections of `ovcli.conf`
   (core/ovcli.py:52-65), so registering the key would make the JS doctor accept
   configuration that nothing consumes.
2. A per-harness entry can only change a knob's default (`knobDefault`,
   config-schema.mjs:275-278). It cannot change a range, an env var name or a
   unit, and those are exactly where Hermes differs.
3. The host owns Hermes's configuration entry points: `get_config_schema`,
   `save_config`, `post_setup` and the dashboard (H:349-402). A second
   configuration source would split one setting across two files.

This table is the maintained record of the differences. Changing a Hermes
default or env var name is a user-visible change and needs a release note.

## 3. Commit lifecycle invariants

These are the behaviours the refactor must keep. Rows 1-10 come from the plan's
invariant table; rows 11-24 were added by reading
`tests/test_standalone.py`, `tests/test_gateway_recall.py` and
`tests/test_native_memory_mirror.py`. "Not pinned" means no current test fails
if the invariant breaks; phase 5 must add a test before replacing the code.
Tests at line 772 and later in `test_standalone.py` reach private state
(`_turn_count`, `_state_path`, `_has_committed_session`) through hand-wired
providers. Both files passed when this record was written (84 tests).

| # | Invariant | Enforced by | Pinned by |
| --- | --- | --- | --- |
| 1 | Queued uploads, their retries, their backlog and their commits use the identity and connection generation captured when the turn was captured, even after a reload. | `_CommitScope` (core/connection.py:191-199), `_capture_commit_scope` (core/connection.py:227-239), capture in `sync_turn` (H:538-545), retries reuse `self.client` and a backlog keeps its client (core/session_writer.py:104-111, :156-189) | tests/test_standalone.py:907, :1205, :1219, :1278, :1319; mirror and recall identity at :606; tests/test_writer_backlog.py `test_backlog_keeps_its_identity_across_reload` |
| 2 | Each turn's sender peer is fixed at capture and is not changed by a later turn. | `_turn_peer` (H:257, H:473-474, core/transcript.py:140-142), `sync_turn` (H:557) | tests/test_gateway_recall.py:164 (:212-216), :233, :341 |
| 3 | A commit never overtakes an upload of the same sid that is still running, nor its unsent backlog. | `_drain_writers` before commit (core/session_writer.py:313, :334), `_writer_commit_lock` (H:585, core/session_writer.py:316, :346-350) | tests/test_standalone.py:800; backlog: tests/test_writer_backlog.py `test_commit_waits_for_backlog` |
| 4 | No new commit starts after `shutdown()`. | `_shutting_down` (H:306, core/session_writer.py:243, core/state_store.py:187, core/session_writer.py:251, :309, :317) | Not pinned |
| 5 | Recovery commits only markers whose owner run is dead and whose connection fingerprint matches; other markers stay untouched. | core/state_store.py:162-195; owner lock core/state_store.py:92-112; fingerprint core/state_store.py:169 | Fingerprint part: tests/test_standalone.py:1278 (:1312-1314). Dead-owner part: not pinned (the test releases the lock through `shutdown()` at :1299 before recovering) |
| 6 | After in-place compression (same sid), later writes can still trigger a commit. | Latch re-armed (H:655-659) | Not pinned |
| 7 | After the old sid was committed (by compression or `on_session_end`), the rotation finalizer does not commit it again. | Latch `scope.committed` (core/session_writer.py:224-234) checked first in `_session_needs_commit` (core/session_writer.py:260-266) | Latch after a threshold commit: tests/test_standalone.py:772 (:796-797). Compression path: not pinned |
| 8 | A skipped commit is made up later, by asking the server for `pending_tokens` when the local turn count is 0. | core/session_writer.py:267-274 | Not pinned |
| 9 | Non-primary contexts still rotate the sid, so recall and search use the current session. | Rotation independent of `_writes_enabled` (H:632-648) | tests/test_standalone.py:964 |
| 10 | For a sid this process never wrote, the plugin confirms `pending_tokens > 0` with the server before committing. | Same code as row 8 (core/session_writer.py:267-274) | Not pinned |
| 11 | Below the token threshold no commit is sent. Crossing it commits with `keep_recent_count: 0`, resets the turn count, latches the sid and deletes the marker. A later write re-arms the latch and rewrites the marker, and the next crossing commits again. `pending_tokens` is read both flat and under `result`. | H:582-597, core/session_writer.py:248-258, :276-296 | tests/test_standalone.py:772 |
| 12 | A failed threshold commit keeps the marker and the turn count, and the next turn retries it. | core/session_writer.py:290-296 | tests/test_standalone.py:827 |
| 13 | A failed or malformed `pending_tokens` lookup never re-uploads the turn and keeps the marker. | core/session_writer.py:254-258 | tests/test_standalone.py:850 |
| 14 | A failed upload triggers no threshold lookup and no commit; the marker stays. Messages that failed with a network error, 408, 429, 5xx, a retryable 409, 401 or 403 stay in the in-memory backlog and are resent, in order, before the next upload or commit of that sid; 401/403 messages are dropped once the connection changes, and other 4xx are dropped with a warning. | `_TurnUpload.run` returns `None`, `_upload_turn` (core/session_writer.py:156-189, :325-344) | tests/test_standalone.py:846; tests/test_writer_backlog.py |
| 15 | The threshold commit runs off the calling thread. A turn that lands while that commit is in flight stays pending and unlatched, and `on_session_end` commits it. | `_finalize_session_async` (core/session_writer.py:298-322) | tests/test_standalone.py:993 |
| 16 | `on_session_switch` commits the old sid even below the threshold, and rotates to the new sid. | H:664-665 | tests/test_standalone.py:924 |
| 17 | `cron`, `subagent` and `flush` contexts make no upload, commit or mirror request and write no marker. | `_writes_enabled` (H:432-433; checks at H:541, core/session_writer.py:327, H:678) | tests/test_standalone.py:935 |
| 18 | After a reload, the old generation's finalizer neither clears the new generation's marker nor suppresses its finalizer; each connection gets exactly one commit. Holds for a changed user and a changed endpoint. | Per-scope `finalizing` and `committed` sets (core/session_writer.py:236-246), per-generation marker name (core/state_store.py:40-42) | tests/test_standalone.py:1219 |
| 19 | Returning to an earlier identity (A → B → A) starts a new generation; the first A generation's commit does not clear the new one's marker, and only the new generation's turns are counted. | New `_CommitScope` per client (core/connection.py:192-201) | tests/test_standalone.py:1319 |
| 20 | Pending markers are named per sid and generation and never contain the API key. | `atomic_json_write` of `session_id`, `owner_run_id`, `connection_key` (core/state_store.py:128-129) | tests/test_standalone.py:1278 (:1297-1298) |
| 21 | Through the real `MemoryManager`, every turn synced on the mem-sync worker is archived once `on_session_end` commits, in order, and the marker is removed; assistant messages carry the configured assistant peer. | `sync_turn`, `on_session_end` | tests/test_gateway_recall.py:164 (:198-223) |
| 22 | The upload fallback chain (batch retry, per-message fallback, backlog resend) keeps the captured author. A turn without structured messages is sent as one user and one assistant text message, each cut to 4000 characters. | `_TurnUpload` (core/session_writer.py:113-189) | tests/test_gateway_recall.py:233 |
| 23 | Tool calls use the configured assistant peer, never the turn's sender, and the same URL, key, account and user as recall and capture. | MCP headers from `build_openviking_headers` with the REST client's snapshot (core/tools.py:124-150) | tests/test_gateway_recall.py:164 (:225-227) |
| 24 | Mirror writes stay in FIFO order, also across an idle worker restart, and their registry mappings are isolated by connection fingerprint. | core/mirror.py:59-69, :82-168 | tests/test_native_memory_mirror.py:251, :457; restarts: `test_worker_restarts_lose_and_reorder_no_write`, `test_soft_eviction_leaves_no_mirror_thread_and_later_writes_stay_in_order` |

Commit and recovery paths not covered by any test: recovery of legacy markers without an owner (core/state_store.py:100-104), the
deferred-commit drain timeout (core/session_writer.py:313-315), and the `on_session_end` drain
timeout (core/session_writer.py:334-336).

## 4. Module layout

Current since phase 2b. The provider class, its host hooks and `register()` stay
in `__init__.py`; the rest lives in the `core/` subpackage, which the host
installs with the plugin but does not pre-execute (section 1.2). Provider methods
moved as mixin classes whose bodies are unchanged; later phases replace the
mixins with services. `DEVELOPMENT.md` ("Module map") maps every function to its
module.

```text
examples/hermes-plugin/
  __init__.py          register() and OpenVikingMemoryProvider: hooks, mixin bases, re-exports
  _setup.py            setup wizard
  core/
    host.py            every Hermes import of core/ and its version-compatibility branches
    log.py             logger named after the provider's package
    deps.py            Deps: injectable transport, clock, health probe and setup validators
    settings.py        config schema, typed settings (SettingsMixin)
    connection.py      connection resolution, profile env, client lifecycle, autostart,
                       connection generation (_CommitScope, ConnectionMixin)
    ovcli.py  endpoint.py  envfile.py  local_server.py  health.py
    http.py            REST client, headers, errors, timeout classification (RestResultMixin)
    recall.py          prefetch, recall routes, deadlines, context mode, recall_status (RecallMixin)
    recall_list.py     list-mode recall (RecallListMixin)
    profile.py         session-start block (ProfileMixin)
    transcript.py      Hermes messages and senders to OpenViking parts and peers (TranscriptMixin)
    session_writer.py  turn upload, tracked workers, commit, exit registry (SessionWriterMixin)
    state_store.py     pending markers, run lock, recovery (StateStoreMixin)
    tools.py           openviking_* tool calls, local wrappers (search, forget,
                       add_resource), MCP connection settings (ToolsMixin)
    tool_catalog.py    default and optional tool sets, openviking_ naming,
                       in-process and disk tool caches
    mirror.py          native memory mirror (MirrorMixin)
    mcp_bridge.py      MCP connection, tools/list, tools/call, result conversion
```

`connection.py` also holds `ConnectionSnapshot` with both fingerprints;
`http.py` holds `build_openviking_headers`, shared by REST and MCP, and
`plugin_version()`; `session_writer.py` holds the per-`(sid, generation)`
backlog; `transcript.py` holds `openviking_session_id()`.

**Planned**, not in the tree yet:

- `cli.py`: `hermes openviking doctor / status`.
- `settings.py`: a `Settings` snapshot resolved once per hook call.
- `quick_local.py` from #5465 is placed after that change merges.

Dependency rules, enforced by `tests/test_structure.py` (static, on the source
files) and `tests/test_core_boundaries.py` (imports the core modules in a clean
interpreter):

- `core/*` does not import the package `__init__` or `_setup`.
- Only `core/host.py` imports Hermes packages (`agent.*`, `hermes_cli.*`,
  `hermes_constants`, `tools.*`, `tui_gateway`, `utils`, ...). Outside `core/`,
  the setup hooks `save_config` and `post_setup` in `__init__.py` (H:359-360,
  :396) and `_setup.py` still import `hermes_cli` and `hermes_constants` at call
  time; the test pins that set so it cannot grow.
- Importing any module registers no `atexit` hook, starts no thread, opens no
  connection and reads no config or environment. `mcp` and `httpx2` are
  imported lazily on the call path. Loading the plugin through Hermes discovery
  registers no `atexit` hook and starts no thread.
- The first 8 KB of `__init__.py` keep the word `MemoryProvider`, which
  discovery requires (hermes:plugins/memory/__init__.py:64-74).
- `core/*` gets its logger from `core/log.py`, named after the provider's
  module, because tests filter logs by `type(provider).__module__`. Exceptions:
  `core/mirror.py` and `core/mcp_bridge.py` (section 1.2).

## 5. Known deviations and unsupported platforms

**Platforms without `fcntl` (Windows): crash recovery unsupported.** `fcntl` is
optional (core/state_store.py:19-22). Without it the run lock is skipped (core/state_store.py:76-78), and
`_mark_session_pending` refuses to write a marker without a run lock
(core/state_store.py:121-123), so new sessions leave no marker and a crash loses their
uncommitted data. Only legacy markers without an owner are still recovered
(core/state_store.py:100-104). Normal exits still commit through `on_session_end` and the atexit
hook. Not planned for this refactor.

**Current deviations from the host contract**, each covered in section 1:

| Deviation | Location | Planned fix |
| --- | --- | --- |
| `identity_signature` missing | — | Phase 1 (optional) |
| One writer thread per turn; order not guaranteed; the backlog of failed messages lives only in memory | H:569-592, core/session_writer.py:256-344 | Section 6 |
| `shutdown()` does not commit | H:703-722 | Section 6 |
| `on_pre_compress` and `on_delegation` missing | — | Not planned |

**Wire and packaging deviations.**

- Headers for REST and MCP come from one builder, `build_openviking_headers`
  (core/http.py:54-77): the key only as `Authorization: Bearer`, account and
  user headers only without a key or on the REST client's trusted-identity
  retry, the actor peer header, and `User-Agent:
  openviking-memory-hermes/<plugin version>`. This matches the shared plugins
  (`../memory-plugin-shared/lib/ov-http.mjs`); the fixtures in
  `tests/test_contract_fixtures.py` pin it. Not a deviation since 3.0.0.
- `plugin.yaml` declares no hooks. The host reads `hooks` as `provides_hooks`
  (hermes:hermes_cli/plugins_manifest.py:509), which lists `register_hook`
  hooks; memory lifecycle hooks are provider methods, and upstream commit
  `72ee40fa68` removed these declarations from the bundled manifests. Its
  `pip_dependencies` only feeds the dashboard's dependency list
  (hermes:hermes_cli/web_server_memory.py:61-63); installs use
  `pyproject.toml`, which takes precedence
  (hermes:pm/plugin_declarations.py:132-154). The two lists are kept
  identical (phase 1).
- `_setup.py` imports private names from `hermes_cli.memory_setup`
  (S:48, S:425), and profile resolution reads the private
  `tui_gateway.launch_profile_policy._snapshot` (core/connection.py:86-94).
  These can break on a host upgrade; CI pins the host. Core modules reach
  `launch_profile_policy` only through `core/host.py` (phase 2b). **Planned**:
  move `_setup.py`'s `hermes_cli.memory_setup` imports there too.

**Deviations from the shared plugin spec.**

- Tools come from the server's `tools/list` over `/mcp`, named
  `openviking_*` (D1). Differences from the other MCP hosts: 11 tools by
  default instead of all 16 (`extra_tools` adds the rest); `forget` loses its
  `recursive` argument and is validated locally; `add_resource` uploads local
  paths first; `search` gets the session id injected in primary contexts.
- Configuration does not come from the shared schema; see section 2.
- There is no URI guard: the host gives memory providers no pre-tool event.
- Non-primary (cron) contexts still run startup recovery, and explicit tools
  keep their write access (README "Non-primary contexts"). This is intended
  (D9).

## 6. Not done yet

Plan items that 3.0.0 does not implement. Each is a behaviour change or a
check still owed, not a known bug.

| Item | State in 3.0.0 | Planned behaviour |
| --- | --- | --- |
| Synchronous upload on the host worker (D6) | `sync_turn` starts one plugin writer thread per turn; the writers share a lock but not the host's FIFO order | Upload inside `sync_turn` on the host's mem-sync worker with an 8 s request timeout, so the worker's order is the upload order |
| Per-caller lock budgets for `on_session_end` | Every caller drains writers for up to 10 s, then waits for the writer lock without a limit (the atexit path has its own 20 s budget) | Short budget on the compression path (turn thread), longer for soft eviction, `/new` and exit; on timeout skip the commit and keep the uncommitted flag and marker |
| Cooldown after a failed upload | Every turn tries the server again, even right after a failure | During a cooldown after a failure, later turns go straight to the backlog without a request |
| Re-commit of finalized sids | A turn uploaded to a sid that is no longer current, after that sid's finalizer already committed it, stays uncommitted until recovery or a later commit | After such an upload succeeds, commit that sid again on the worker |
| `pending_tokens` from the write response | The threshold check sends a separate `GET /api/v1/sessions/<sid>` after each successful upload | Read `pending_tokens` from the batch-write response |
| TTL for 401/403 backlogs | Messages rejected with 401/403 are kept until the connection generation changes, with no time limit | Also drop them after a TTL within the same generation |
| `shutdown()` commit | `shutdown()` joins threads but does not commit; an unsent backlog is dropped | Within a 5 s total budget, resend the backlog and commit; keep the atexit entry and run lock while data remains |
| `hermes openviking doctor` / `status` (`cli.py`) | Not present | Plugin version and location, config sources, credential source, connection and identity probe, `/mcp` reachability and tool cache, uncommitted sessions and backlogs, last recall outcome; offline mode and JSON output |
| Real-server recall comparison (phase 4) | Context mode became the default route from unit tests and the shared request contract only | Compare latency and hit-URI overlap of the context and list routes on real queries, with and without `session_id`, and set the `query_expansion` and `purpose` defaults from the result; then remove `recall_context_mode` |
| Cross-thread stress tests for the backlog | The backlog tests drive one writer at a time with fake transports | Concurrent `sync_turn`, session switches, reloads and commits against a failing and recovering server |

Smaller items still open: `identity_signature` (section 1.1), a `Settings`
snapshot resolved once per hook call, loggers of `core/mirror.py` and
`core/mcp_bridge.py` from `core/log.py`, `_setup.py`'s `hermes_cli.memory_setup`
imports moved into `core/host.py`, and failed messages written to disk (D3).
