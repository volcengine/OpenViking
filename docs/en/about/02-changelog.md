# Changelog

All notable changes to OpenViking will be documented in this file.
This changelog is automatically generated from [GitHub Releases](https://github.com/volcengine/OpenViking/releases).

## v0.3.23 (2026-06-03)

### Highlights

- **Native `ov` CLI refresh**: `ov config` is now the interactive configuration manager for adding, editing, deleting, and switching saved configs, while `ov config show`, `ov config validate`, and `ov config switch` remain explicit subcommands. New `ov language` / `ov lang` selects the display language, `ov status [--verbose]` provides aggregated diagnostics, and `ov health` plus runtime errors render with clearer guidance.
- **Web Studio Playground and identity management**: Studio adds a Playground with a context tree, Terminal, and Agent panel, plus a Connection & Identity page that can save connection state, select account/user/agent, create accounts and users, and copy or regenerate API keys.
- **Config-driven VikingBot experience recall**: New `bot.ov_server.recall_exp_first_round_only`, `exp_recall_limit`, and `exp_recall_max_chars` inject agent experience only on the first turn, and both local and remote modes isolate experience namespaces by incoming `agent_id`.
- **Simpler resource watches**: `add_resource` no longer requires an explicit `to` when `watch_interval > 0`; when the import returns a stable `root_uri`, the watch task binds to it automatically, with CLI, MCP, and docs examples updated to match.
- **Structured plugin tool results and CJK token estimation**: Claude Code and OpenClaw plugins now write structured tool parts instead of flattening calls and results into text only, and CJK-aware token estimation is shared across Python and plugin code to reduce budget underestimation for Chinese, Japanese, and Korean sessions.

### Upgrade Notes

- `ov config setup-cli` has been removed; use bare `ov config` for setup. On first use the new CLI prompts for a display language in interactive shells, so non-interactive automation should run `ov language en` or `ov language zh-CN` first.
- `ov status` now defaults to a curated diagnostic view; use `ov status --verbose` or `-o json` for raw component data.
- `ovcli.conf` defaults to `http://127.0.0.1:1933`, and config serialization now omits default and empty fields.
- Semantic processing concurrency now defaults to 64 instead of 100, and the documented `vlm.max_concurrent` default is corrected to 64. Local directory uploads now skip symlinks to avoid recursive, duplicate, or out-of-scope ingestion.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.3.22...v0.3.23)

## v0.3.22 (2026-05-29)

### Highlights

- **Configurable retrieval query planner**: Added a lightweight query-planner config so the intent-analysis model used during retrieval can be selected and tuned.
- **Legacy Memory V1 removed**: The deprecated memory v1 path was removed, and the memory `version` field now rejects `v1` payloads.
- **LangChain reliability**: Stale OpenViking clients are now recovered automatically, and LangChain integrations can perform local batch message writes.
- **VikingDB robustness**: Vector search now skips candidates with corrupted JSON fields, and `ap-southeast-1` region host mappings were added for VikingDB.
- **CLI and server polish**: The `ov` CLI reports a missing CLI config before issuing server requests, server-mode terminology was clarified from `dev-implicit` to `dev`, and embedding input truncation was unified.

### Upgrade Notes

- Memory V1 has been removed; callers must use the current memory `version`, and `v1` payloads are now rejected.
- Server-mode wording changed from `dev-implicit` to `dev`; update scripts or dashboards that match on the previous term.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.3.21...v0.3.22)

## v0.3.21 (2026-05-27)

### Highlights

- **More retrievable trajectory memory**: The trajectory schema now has `retrieval_anchor` and an `embedding_template`, so vector indexing uses `trajectory_name + retrieval_anchor` instead of the full operation contract. Experiences and trajectories are connected with system-managed `derived_from` `StoredLink` records (forward `links` + reverse `backlinks`), replacing fragile `source_trajectories` metadata.
- **Batch session message ingestion**: Added `POST /api/v1/sessions/{session_id}/messages/batch` and `ov session add-messages` to add multiple messages in one call (useful for history import and memory extraction); `ov add-memory` now uses the same stricter JSON message parser.
- **OpenClaw search is now `ov_search`**: The OpenViking OpenClaw plugin no longer registers `memory_search`, avoiding collisions with OpenClaw built-ins. Use `ov_search` or `/ov-search` after importing resources or skills.
- **Stronger URL and document parsing**: HTTP import detection now recognizes image, audio, video, Office, EPUB, and zip downloads, and re-checks headers after `GET` when `HEAD` is unreliable. Local Word/PowerPoint/Excel/EPUB/legacy-doc conversions now run in worker threads so they no longer block the event loop.
- **Web Studio ships with Python installs**: `setup`/`build` now builds and bundles the Web Studio static assets, so `/studio` works from pip/pipx installs without Docker.
- **NVIDIA NIM through LiteLLM VLM**: Model names containing `nvidia_nim` or `nemotron` now route through the NVIDIA NIM LiteLLM prefix and `NVIDIA_NIM_API_KEY`.
- **tau2/VikingBot benchmark runner**: Added `benchmark/tau2/vikingbot` for cold start, train trajectory commits, repeated test averaging, and cross-epoch self-improvement; the previous tau2 LLM harness moved to `benchmark/tau2/llm`.

