# Developing the Hermes memory provider

This directory imports the OpenViking provider from
[`NousResearch/hermes-plugin-openviking`](https://github.com/NousResearch/hermes-plugin-openviking/tree/5dca75f4d3dcef9467ce2ff32e170d84c679de5f),
commit `5dca75f4d3dcef9467ce2ff32e170d84c679de5f`.

At the initial import, `__init__.py`, `_setup.py`, and `plugin.yaml` matched that
handoff and `plugins/memory/openviking/` in Hermes Agent commit
`d177b119e9c56c9ddc0b7379ffce52341ec06584`. The original MIT license is retained
in this directory. Original contributor history is available in
[Hermes Agent](https://github.com/NousResearch/hermes-agent/commits/d177b119e9c56c9ddc0b7379ffce52341ec06584/plugins/memory/openviking).

Contributions to this directory are provided under its [MIT license](LICENSE).
Preserve the existing copyright and permission notice.

The distribution name is `hermes-plugin-openviking`. The provider, plugin, and
future Hermes catalog key remain `openviking`. Existing `memory.openviking`
settings, environment variables, linked `ovcli.conf` files and data paths keep
their behavior. Release 3.0.0 replaced the imported `viking_*` tools with the
server's MCP tools as `openviking_*` and prefixes OpenViking session ids with
`hermes-`; see README "Upgrading to 3.0.0".

The active-session commit lifecycle was ported from
[KoNit-K's Hermes PR #112533](https://github.com/NousResearch/hermes-agent/pull/112533),
with the original author retained. The OpenViking adaptation uses a configurable
pending-token threshold instead of the original six-turn trigger.

Native memory mirroring is adapted from
[Hermes PR #100187](https://github.com/NousResearch/hermes-agent/pull/100187),
commit `32f75a9e6728a9a3d2f50a870dab3715a1f34fd7`, which continues
[austinlaw076's PR #85860](https://github.com/NousResearch/hermes-agent/pull/85860).
The external plugin uses relative imports and Hermes's context-preserving worker
helper. Its connection cache and session-commit lifecycle retain the later
OpenViking fixes.

Gateway sender attribution and recall scope adapt
[Hermes PR #105812](https://github.com/NousResearch/hermes-agent/pull/105812),
including liuhao1024's capture change from
[PR #98506](https://github.com/NousResearch/hermes-agent/pull/98506), with the
original author retained. The adaptation uses Hermes's existing per-turn author
hooks and preserves the original sender-ID encoding and Personal/Shared setup
presets. Shared Agent changes gateway session settings only after confirmation;
Personal Agent preserves them. An upgrade with no recall scope set retains the
previous recall requests. No Hermes core patch is required.

Profile-bound connection and recall settings adapt
[starship-s's Hermes PR #83647](https://github.com/NousResearch/hermes-agent/pull/83647),
with the original author retained. This port leaves memory URI handling as it
is: the original PR's UID-less `viking://user/memories/...` rewrite is not
accepted by current OpenViking servers.

## Migration coordination

Before the catalog cutover, submit a Hermes catalog entry with:

- `name: openviking`
- `repo: https://github.com/volcengine/OpenViking`
- `subdir: examples/hermes-plugin`
- `sha`: the full reviewed OpenViking commit SHA
- `category: memory`
- `tier: community`

Include the required `capabilities` block for tools, hooks, middleware, and
environment variables. Its declarations must match the plugin at the reviewed
SHA. Follow the [Hermes catalog entry schema](https://github.com/NousResearch/hermes-agent/blob/main/plugin-catalog/README.md#entry-schema)
and validate the pinned installation directory:

```bash
hermes plugins validate /path/to/hermes-profile/plugins/openviking
```

Publish the catalog entry and validate migration before Hermes removes its
bundled provider. Hermes PR [#114569](https://github.com/NousResearch/hermes-agent/pull/114569)
adds catalog recovery for configured providers that no longer resolve. The
bundled provider takes precedence while it remains present.

This plugin does not add a Desktop `config_schema.py`.
The wizard uses private helpers from `hermes_cli.memory_setup`; changes to
those helpers require compatibility checks. The plugin uses HTTP and does not
install or package the OpenViking server.

<!-- module-map:start -->
## Module map

The provider was one `__init__.py`. Phase 2b moved its code into `core/` without
changes to the moved bodies, so an upstream patch to a function applies to the
same function in the module listed here, unless the function is listed as
removed. Later phases changed some bodies and added names; "Added" lines list
the new names. Provider methods live in mixin classes that
`OpenVikingMemoryProvider` inherits. The class itself, `register()` and the
hooks Hermes calls stay in `__init__.py`. It re-exports the moved module-level
names that still exist, for tests and older callers, except `_default_deps`,
`_exit_hook_registered` and `fcntl`. Names added after phase 2b, apart from
`openviking_session_id`, are not re-exported; import them from their `core/`
module.

Only `core/host.py` imports Hermes (`agent`, `hermes_cli`, `hermes_constants`,
`tools`, `tui_gateway`, `utils`); function-level Hermes imports in moved code read
`from .host import <name>` and still import at call time. No core module imports
`__init__.py` or `_setup.py`. Core modules log through `core/log.py`, which returns
the logger named after the plugin package, the same one `__init__.py` uses;
`core/mirror.py` still uses the fixed name `plugins.memory.openviking` it had in
`native_memory_mirror.py`, and `core/mcp_bridge.py` uses
`plugins.memory.openviking.mcp`. `tests/test_structure.py` checks the import rules.
Tests reach a core module through the `core_module` fixture, because Hermes loads
the plugin under its own namespace.

### `core/log.py`

The plugin logger shared by the core modules.

- `get_logger()`

### `core/host.py`

Every Hermes import of the plugin and its version-compat branches.

- Imported with the module: `MemoryProvider`, `RecallStatus`, `_HERMES_VERSION`, `_get_launch_hermes_home`, `atomic_json_write`, `env_var_enabled`, `extract_user_instruction_from_skill_message`, `flatten_message_text`, `get_hermes_home`, `get_process_hermes_home`, `get_secret`, `spawn_context_thread`, `tool_error`
- Imported on use (`from .host import <name>` inside a function): `raise_if_read_blocked`, `build_profile_secret_scope`, `is_multiplex_active`, `reset_secret_scope`, `set_secret_scope`, `load_config`, `load_config_readonly`, `save_config`, `hydrate_profile_secret_sources`, `mkdir_under_hermes_home`, `reset_hermes_home_override`, `set_hermes_home_override`, `is_always_blocked_url`, `launch_profile_policy`

### `core/settings.py`

Config schema, connection defaults and typed, range-clamped settings.

- From `__init__.py`: `_DEFAULT_ENDPOINT`, `_OPENVIKING_SERVICE_ENDPOINT`, `_DEFAULT_AGENT`, `_CONNECTION_KEYS`, `_OPENVIKING_ENV_KEYS`, `_DEFAULT_RECALL_REQUEST_TIMEOUT_SECONDS`, `_cfg_field`, `_NUM`, `_CONFIG_SCHEMA`, `_SETTING_SPECS`, `_RECALL_SETTING_KEYS`, `_INVALID_SETTING_WARNINGS`, `_INVALID_SETTING_WARNINGS_LOCK`, `_clean_config_value`, `_validate_openviking_identity_value`
- `OpenVikingMemoryProvider` methods, now in `SettingsMixin`: `_parse_setting_value`, `_setting`

### `core/endpoint.py`

OpenViking endpoint normalization, labels and the SSRF floor check.

- From `__init__.py`: `_LOCAL_OPENVIKING_HOSTS`, `_OpenVikingEndpointError`, `_openviking_endpoint_label`, `_openviking_endpoint_is_always_blocked`, `_normalize_openviking_url`, `_is_local_openviking_url`

### `core/envfile.py`

Profile ``.env`` writes that keep secrets on one line and 0600.

- From `__init__.py`: `_secure_secret_file`, `_env_line_safe`, `_write_env_vars`

### `core/ovcli.py`

OpenViking CLI (``ovcli.conf``) profiles: paths, parsing and discovery.

- From `__init__.py`: `_OVCLI_CONFIG_ENV`, `_OVCLI_DEFAULT_RELATIVE_PATH`, `_OVCLI_SAVED_PREFIX`, `_OvcliProfile`, `_default_ovcli_config_path`, `_resolve_ovcli_config_path`, `_load_ovcli_config`, `_connection_values_from_ovcli`, `_is_valid_ovcli_profile_name`, `_load_profile`, `_profile_identity`, `_discover_ovcli_profiles`, `_ovcli_values_for`, `_ovcli_data_from_connection_values`

### `core/http.py`

OpenViking REST client, error formatting, timeout classification and identity probe.

- From `__init__.py`: `_IDENTITY_UNSET`, `_TIMEOUT`, `_OPENVIKING_IDENTIFIED_STATES`, `_OpenVikingHTTPError`, `_sanitize_openviking_error_message`, `_status_code_from_error`, `_format_openviking_exception`, `_get_httpx`, `_is_timeout_error`, `_VikingClient`, `_resolve_user_space`, `_probe_openviking_identity`
- `OpenVikingMemoryProvider` methods, now in `RestResultMixin`: `_unwrap_result`, `_extract_text_content`
- Added: `build_user_agent`, `plugin_version`, `_read_plugin_version`, `_openviking_user_agent`, `build_openviking_headers` (shared by REST and MCP), `is_retryable_failure`
- Removed: `_OPENVIKING_USER_AGENT` (the User-Agent now carries the plugin version)

### `core/health.py`

Server health classification and the setup wizard's validators.

- From `__init__.py`: `_OPENVIKING_RESPONDED_FAILURE_PREFIX`, `_LEGACY_OPENVIKING_IDENTITY_DETAIL`, `_identity_failure`, `_client_health_failure`, `_validate_openviking_reachability`, `_validate_openviking_setup_values`, `_classify_runtime_openviking_health`

### `core/local_server.py`

Autostart of a local openviking-server and its port diagnostics.

- From `__init__.py`: `_LOCAL_OPENVIKING_AUTOSTART_TIMEOUT`, `_LOCAL_OPENVIKING_PROBE_TIMEOUT`, `_LOCAL_SERVER_STARTED`, `_LOCAL_SERVER_OCCUPIED`, `_LOCAL_SERVER_FAILED`, `_OPENVIKING_SERVER_LOG_RELATIVE_PATH`, `_local_openviking_bind`, `_local_openviking_port_is_open`, `_describe_local_port_listener`, `_local_listener_suffix`, `_start_local_openviking_server`, `_wait_for_openviking_health`

### `core/deps.py`

``Deps``: every outside dependency of the plugin as one injectable value.

- From `__init__.py`: `Deps`, `_default_deps`, `default_deps`, `set_default_deps`, `_rest_client`

### `core/tools.py`

The ``openviking_*`` tool calls over MCP, their local wrappers and argument checks.

- From `__init__.py`: `_REMOTE_RESOURCE_PREFIXES`, `_GENERATED_MEMORY_SUMMARY_FILENAMES`, `_OPENVIKING_RECALL_TOOL_NAMES`, `_SYSTEM_PROMPT_TOOL_GUIDANCE`, `_zip_directory`, `_is_windows_absolute_path`, `_validate_forget_memory_uri`, `_is_local_path_reference`
- Added: `_MCP_PATH`, `_strip_front_matter`, `_mcp_connection`, `prime_tool_cache`; `ToolsMixin` methods `_tool_connection_settings`, `_tools_hermes_home`, `_extra_tools`, `_mcp_connection`, `_catalog_key`, `_list_server_tools`, `_openviking_tool_schemas`, `_refresh_tool_catalog`, `_call_openviking_tool`, `_prepare_search`, `_prepare_forget`, `_prepare_add_resource`
- Removed in 3.0.0 with the `viking_*` tools: `_READ_BATCH_LIMIT`, `_READ_BATCH_FULL_LIMIT`, `_LEVEL_ENDPOINTS`, `_LEVEL_MAX_CHARS`, `_tool_schema`, `_str`, `SEARCH_SCHEMA`, `READ_SCHEMA`, `BROWSE_SCHEMA`, `REMEMBER_SCHEMA`, `FORGET_SCHEMA`, `ADD_RESOURCE_SCHEMA`, `_TOOL_SCHEMAS`, `_TOOL_HANDLERS`, and the methods `_normalize_summary_uri`, `_is_directory_uri`, `_tool_search`, `_read_uri_payload`, `_tool_read`, `_tool_browse`, `_tool_remember`, `_tool_forget`, `_tool_add_resource`. Upstream patches to these do not apply.

### `core/tool_catalog.py`

Added in phase 6. Which server tools Hermes registers and where the list comes from.

- `TOOL_PREFIX`, `LIVE_LIST_BUDGET_SECONDS`, `DEFAULT_EXPOSED_TOOLS`, `OPTIONAL_TOOLS`, `RECALL_TOOLS`, `RECALL_TOOL_NAMES`, `CACHE_RELATIVE_PATH`, `parse_extra_tools`, `exposed_server_names`, `server_name`, `to_hermes_schema`, `build_schemas`, `fingerprint`, `cache_path`, `read_disk_cache`, `write_disk_cache`, `remember`, `load`, `clear_memory_cache`

### `core/mcp_bridge.py`

Added before phase 6. One short MCP session per call on a one-shot thread; `tools/list`, `tools/call` and result conversion.

- `McpConnection`, `ToolResult`, `McpBridgeError`, `list_tools`, `call_tool`, `content_to_text`, `bound_text`, `to_tool_result`, `READ_ONLY_TOOLS`, `HANDSHAKE_TIMEOUT_SECONDS`, `CALL_TIMEOUT_SECONDS`, `MAX_RESULT_BYTES`, `MAX_RESULT_LINES`

### `core/transcript.py`

Hermes messages and senders to OpenViking message parts and peer IDs.

- Added: `openviking_session_id` (the `hermes-` prefix)
- From `__init__.py`: `_gateway_peer_id`, `_derive_openviking_user_text`, `_preview`, `_TOOL_STATUS_ERROR_ALIASES`, `_TOOL_STATUS_COMPLETED_ALIASES`, `_message_text`, `_tool_part`, `_tool_call_id`, `_tool_call_name`, `_is_openviking_recall_tool_name`, `_tool_call_input`, `_tool_result_status`, `_rfind_message`, `_index_tool_calls`
- `OpenVikingMemoryProvider` methods, now in `TranscriptMixin`: `_sender_peer`, `_current_sender_peer`, `_extract_current_turn_messages`, `_messages_to_openviking_batch`

### `core/connection.py`

Connection settings, profile environment, client lifecycle, autostart and commit scopes.

- From `__init__.py`: `_FAILED_CONFIG_RETRY_COOLDOWN_SECONDS`, `_RETRY_LATER`, `_FIX_ENDPOINT`, `_HTTPX_MISSING`, `_load_hermes_openviking_config`, `_profile_openviking_env`, `_resolve_connection_settings`, `_emit_runtime`, `_runtime_openviking_timeout_message`, `_CommitScope`
- Added: `ConnectionSnapshot`
- `OpenVikingMemoryProvider` methods, now in `ConnectionMixin`: `_start_runtime_openviking_waiter`, `_settings_tuple`, `_build_client`, `_publish_client`, `_capture_commit_scope`, `_finish_runtime_openviking_start`, `_handle_runtime_openviking_unreachable`, `_ensure_client`, `_profile_config_and_env`, `_resolve_bound_connection_settings`, `_in_cooldown`, `_ensure_client_locked`, `_new_client`, `_user_space`

### `core/recall.py`

Prefetch: recall routing, deadlines, context-mode recall and recall status.

- From `__init__.py`: `_RECALL_QUERY_MIN_CHARS`, `_RECALL_MIN_TIMEOUT_SECONDS`, `_PREFETCH_BUDGET_SECONDS`, `_RECALL_FALLBACK_RESERVE_SECONDS`, `_SESSION_START_DEFAULT_KEY`, `_RECALL_PENDING, _RECALL_INJECTED, _RECALL_EMPTY`, `_RECALL_TIMEOUT, _RECALL_UNAVAILABLE, _RECALL_ERROR`, `_RECALL_OUTCOMES_KEPT`, `_RECALL_STATUS_LABEL`, `_RecallProbe`
- Added: `_CONTEXT_QUOTA_WEIGHTS`, `_context_quotas` (context-mode routes)
- `OpenVikingMemoryProvider` methods, now in `RecallMixin`: `_prefetch_context`, `_run_prefetch_parts`, `_prefetch_budget`, `_recall_budget`, `_begin_recall`, `_finish_recall`, `_recall_record`, `_remaining_recall_timeout`, `_fallback_request_timeout`, `_search_prefetch_context`, `_recall_config`

### `core/recall_list.py`

List-mode recall: search/find, candidate ranking and entry building.

- From `__init__.py`: `_RECALL_SUMMARY_KEYS`
- `OpenVikingMemoryProvider` methods, now in `RecallListMixin`: `_post_prefetch_search`, `_clamp_score`, `_recall_abstract`, `_select_recall_candidates`, `_build_prefetch_entries`

### `core/profile.py`

The session-start memory block: profile, preferences and entities.

- From `__init__.py`: `_SESSION_START_SUFFIXES`, `_SESSION_START_LIST_PARAMS`
- `OpenVikingMemoryProvider` methods, now in `ProfileMixin`: `_claim_session_start`, `_settle_session_start`, `_rearm_session_start`, `_profile_token_budget`, `_extract_memory_listing`, `_token_units`, `_estimate_tokens`, `_take_tokens`, `_truncate_profile_content`, `_assemble_session_start_memory_block`, `_format_memory_listing`, `_build_session_start_memory_block`, `_session_start_memory_context`

### `core/session_writer.py`

Turn upload, tracked workers, session commit and the exit commit.

- From `__init__.py`: `_SESSION_DRAIN_TIMEOUT`, `_DEFERRED_COMMIT_TIMEOUT`, `_SESSION_MESSAGE_BATCH_LIMIT`, `_SYNC_TRACE_ENV`, `_NON_PRIMARY_AGENT_CONTEXTS`, `_exit_registry`, `_exit_registry_lock`, `_exit_hook_registered`, `_EXIT_COMMIT_BUDGET`, `_register_for_exit`, `_deregister_for_exit`, `_atexit_commit_sessions`, `_TurnUpload`
- Added: `_BACKLOG_MAX_MESSAGES`, `_BACKLOG_MAX_BYTES`, `_upload_failure_kind`, `_Backlog`; `SessionWriterMixin` methods `_backlog_table`, `_prune_backlog`, `_backlog_add`, `_flush_backlog`, `_upload_turn`
- `OpenVikingMemoryProvider` methods, now in `SessionWriterMixin`: `_spawn_tracked`, `_join_all`, `_drain_finalizers`, `_drain_writers`, `_has_committed_session`, `_mark_session_committed`, `_claim_deferred_sid`, `_maybe_commit_live_session`, `_session_needs_commit`, `_commit_session`, `_finalize_session_async`, `_end_session`, `_has_uncommitted_data`

### `core/state_store.py`

Pending-session markers, run locks and recovery of dead runs' sessions.

- From `__init__.py`: `fcntl`, `_PENDING_SESSIONS_RELATIVE_DIR`, `_RUN_LOCKS_RELATIVE_DIR`, `_LEGACY_RECOVERY_LOCK_FILENAME`, `_LOCK_BUSY_ERRNOS`
- `OpenVikingMemoryProvider` methods, now in `StateStoreMixin`: `_state_path`, `_flock_open`, `_flock_close`, `_acquire_run_lock`, `_release_run_lock`, `_claim_owner_run_for_recovery`, `_mark_session_pending`, `_clear_pending_session`, `_pending_sessions`, `_recover_pending_sessions`

### `core/mirror.py`

Mirror of Hermes native MEMORY.md / USER.md entries into OpenViking.

- From `__init__.py`: `_MEMORY_WRITE_TARGET_SUBDIR_MAP`
- From `native_memory_mirror.py`: `logger`, `_REGISTRY_VERSION`, `_REGISTRY_RELATIVE_PATH`, `_SUPPORTED_ACTIONS`, `_POLL_SECONDS`, `_REGISTRY_LOCKS_GUARD`, `_REGISTRY_LOCKS`, `_MappingError`, `_registry_lock`, `_connection_fingerprint`, `NativeMemoryMirror`, `_MIRROR_ATTR`, `enqueue_native_memory_write`, `shutdown_native_memory_mirror`
- `OpenVikingMemoryProvider` methods, now in `MirrorMixin`: `_build_memory_uri`

<!-- module-map:end -->

## Validation

The `Hermes Plugin Tests` workflow runs this directory's complete external-provider
suite on plugin changes, pushes to `main`/`develop`, and manual dispatch.
It uses Python 3.14 and a reviewed Hermes commit, with test retries disabled.
When updating the host SHA in `.github/workflows/hermes-plugin-tests.yml`, check
the host dependency pins and run the suite before submitting the change.
These regression tests use mock responses and local test servers; live-service
validation remains part of release testing.

CI also runs `scripts/check-hermes-plugin-install.py` through the real Hermes
CLI in an isolated profile. It installs this repository's plugin subdirectory,
checks that the installed tree equals the plugin directory at the checked-out
commit file for file, enables it through Hermes's dependency manager, validates
the installed directory, and checks that the external provider and every
`core/` module import from the installed copy. The bundled OpenViking copy is
temporarily removed from the test checkout. This catches dependency conflicts
across supported platforms that runtime tests alone do not exercise.

Use a Hermes checkout with its development dependencies installed.

The complete mirror tests require the committed-entry event contract introduced
in [Hermes PR #118903](https://github.com/NousResearch/hermes-agent/pull/118903)
and merged through [#120003](https://github.com/NousResearch/hermes-agent/pull/120003)
(commit `5908e1aaa83e82aaf12541d7a9d90762d0b46a64`):
`MemoryManager` forwards `previous_content` for each successful replace/remove.
The legacy compatibility tests verify that missing metadata skips these remote
mutations. Do not substitute a guessed match in tests or production.

From that checkout, run its canonical test runner against this directory:

```bash
PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}" HERMES_TEST_FILE_RETRIES=0 \
  scripts/run_tests.sh /path/to/OpenViking/examples/hermes-plugin/tests \
  --confcutdir=/path/to/OpenViking/examples/hermes-plugin/tests -q
```

`--confcutdir` keeps pytest from importing the plugin as a test package before
Hermes loads it under its own namespace. The tests use temporary profile homes
and remove bundled-provider discovery.
They cover external loading, profile isolation, setup, the MCP tool bridge and
catalogue (against a fake MCP session), recall routing, the upload backlog,
session commits, and native memory mirroring. The mirror suite includes ordered writes, restart
continuity, registry failures, connection isolation, and concurrent workers.
Gateway tests use mock events through Hermes's turn hooks and memory manager.
They cover sender changes, capture retries, commits, recall scopes, compression
fallback, and missing sender metadata. Setup tests cover both presets,
confirmation, cancellation, profile-local persistence, connection routes, and
actual Hermes session keys.
Provider-specific regression tests belong here and must use the shared external
loader fixture. Generic Hermes framework tests remain in Hermes.
Inject fakes for the REST transport or client, the MCP session, the clock, the
health probe and the setup wizard's profile discovery and validators through `Deps`, using the
`inject_deps` fixture, instead of patching names on the plugin module. A patched
module global stops reaching the code that reads it once that code moves into
`core/`.

For compatibility checks while Hermes still bundles OpenViking, also run its
provider tests. These load the bundled copy unless explicitly routed through the
external loader; they do not replace this directory's tests:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/memory/test_openviking_provider.py \
  tests/plugins/memory/test_openviking_optional_peer.py \
  tests/plugins/memory/test_openviking_shutdown.py \
  tests/plugins/memory/test_openviking_endpoint_always_blocked.py \
  tests/openviking_plugin/test_openviking.py -q
```

## Not done yet

The approved refactor plan is not finished. `DESIGN.md` section 6 describes
each item with its current state:

- synchronous upload on the host's mem-sync worker
- per-caller lock budgets for `on_session_end`
- a cooldown after a failed upload
- re-commit of a finalized sid that receives a late upload
- `pending_tokens` read from the write response
- a TTL for backlogs rejected with 401/403
- the `hermes openviking doctor` / `status` CLI
- the real-server comparison of context-mode and list recall
- cross-thread stress tests for the upload backlog
