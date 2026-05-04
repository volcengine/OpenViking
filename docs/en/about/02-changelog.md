# Changelog

All notable changes to OpenViking will be documented in this file.
This changelog is automatically generated from [GitHub Releases](https://github.com/volcengine/OpenViking/releases).

## v0.3.14 (2026-04-30)

### Highlights

- **Observability**: OTLP export now supports custom `headers` for traces, logs, and metrics, enabling direct connection to backends that require extra auth or gRPC metadata.
- **Upload**: Local directory scans and uploads now respect root and nested `.gitignore` rules, reducing noise from build artifacts and temp files.
- **Search**: `search` and `find` now accept multiple target URIs for cross-directory and cross-repo retrieval.
- **Multi-tenant**: OpenClaw plugin clarifies `agent_prefix` as prefix-only; OpenCode memory plugin adds tenant header forwarding.
- **Admin**: New agent namespace discovery API, CLI command, and docs for listing existing agent namespaces under an account.

### Upgrade Notes

- OTLP backends requiring extra auth can now use `headers` across all three exporter types (gRPC metadata in gRPC mode, HTTP headers in HTTP mode).
- Local directory uploads will now filter files per `.gitignore` by default — previously imported temp/generated files may be excluded after upgrade.
- OpenClaw plugin `agent_prefix` is now prefix-only and no longer treated as a full agent identifier; docs migrate `agentId` → `agent_prefix`.

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
- OpenClaw plugin is remote-only (no local subprocess), `agentId` → `agent_prefix`, `recallTokenBudget` → `recallMaxInjectedChars`.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.3.12...v0.3.13)

## v0.3.12 (2026-04-24)

Focused on parser hardening, VitePress docs site launch, API key security improvements, and Azure DevOps git support.

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
- The OpenClaw plugin now aligns remote connection behavior, auth, namespace, and `role_id` handling with the server multi-tenant model.

### Fixes

- Fixed Bot API channel auth checks, startup port preflight checks, and installed-version reporting.
- Fixed orphan `toolResult` errors caused by incompatible OpenClaw tool-call message formats.
- Fixed console `add_resource` target fields, repo target URIs, filesystem `mkdir`, and the reindex maintenance route.
- Fixed Windows `.bat` environment read/write, shell escaping, `ov.conf` validation, and hardcoded paths.
- Fixed LiteLLM `cache_control` 400 errors for Gemini + tools and added support for OpenAI reasoning model families.
- Fixed S3FS directory mtime stability, Rust native build environment pollution, and SQLite database extension parsing.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.3.9...v0.3.10)

## v0.3.9 (2026-04-18)

Highlights include Memory V2 as default, MCP client support for bot, Codex memory plugin example, OpenClaw unified `ov_import`/`ov_search`, interactive setup wizard for local Ollama deployment, and metric system addition.

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
- Legacy memory v1 can be restored via `"memory": { "version": "v1" }` in `ov.conf`.
- `ov init` / `ov doctor` → `openviking-server init` / `openviking-server doctor`.
- OpenRouter/compatible rerank/VLM providers can use `extra_headers` for required headers.
- S3-compatible services with batch-delete quirks: enable `storage.agfs.s3.disable_batch_delete`.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.3.5...v0.3.8)

## v0.3.5 (2026-04-10)

Bug fixes for memory v2 lock retry, bot proxy error sanitization, session auto-creation, and embedding dimension adaptation. Added scenario-based API tests and OSS batch-delete compatibility.

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

Config-driven retry unification for VLM and embedding, OVPack guide, observability docs reorganization, Docker vikingbot/console addition, and OpenClaw session-pattern guard.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.3.1...v0.3.2)

## v0.3.1 (2026-03-31)

PHP tree-sitter support, multi-platform API tests, auto language detection for semantic summaries, configurable prompt template directories, and OpenClaw install hardening.

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

Unit tests for core utilities, LiteLLM thinking param scoped to DashScope, dual-mode CI for API tests, Windows engine wheel fix, OpenClaw duplicate registration guard.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.2.12...v0.2.13)

## v0.2.12 (2026-03-25)

Docker `uv sync --locked`, CancelledError handling during shutdown, bot config rollback.

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

Agent-level watch task isolation, summary-based file embedding, bot mode config and debug mode, RocksDB lock contention fix for shared adapter.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.2.8...v0.2.9)

## v0.2.8 (2026-03-19)

### Highlights

- OpenClaw plugin upgraded to 2.0 (context engine), OpenCode memory plugin added, multi-agent memory isolation via `agentId`.
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

PDF bookmark headings, GitHub tree/ref URL import, index control for `add_resource`, curl-based OpenClaw install, bot refactoring with new eval module, ripgrep-based grep acceleration.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.2.3...v0.2.5)

## v0.2.3 (2026-03-03)

### Breaking Change

After upgrading, datasets/indexes generated by historical versions are not compatible with the new version and cannot be reused directly. Please rebuild the datasets after upgrading (a full rebuild is recommended).

Stop the service → `rm -rf ./your-openviking-workspace` → restart the service with `openviking-server`.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.2.2...v0.2.3)

## v0.2.2 (2026-03-03)

### Breaking Change

This release includes a breaking change. Before upgrading, stop VikingDB Server and clear the workspace directory first.

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

OpenViking CLI v0.2.0 release with cross-platform binaries.

[Full Changelog](https://github.com/volcengine/OpenViking/releases/tag/cli%400.2.0)

## v0.1.18 (2026-02-23)

Rust CLI implementation, markitdown-inspired parsers (Word, PowerPoint, Excel, EPub, ZIP), multi-provider support, TUI filesystem navigator, memory redesign with conflict-aware dedup.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.1.17...v0.1.18)

## cli@0.1.0 (2026-02-14)

OpenViking CLI v0.1.0 initial release with cross-platform binaries.

[Full Changelog](https://github.com/volcengine/OpenViking/releases/tag/cli%400.1.0)

## v0.1.17 (2026-02-14)

Reverted dynamic `project_name` config, CI workspace cleanup fix, tree URI output validation.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.1.16...v0.1.17)

## v0.1.16 (2026-02-13)

VectorDB fixes, readable temp URIs, dynamic `project_name` config for VectorDB/volcengine, uvloop conflict fix.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.1.15...v0.1.16)

## v0.1.15 (2026-02-13)

Server/CLI mode now available. HTTP client refactor, QueueManager decoupling, CLI launch speed optimization, memory output language pipeline, parser branch/commit ref support.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.1.14...v0.1.15)

## v0.1.14 (2026-02-12)

HTTP Server and Python HTTP Client, OpenClaw MCP skill, directory pre-scan validation, DAG-triggered embedding, Bash CLI framework, parallel add support.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.1.12...v0.1.14)

## v0.1.12 (2026-02-09)

Sparse logit alpha search, S3 config refactor, async execution unification, native VikingDB deployment, Zip Slip prevention, MCP query support.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.1.11...v0.1.12)

## v0.1.11 (2026-02-05)

Support for small GitHub code repos.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.1.10...v0.1.11)

## v0.1.10 (2026-02-05)

Compilation fixes and Windows release fix.

[Full Changelog](https://github.com/volcengine/OpenViking/compare/v0.1.9...v0.1.10)

## v0.1.9 (2026-02-05)

Initial public release. GitHub templates, multi-provider embedding/VLM support, Intel Mac support, Linux compilation, Python 3.13 support, chat examples, logging standardization.

[Full Changelog](https://github.com/volcengine/OpenViking/releases/tag/v0.1.9)