### Upgrade Notes

- OpenClaw users should migrate `/memory-search` and `memory_search` calls to `/ov-search` and `ov_search`.
- pip/pipx and Docker builds now produce the Web Studio bundle through the Python build path; set `OV_SKIP_STUDIO_BUILD=1` to skip the Studio build during local development.
- `content.read` adds a `raw=true` parameter; default reads still hide memory internals for compatibility.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.3.20...v0.3.21)

## v0.3.20 (2026-05-25)

### Highlights

- **Request-scoped HTTP profiling**: Servers can enable `server.profile_enabled`; requests with `profile=1` then run `cProfile` for only that HTTP request and append `profile` lines to JSON responses. The `ov` CLI can enable and display this with `--profile`.
- **Batch Session message ingestion**: Added `POST /api/v1/sessions/{session_id}/messages/batch` plus Python HTTP client and Session wrapper support via `batch_add_messages` (up to 100 messages per request), reducing HTTP round trips for LangChain/LangGraph-style integrations.
- **Memory embedding templates**: Memory schemas now support a top-level `embedding_template`, replacing field-level `searchable` flags. The built-in `entities`, `events`, and `preferences` templates include key fields plus final content in embedding input.
- **Semantic indexing reliability**: Resource processing now syncs temp source trees into the target before running the semantic DAG (diff results use target URIs); stale semantic lock handoffs can be recovered by reacquiring tree locks, and lock acquisition failures requeue work instead of tripping the API circuit breaker.
- **Embedding input guardrails**: Embedding queue input is truncated according to `embedding.max_input_tokens`, and oversized-input errors are classified as `input_too_large` to avoid repeated retries for unrecoverable payloads.

### Upgrade Notes

- If a custom memory schema still uses field-level `searchable: true`, migrate it to top-level `embedding_template`; field-level `searchable` no longer contributes to embedding text generation.
- Rename `memory.enable_role_id_memory_isolate` to `memory.role_id_memory_isolation_enabled` in custom `ov.conf` files.
- Treat `profile=1` as a debugging tool, not a high-traffic production default. Profile output is capped at about 16 KiB.
- The batch message endpoint accepts up to 100 messages per request.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.3.19...v0.3.20)

## v0.3.19 (2026-05-22)

### Highlights

- **Breaking Console BFF timezone semantics**: `/api/v1/console/dashboard/summary`, `/tokens`, and `/context-commits` now accept an IANA `timezone` query parameter and return viewer-timezone buckets from the server. Consumers should treat returned `date` and `hour` fields as already localized instead of shifting them again on the client.
- **Usage/Audit UTC persistence**: Token, retrieval, context-commit, agent-activity, and audit rollups now persist UTC `date_utc`, `hour_utc`, and `created_at` values, with read-time re-bucketing through `zoneinfo` for DST and half-hour timezone support.
- **Local Usage/Audit schema reset**: The SQLite store tracks schema version v3 and resets incompatible local Usage/Audit tables on upgrade, avoiding mixed local/UTC rows and partial daily/hourly migrations for short-retention pre-GA data.
- **Web Studio heatmap alignment**: Web Studio injects the browser timezone into Console BFF requests and the heatmap now uses server-returned bucket dates directly, fixing the UTC+ viewer double-shift that could push today's commits into tomorrow.
- **Adjacent updates**: Added session skill extraction behind `memory.session_skill_extraction_enabled`, Hermes OpenViking LoCoMo benchmark scripts, an OAuth docs correction for the new Studio OAuth setup entry, and a LiteLLM dependency refresh.

### Upgrade Notes

- This release has a breaking BFF behavior change: custom Console clients, generated SDKs, or dashboards using `/api/v1/console/*` should send `timezone=<IANA name>` where appropriate and remove client-side UTC-to-local bucket shifting.
- Returned `date` / `hour` buckets from `tokens` and `context-commits` are viewer-timezone buckets. `audit.created_at` remains UTC ISO and should only be formatted for display.
- On first boot with the new Usage/Audit SQLite schema, incompatible local usage/audit tables are dropped and recreated. Existing short-retention usage rollups and request audit rows may be discarded.
- Regenerate any pinned OpenAPI clients or static Console API types so the new `timezone` parameter is available.
- If `timezone` is omitted, Console BFF falls back to `server.observability.usage_audit.timezone` (default `local`); set it explicitly for server-side integrations that need stable user-facing day boundaries.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.3.18...v0.3.19)

## v0.3.18 (2026-05-22)

### Highlights

- **Web Studio as the default console**: Added the `web-studio` console workspace, shipped it in Docker and pip distributions, served it at `/studio`, moved OAuth authorize UI into it, and retired the legacy console while keeping favicon compatibility routes.
- **MCP, API, and CLI automation**: Added Watch Management across REST, `ov`, and MCP; added progressive single-entrypoint local-file upload; added `code_outline`, `code_search`, and `code_expand`; and tightened upload-only and zip `--ignore-dirs` handling.
- **Agent and OpenClaw ecosystem**: OpenClaw setup helper now supports npm plugin installs, plugin docs align with ClawHub package metadata, `ov_dream` was added as an OpenClaw skill, and oversized OpenClaw tool results can be externalized to OpenViking.
- **Memory and retrieval**: Upgraded trajectory extraction, added memory link support, added switchable Vaka memory templates, fixed missing tool-call counts and missing message-peer retrieval, and parallelized hierarchical child search.
- **Storage, VectorDB, and model reliability**: Async storage locks/IO and loop-isolated async clients reduce contention; fixes cover semantic lock ownership, false `mv not found`, URI remapping, S3 grep performance, VectorDB Unicode recovery, oversized byte rows, embedding error surfacing, and VLM LiteLLM native routes.
- **Observability, docs, and deployment polish**: Added VikingBot feedback observability, centralized the metric registry, moved usage audit SQLite into system data, refreshed Helm chart defaults, updated brand assets and QR code, and documented public base URL, signed upload TTLs, Watch APIs, MCP code tools, readiness probes, and the `/studio` migration.

### Upgrade Notes

- Legacy console and `8020` references should migrate to Web Studio at `/studio`; update custom reverse proxies, bookmarks, and deployment docs.
- Docker and pip packages now include Web Studio assets; deployments with custom Dockerfile, Caddy, or Helm overlays should review the refreshed defaults before rolling forward.
- Usage audit SQLite now lives under system data; deployments with hand-managed local audit files should confirm the new path and retention expectations.
- Embedding upstream failures are no longer hidden behind silent timeouts; callers and health probes should expect explicit provider errors.
- Watch Management, MCP upload, and code-navigation tools expand public integration surfaces; regenerate clients or static API/MCP schema snapshots where applicable.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.3.17...v0.3.18)

## v0.3.17 (2026-05-15)

### Highlights

- **Agent Integrations**: New LangChain and LangGraph integration via `openviking.integrations.langchain` (`OpenVikingRetriever`, `with_openviking_context()`, `OpenVikingChatMessageHistory`, `OpenVikingContextMiddleware`, `OpenVikingStore` as a LangGraph store, `create_openviking_tools()`); revamped Codex / OpenCode plugins use lifecycle hooks for auto-recall, incremental capture, and PreCompact commit, and connect directly to the native `/mcp` endpoint.
- **OVPack v2 and full backup/restore**: `ov export` / `ov import` support v2 manifests, file checksums, portable index scalar fields, optional dense vector snapshots, and conflict policies; new `ov backup` / `ov restore` cover full public-scope migration.
- **Native CLI distribution**: New `@openviking/cli` npm package installs the platform `ov` binary via `npm i -g @openviking/cli`; Rust CLI release pipeline adds Linux musl artifacts, npm trusted publishing, and a broader integration test suite.
- **Retrieval and filesystem**: `find` / `search` accept a `level` filter for L0 abstracts, L1 overviews, and L2 file hits. Resource files gained a Phase 1 WebDAV adapter, and `observer.filesystem` exposes filesystem-level observability.
- **Console and Usage/Audit**: New Usage/Audit module and `/api/v1/console/*` BFF aggregate token usage, retrieval counts, context commit heatmaps, request audit logs, and context inventory from the existing observability event bus.
- **Storage and concurrency reliability**: Strengthened exact-path and lifecycle locks fix content-write races; blocking backend calls moved off the event loop; QueueFS SQLite persistence expanded; task records are now persisted for cross-instance lookup; `add_resource(wait=false)` returns a reserved `root_uri` plus persistent task progress while ingestion completes.

### Upgrade Notes

- `storage.task_tracker` is deprecated and ignored. Task records are always persisted under each account's `_system/tasks` directory.
- `vlm.backup` is a single-tier failover and only triggers on retryable errors (rate limit, `5xx`, connection failure, timeout). Auth, authorization, and billing failures do not trigger failover.
- `vlm.extra_request_body` is merged into the OpenAI SDK / LiteLLM `extra_body`, useful for Ollama, OpenAI-compatible gateways, and providers requiring extra JSON fields.
- New Codex memory plugin deployments should prefer `OPENVIKING_*` environment variables for tuning; the legacy `codex.*` block in `ov.conf` remains supported for backward compatibility but is no longer recommended.
- OVPack dense vector snapshots require pure dense indexes; embedding provider, model, input, parameters, and dimension must match. Mismatches fall back to recompute under `--vector-mode auto` or fail under `--vector-mode require`.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.3.16...v0.3.17)

## v0.3.14 (2026-04-30)

### Highlights

- **Observability**: OTLP export now supports custom `headers` for traces, logs, and metrics, enabling direct connection to backends that require extra auth or gRPC metadata.
- **Upload**: Local directory scans and uploads now respect root and nested `.gitignore` rules, reducing noise from build artifacts and temp files.
- **Search**: `search` and `find` now accept multiple target URIs for cross-directory and cross-repo retrieval.
- **Multi-tenant**: OpenClaw plugin clarifies `peer_prefix` as peer metadata only; OpenCode memory plugin adds tenant header forwarding.
- **Admin**: Deprecated agent namespace discovery surfaces are removed.

### Upgrade Notes

- OTLP backends requiring extra auth can now use `headers` across all three exporter types (gRPC metadata in gRPC mode, HTTP headers in HTTP mode).
- Local directory uploads will now filter files per `.gitignore` by default — previously imported temp/generated files may be excluded after upgrade.
- OpenClaw plugin runtime identity now maps through `peer_prefix` peer metadata instead of an OpenViking agent namespace.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.3.13...v0.3.14)

## v0.3.13 (2026-04-29)

### Highlights

- **Native MCP endpoint**: `openviking-server` now exposes `/mcp` on the same port as the REST API, reusing API-Key auth and providing 9 tools (`search`, `read`, `list`, `store`, `add_resource`, `grep`, `glob`, `forget`, `health`).
- **User-level privacy configs**: New `/api/v1/privacy-configs` API and `openviking privacy` CLI for managing sensitive skill settings with version history and rollback.
- **Observability upgrade**: Unified `server.observability` config enables Prometheus `/metrics` and OpenTelemetry exporters for metrics, traces, and logs.
- **Retrieval tuning**: New `embedding.text_source`, `embedding.max_input_tokens`, `retrieval.hotness_alpha`, and `retrieval.score_propagation_alpha` controls.
- **API semantics**: Empty search queries rejected early; stricter `viking://` URI validation; standard error envelopes for processing/zip/HTTP errors.
- **Docker experience**: Persistent state consolidated under `/app/.openviking`; missing `ov.conf` returns 503 initialization guide instead of crashing.
- **Security**: Bot image tool sandboxed from host filesystem; health checks skip identity resolution without credentials; API key hashing is now an explicit separate switch.

### Upgrade Notes

- `encryption.api_key_hashing.enabled` must now be configured explicitly (defaults to `false`). If you relied on implicit hashing, add it to your config.
- OpenClaw plugin is remote-only (no local subprocess), runtime agent identity moved to peer metadata, `recallTokenBudget` → `recallMaxInjectedChars`.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.3.12...v0.3.13)

## v0.3.12 (2026-04-24)

### Highlights

- **New Integrations**: Added Azure DevOps support for Git hosting and larkoffice.com URL parsing for Feishu documents.
- **Security**: Overhauled API key management with security hardening, fixed account name exposure, and resolved a trusted-mode 500 regression in proxy role lookups.
- **Documentation**: Launched a VitePress-powered docs site with GitHub Pages deployment, added llms.txt support and a Copy Markdown button.
- **Bug Fixes**: Corrected Feishu config limit validation, SSH repository host recognition with userinfo, AGFS URI error mapping, and token counting for pending tool parts.
- **Developer Experience**: Added maintainer routing map to contributing docs and S3 key normalization encoding for RAGFS.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.3.10...v0.3.12)

## v0.3.10 (2026-04-23)

### Highlights

- Added Codex, Kimi, and GLM VLM providers, plus `vlm.timeout` for per-request HTTP timeouts.
- Added VikingDB `volcengine.api_key` data-plane mode for accessing pre-created cloud VikingDB collections and indexes with an API key.
- Added `write(mode="create")` for creating new text resource files and automatically refreshing related semantics and vectors.
- Added ClawHub publishing, an interactive setup wizard, and `OPENCLAW_STATE_DIR` support for the OpenClaw plugin.
- Added a SQLite backend for QueueFS with persisted queues, ack support, and stale processing message recovery.
- Added Locomo / VikingBot evaluation preflight checks and result validation.

### Improvements

- Adjusted the default `recallTokenBudget` and `recallMaxContentChars` to reduce the risk of overlong OpenClaw auto-recall context injection.
- `ov add-memory` now returns `OK` for asynchronous commit workflows instead of implying the background task has already finished.
- `ov chat` now reads authentication from `ovcli.conf` and sends the required request headers.
- The OpenClaw plugin now aligns remote connection behavior, auth, namespace, and `peer_id` handling with the server multi-tenant model.

### Fixes

- Fixed Bot API channel auth checks, startup port preflight checks, and installed-version reporting.
- Fixed orphan `toolResult` errors caused by incompatible OpenClaw tool-call message formats.
- Fixed console `add_resource` target fields, repo target URIs, filesystem `mkdir`, and the reindex maintenance route.
- Fixed Windows `.bat` environment read/write, shell escaping, `ov.conf` validation, and hardcoded paths.
- Fixed LiteLLM `cache_control` 400 errors for Gemini + tools and added support for OpenAI reasoning model families.
- Fixed S3FS directory mtime stability, Rust native build environment pollution, and SQLite database extension parsing.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.3.9...v0.3.10)

## v0.3.9 (2026-04-18)

### Highlights

- **Memory**: Shipped Memory V2 as the new default, including a full test suite, session row migration, and a fix for file lock conflicts in concurrent scenarios.
- **OpenClaw**: Refactored context partitioning into Instruction/Archive/Session layers, unified `ov_import` and `ov_search` in the plugin, and extended Phase 2 commit wait timeout.
- **Bot & MCP**: Ported MCP client support from HKUDS/nanobot v0.1.5, added per-channel OpenViking config disable, and fixed heartbeat reliability.
- **Search & Retrieval**: Optimized large-directory search by skipping redundant scope checks, fixed sparse embedder async initialization, and added rerank extra-headers support.
- **Setup & Onboarding**: Introduced an interactive `openviking-server init` wizard for local Ollama deployment and added a default file/dir ignore config for `ovcli.conf`.
- **Infrastructure**: Added a metric system, updated the default Doubao embedding model, raised the Rust toolchain for RAGFS Docker builds, and split the parser layer into accessor and parser sublayers.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.3.8...v0.3.9)

## v0.3.8 (2026-04-15)

### Memory V2 Spotlight

Memory V2 is now the default memory pipeline, featuring a redesigned format, refactored extraction and dedup flow, and improved long-term memory quality.

### Highlights

- Memory V2 by default with improved format and extraction pipeline.
- Local deployment and setup experience enhancements (`openviking-server init`).
- Plugin and agent ecosystem improvements (Codex, OpenClaw, OpenCode examples).
- Config and deployment improvements (S3 batch delete toggle, OpenRouter `extra_headers`).
- Performance and reliability improvements across memory, session, and storage layers.

### Upgrade Notes

- If you frequently upload directories through the CLI, consider setting `upload.ignore_dirs` in `ovcli.conf` to reduce noisy uploads.
- Legacy memory v1 has been removed; memory extraction now uses v2 only.
- `ov init` / `ov doctor` → `openviking-server init` / `openviking-server doctor`.
- OpenRouter/compatible rerank/VLM providers can use `extra_headers` for required headers.
- S3-compatible services with batch-delete quirks: enable `storage.agfs.s3.disable_batch_delete`.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.3.5...v0.3.8)

## v0.3.5 (2026-04-10)

### Highlights

- **Storage**: Added a `disable_batch_delete` option to S3FS for OSS compatibility and improved RAGFS path scope fallback to prefix filters.
- **Session & Memory**: Fixed auto-creation of missing sessions on first message add and resolved a Memory V2 config initialization ordering issue.
- **Bot**: Fixed multi-user memory commits, response language handling, and ensured `afterTurn` stores messages with correct roles while skipping heartbeat entries.
- **Security & CI**: Removed a leaked token from settings.py, sanitized internal error details in bot proxy responses, and streamlined CI with a conditional OS matrix.
- **Developer Experience**: Added scenario-based API tests and exposed re-enqueue counts in queue status for easier debugging.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.3.4...v0.3.5)

## v0.3.4 (2026-04-09)

### Highlights

- OpenClaw plugin defaults adjusted (`recallPreferAbstract` and `ingestReplyAssist` now `false`); eval scripts and recall query sanitization added.
- Memory and session runtime stability improved: request-scoped write waits, PID lock recovery, orphan compressor refs, async contention fixes.
- Security tightened: SSRF protection for HTTP resource imports, localhost-only trusted mode without API key, configurable embedding circuit breaker.
- Ecosystem expansion: Volcengine Vector DB STS Token, MiniMax-M2.7 provider, Lua parser, bot channel mention.
- CI/Docker: auto-update `main` on release, Docker Hub push, Gemini optional dependency in image.

### Upgrade Notes

- OpenClaw `recallPreferAbstract` and `ingestReplyAssist` now default to `false` — enable explicitly if needed.
- HTTP resource imports now enforce private-network SSRF protection by default.
- Trusted mode without API key is restricted to localhost only.
- Write interface now uses request-scoped wait — review external orchestration timing dependencies.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.3.3...v0.3.4)

## v0.3.3 (2026-04-03)

### Highlights

- RAG benchmark evaluation framework added; OpenClaw LoCoMo eval scripts; content write API.
- OpenClaw plugin: architecture docs, installer no longer overwrites `gateway.mode`, e2e healthcheck tool, bypass session patterns, fault isolation from OpenViking.
- Test coverage: OpenClaw plugin unit tests, e2e tests, oc2ov integration tests and CI.
- Session creation now supports specifying `session_id`; CLI chat endpoint priority and `grep --exclude-uri/-x` enhanced.
- Security: task API ownership leak fix, unified stale lock handling, ZIP encoding fix, embedder dimension passthrough.

### Upgrade Notes

- OpenClaw installer no longer writes `gateway.mode` — manage explicitly after upgrade.
- `--with-bot` failures now return error codes; scripts relying on "fail-but-continue" need adjustment.
- OpenAI Dense Embedder now correctly passes custom dimension to `embed()`.
- Cross-subtree retrieval via tags metadata was added then reverted in this release window — not a final capability.
- `litellm` dependency updated to `>=1.0.0,<1.83.1`.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.3.2...v0.3.3)

## v0.3.2 (2026-04-01)

### Highlights

- **Docker**: Added VikingBot and Console services to Docker setup; updated examples to use latest image tags.
- **OpenClaw Plugin**: Added session-pattern guard for ingest reply assist; unified test directory structure.
- **VLM**: Rolled back ResponseAPI to Chat Completions while preserving tool call support.
- **Reliability**: Fixed HTTPX SOCKS5 proxy crash; improved PyPI mirror fallback in installer; skipped FUSE-incompatible filesystem tests on Windows.
- **Docs**: Added OVPack guide in Chinese and English; reorganized observability documentation; retired legacy integration examples.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.3.1...v0.3.2)

## v0.3.1 (2026-03-31)

### Highlights

- **Language Support**: Added PHP tree-sitter AST parsing.
- **Storage**: Introduced auto language detection for semantic summary generation; fixed parent URI compatibility with legacy records.
- **CI**: Expanded API test coverage to 5 platforms; switched to native per-arch Docker image builds; refreshed uv.lock for release.
- **Configuration**: Added configurable prompt template directories; unified archive context handling in session management.
- **OpenClaw Plugin**: Simplified install flow, hardened helpers, and preserved existing `ov.conf` on auto install.
- **Memory**: Applied memory optimization improvements.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.2.14...v0.3.1)

## v0.2.14 (2026-03-30)

### Highlights

- Multi-tenant identity management: CLI tenant defaults and overrides, `agent-only` memory scope, multi-tenant usage guide.
- Parsing: image OCR text extraction, `.cc` file recognition, duplicate title filename conflict fix, upload-id based HTTP upload flow.
- OpenClaw plugin: unified installer/upgrade flow, default latest Git tag install, session API and context pipeline refactoring, Windows/compaction/subprocess compatibility fixes.
- Bot and Feishu: proxy auth fix, Moonshot compatibility, Feishu interactive card markdown upgrade.
- Storage: queuefs embedding tracker hardening, vector store `parent_uri` removal, Docker doctor alignment, eval token metrics.

### Upgrade Notes

- Bot proxy endpoints `/bot/v1/chat` and `/bot/v1/chat/stream` now require authentication.
- HTTP file uploads should use the `temp_upload → temp_file_id` flow.
- OpenClaw plugin compaction delegation requires `openclaw >= v2026.3.22`.
- OpenClaw installer now defaults to latest Git tag — specify explicitly to pin versions.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.2.13...v0.2.14)

## v0.2.13 (2026-03-26)

### Highlights

- **Testing**: Added comprehensive unit tests for core utilities; improved API test infrastructure with dual-mode CI support.
- **Platform**: Fixed Windows engine wheel runtime packaging.
- **VLM**: Scoped LiteLLM thinking parameter to DashScope providers only.
- **OpenClaw Plugin**: Hardened duplicate registration guard.
- **Docs**: Added basic usage examples and Chinese documentation.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.2.12...v0.2.13)

## v0.2.12 (2026-03-25)

This patch release stabilizes the server shutdown sequence by properly handling `CancelledError`, rolls back a bot configuration regression, and tightens the Docker build by switching to `uv sync --locked` for reproducible dependency resolution.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.2.11...v0.2.12)

## v0.2.11 (2026-03-25)

### Highlights

- Model ecosystem: MiniMax embedding, Azure OpenAI embedding/VLM, GeminiDenseEmbedder, LiteLLM embedding and rerank, OpenAI-compatible rerank, Tavily search backend.
- Content pipeline: Whisper ASR for audio, Feishu/Lark document parser, configurable file vectorization strategy, search result provenance metadata.
- Server ops: `ov reindex`, `ov doctor`, Prometheus exporter, memory health stats API, trusted tenant header mode, Helm Chart.
- Multi-tenant security: file encryption, document encryption, tenant context passthrough fixes, ZIP Slip fix, trusted auth API key enforcement.
- Stability: vector score NaN/Inf clamping, async/concurrent session commit fixes, Windows stale lock and TUI fixes, proxy compatibility, API retry storm protection.

### Upgrade Notes

- `litellm` security policy: temporarily disabled, then restored as `<1.82.6` — pin your dependency version explicitly.
- Trusted auth mode now requires a server-side API key.
- Helm default values updated for Volcengine — review values config on chart upgrade.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.2.10...v0.2.11)

## v0.2.10 (2026-03-24)

### LiteLLM Security Hotfix

Emergency hotfix due to a supply chain security incident in the upstream `LiteLLM` dependency. All LiteLLM-related entry points are temporarily disabled.

### Action Required

1. Check if `litellm` is installed in your environment
2. Uninstall suspicious versions and rebuild virtual environments, images, or artifacts
3. Rotate API keys and credentials on machines that installed suspicious versions
4. Upgrade to this hotfix version

LiteLLM features will remain unavailable until a trusted upstream fix is released.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.2.9...v0.2.10)

## v0.2.9 (2026-03-19)

This release focuses on stability and developer experience improvements. Key fixes address RocksDB lock contention by sharing a single adapter across account backends, restore previously lost plugin bug fixes, and improve vector store incremental updates. New features include a bot debug mode with `/remember` command support, summary-based file embedding in the semantic pipeline, and comprehensive PR-Agent review rules for CI. Documentation also received Docker Compose and Mac port forwarding guidance.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.2.8...v0.2.9)

## v0.2.8 (2026-03-19)

### Highlights

- OpenClaw plugin upgraded to 2.0 (context engine), OpenCode memory plugin added, multi-agent memory isolation via peer metadata.
- Memory cold-storage archival with hotness scoring, chunked vectorization for long memories, `used()` tracking interface.
- Rerank integration in hierarchical retrieval, RetrievalObserver for quality metrics.
- Resource watch scheduling, reindex endpoint, legacy `.doc`/`.xls` parser support, path locking and crash recovery.
- Request-level trace metrics, memory extract telemetry breakdown, OpenAI VLM streaming, `<think>` tag cleanup.
- Cross-platform fixes (Windows zip, Rust CLI), AGFS Makefile refactor, CPU-variant vectordb engine, Python 3.14 wheel support.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.2.6...v0.2.8)

## v0.2.6 (2026-03-11)

### Highlights

- CLI UX: `ov chat` with `rustyline` line editing, Markdown rendering, chat history.
- Async capabilities: session commit with `wait` parameter, configurable worker count.
- New OpenViking Console web UI for debugging and API exploration.
- Bot enhancements: eval support, `add-resource` tool, Feishu progress notifications.
- OpenClaw memory plugin major upgrade: npm install, consolidated installer, stability fixes.
- Platform: Linux ARM support, Windows UTF-8 BOM fix, CI runner OS pinning.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.2.5...v0.2.6)

## v0.2.5 (2026-03-06)

### Highlights

- **PDF & Parsing**: Font-based heading detection and bookmark extraction as structured markdown headings; `add_resource` now supports index control with refactored embedding logic and correctly handles ZIP-based container formats.
- **Session & Memory**: `add_message()` adds `parts` parameter support; semantic indexing triggered for parent directories after memory extraction.
- **URI Handling**: Short-format `VikingURI` support, `git@` SSH URL format in the CLI, and GitHub `tree/<ref>` URL for code repository import.
- **Bot & Integrations**: VikingBot refactored with new evaluation module, Feishu multi-user and channel enhancements, OpenAPI standardization; Telegram crash fix for Claude.
- **Infrastructure**: `agfs` gains ripgrep-based grep acceleration and async grep support with optional binding client mode; automated PR review workflows using Doubao model with severity classification.
- **Installation**: curl-based installation works correctly on Ubuntu/Debian without triggering system protection errors; Rust compile fixed for `uv pip install -e .`.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.2.3...v0.2.5)

## v0.2.3 (2026-03-03)

### Breaking Change

Datasets and indexes generated by previous versions are incompatible with this release and cannot be reused. A full rebuild is required after upgrading to avoid retrieval anomalies, inconsistent filtering, or runtime errors. Stop the service, remove your workspace directory (`rm -rf ./your-openviking-workspace`), then restart with `openviking-server`.

This release delivers CLI optimizations including `glob -n` flag support and `cmd echo`, alongside README updates for both English and Chinese documentation.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.2.2...v0.2.3)

## v0.2.2 (2026-03-03)

### Breaking Change

Before upgrading, stop the VikingDB Server and clear your workspace directory. Indexes from prior versions are not forward-compatible with this release.

This release adds C# AST extractor support for code parsing, fixes multi-tenant filtering, normalizes OpenViking memory target paths, and improves git repository detection with `git@` SSH URL support. The `agfs` dependency libraries and binaries are now pre-compiled, eliminating the need for a build step at install time. Documentation adds Qwen model usage instructions.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.2.1...v0.2.2)

## v0.2.1 (2026-02-28)

### Highlights

- **Multi-tenancy**: Foundational multi-tenancy support at the API layer for isolated multi-user/team usage.
- **Cloud-Native**: Cloud-native VikingDB support, improved cloud deployment docs and Docker CI.
- **OpenClaw/OpenCode**: Official `openclaw-openviking-plugin` installation, `opencode` plugin introduction.
- **Storage**: Vector database interface refactored, AGFS binding client, AST code skeleton extraction, private GitLab domain support.
- **CLI**: `ov` command wrapper, `add-resource` enhancements, `ovcli.conf` timeout support, `--version` flag.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.1.18...v0.2.1)

## cli@0.2.0 (2026-02-27)

Updated CLI binary release with cross-platform support for macOS and Linux, aligned with the v0.1.18 feature set including the Rust-based implementation and expanded file parser capabilities.

[Full Changelog](https://github.com/volcengine/OpenViking/releases/tag/cli%400.2.0)

## v0.1.18 (2026-02-23)

This release brings major new capabilities to OpenViking. A high-performance Rust CLI is introduced alongside a terminal UI for filesystem navigation. File parsing is significantly expanded with support for Word, PowerPoint, Excel, EPub, and ZIP formats. Multi-provider support is added for embedding and VLM backends. Memory handling is redesigned with conflict-aware deduplication and a new extraction flow.

### Highlights

- **Rust CLI**: New blazing-fast CLI implementation.
- **File Parsers**: Word, PowerPoint, Excel, EPub, ZIP support via markitdown-inspired parsers.
- **TUI**: Basic terminal UI for filesystem navigation (`ov tui`).
- **Multi-provider**: Support for multiple embedding and VLM providers.
- **Memory**: Redesigned extraction and deduplication flow with conflict awareness.
- **Skills**: New memory, resource, and search skills; improved skill search ranking.
- **Directory Parsing**: Added directory-level parsing support.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.1.17...v0.1.18)

## cli@0.1.0 (2026-02-14)

Initial CLI binary release with cross-platform support for macOS and Linux, providing a standalone distributable for OpenViking server management and resource operations.

[Full Changelog](https://github.com/volcengine/OpenViking/releases/tag/cli%400.1.0)

## v0.1.17 (2026-02-14)

A stability-focused patch release. Reverts dynamic project name configuration in VectorDB due to instability, fixes CI workspace cleanup, and resolves a tree URI output error with added validation of `ov.conf` on startup.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.1.16...v0.1.17)

## v0.1.16 (2026-02-13)

A focused bug-fix and improvement release. Fixes VectorDB connectivity issues and a server conflict between uvloop and nest_asyncio. Temporary URIs are now human-readable, resource add timeouts are enlarged, and dynamic project name configuration is introduced for VectorDB and Volcengine backends.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.1.15...v0.1.16)

## v0.1.15 (2026-02-13)

This release focuses on architectural refactoring and reliability improvements. The HTTP client is split into distinct embedded and HTTP modes for cleaner separation of concerns. The CLI launch speed is improved through directory restructuring. VectorDB timestamp and collection creation bugs are resolved.

### Highlights

- **Refactor**: HTTP client split into embedded and HTTP modes; QueueManager decoupled from VikingDBManager.
- **CLI**: Faster launch speed; improved `ls` and `tree` output.
- **VectorDB**: Fixed timestamp format and collection creation issues.
- **Parser**: Support for repository branch and commit refs.
- **OpenClaw**: Initial memory output language pipeline adaptation.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.1.14...v0.1.15)

## v0.1.14 (2026-02-12)

A major infrastructure release. An HTTP server and Python HTTP client are introduced, enabling remote access to OpenViking services. The OpenClaw skill adds MCP integration support. Directory pre-scan validation, DAG-triggered embedding, and parallel resource addition improve performance and reliability.

### Highlights

- **HTTP Server**: New server mode with Python HTTP client for remote access.
- **OpenClaw Skill**: MCP integration for OpenViking.
- **CLI**: Full Bash CLI framework with comprehensive command implementation.
- **Embedding**: DAG-triggered embedding and parallel add support.
- **Directory Scan**: Pre-scan validation module added.
- **Config**: Default configuration directory set to `~/.openviking`.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.1.12...v0.1.14)

## v0.1.12 (2026-02-09)

This release improves search quality, storage reliability, and code maintainability. Sparse logit alpha search is added for enhanced retrieval. Query embeddings are reused in the hierarchical retriever for better performance. Native VikingDB deployment is now supported. A critical Zip Slip path traversal vulnerability (CWE-22) is patched.

### Highlights

- **Search**: Sparse logit alpha support and optimized query embedding reuse.
- **VikingDB**: Native deployment support.
- **Security**: Zip Slip path traversal fix (CWE-22).
- **Refactor**: Unified async execution utilities; restructured S3 configuration.
- **MCP**: Query support added and validated with Kimi.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.1.11...v0.1.12)

## v0.1.11 (2026-02-05)

Adds support for ingesting small GitHub code repositories, enabling OpenViking to index and search public codebases directly.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.1.10...v0.1.11)

## v0.1.10 (2026-02-05)

Patch release fixing a compilation error and resolving a Windows binary release packaging issue.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.1.9...v0.1.10)

## v0.1.9 (2026-02-05)

The initial public release of OpenViking. This release establishes the core project structure with cross-platform support including Linux and Intel Mac. It introduces the service layer architecture, separating embedding and VLM backends into configurable providers. Memory deduplication is improved and retrieval recursion bugs are fixed. Python 3.13 compatibility, S3FS support, and usage examples for chat and memory workflows are included.

### Highlights

- **Initial Release**: Core OpenViking server, client, and CLI foundation.
- **Providers**: Configurable embedding and VLM backends with provider abstraction.
- **Architecture**: Service layer extracted from async client; ObserverService separated from DebugService.
- **Platform**: Linux compile support, Intel Mac compatibility, Python 3.13 support.
- **Memory**: Simplified deduplication logic and fixed retrieval recursion bug.
- **Examples**: Chat and chat-with-memory usage examples added.

[Full Changelog](https://github.com/volcengine/OpenViking/releases/tag/v0.1.9)
