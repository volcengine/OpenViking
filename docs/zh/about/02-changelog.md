# 更新日志

OpenViking 的所有重要变更都将记录在此文件中。
此更新日志从 [GitHub Releases](https://github.com/volcengine/OpenViking/releases) 自动生成。

## v0.3.14 (2026-04-30)

### 重点更新

- **可观测性**：OTLP 导出支持自定义 `headers`，覆盖 traces、logs、metrics 三条链路，便于直连需要额外鉴权头或 gRPC metadata 的观测后端。
- **上传**：本地目录扫描和上传现在遵循根目录及子目录中的 `.gitignore` 规则，减少构建产物和临时文件被误导入。
- **检索**：`search` / `find` 支持一次传入多个 target URI，适合跨目录、跨仓库范围检索。
- **多租户**：OpenClaw 插件明确 `agent_prefix` 仅作为前缀使用；OpenCode memory plugin 补上 tenant headers 透传。
- **管理**：新增 agent namespace 发现能力，服务端 API、CLI 和文档同步支持列出指定 account 下已有的 agent namespace。

### 升级说明

- OTLP 后端接入可通过 `headers` 统一配置鉴权信息（gRPC 模式为 metadata，HTTP 模式为请求头）。
- 本地目录上传默认遵循 `.gitignore` 规则，此前被导入的临时/生成文件升级后可能被自动过滤。
- OpenClaw 插件 `agent_prefix` 仅表示前缀，文档中 `agentId` 已统一迁移为 `agent_prefix`。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.3.13...v0.3.14)

## v0.3.13 (2026-04-29)

### 重点更新

- **内置 MCP 端点**：`openviking-server` 在同一进程、同一端口暴露 `/mcp`，复用 REST API 的 API-Key 鉴权，提供 `search`、`read`、`list`、`store`、`add_resource`、`grep`、`glob`、`forget`、`health` 9 个工具。
- **用户级隐私配置**：新增 `/api/v1/privacy-configs` API 和 `openviking privacy` CLI，按 `category + target_key` 保存、轮换、回滚 skill 等敏感配置。
- **可观测性升级**：统一 `server.observability` 配置，支持 Prometheus `/metrics` 和 OpenTelemetry metrics/traces/logs 导出。
- **检索调优**：新增 `embedding.text_source`、`embedding.max_input_tokens`、`retrieval.hotness_alpha`、`retrieval.score_propagation_alpha` 等配置。
- **API 语义收敛**：搜索空 query 提前拒绝；公开 `viking://` URI 校验更严格；错误统一进入标准 error envelope。
- **Docker 体验**：持久化状态收敛到 `/app/.openviking`；缺少 `ov.conf` 时容器存活并返回 503 初始化指引。
- **安全**：bot 图片工具禁止读取沙箱外文件；health check 无凭证时跳过身份解析；API key 字段哈希拆分为独立开关。

### 升级说明

- `encryption.api_key_hashing.enabled` 需要显式配置（默认 `false`）。如依赖旧的隐式哈希行为，需手动开启。
- OpenClaw 插件仅保留远程模式，不再启动本地子进程；`agentId` → `agent_prefix`，`recallTokenBudget` → `recallMaxInjectedChars`。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.3.12...v0.3.13)

## v0.3.12 (2026-04-24)

### 重点更新

- **新集成**：新增 Azure DevOps Git 托管支持和 larkoffice.com 飞书文档 URL 解析。
- **安全**：API Key 管理重构与安全增强，修复 account name 暴露问题，解决 trusted-mode proxy role 查询 500 回退。
- **文档**：上线 VitePress 文档站并部署到 GitHub Pages，新增 llms.txt 支持和 Copy Markdown 按钮。
- **Bug 修复**：修正飞书 config 限制校验、SSH 仓库 host 的 userinfo 识别、AGFS URI 错误映射、pending tool parts token 计数。
- **开发者体验**：新增 maintainer routing map 贡献文档，RAGFS 新增 S3 key normalization encoding。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.3.10...v0.3.12)

## v0.3.10 (2026-04-23)

### 重点更新

- 新增 Codex、Kimi、GLM VLM provider，并支持 `vlm.timeout` 配置。
- 新增 VikingDB `volcengine.api_key` 数据面模式，可通过 API Key 访问已创建好的云上 VikingDB collection/index。
- `write()` 新增 `mode="create"`，支持创建新的文本类 resource 文件，并自动触发语义与向量刷新。
- OpenClaw 插件新增 ClawHub 发布、交互式 setup 向导和 `OPENCLAW_STATE_DIR` 支持。
- QueueFS 新增 SQLite backend，支持持久化队列、ack 和 stale processing 消息恢复。
- Locomo / VikingBot 评测链路新增 preflight 检查和结果校验。

### 体验与兼容性改进

- 调整 `recallTokenBudget` 和 `recallMaxContentChars` 默认值，降低 OpenClaw 自动召回注入过长上下文的风险。
- `ov add-memory` 在异步 commit 场景下返回 `OK`，避免误判后台任务仍在执行时的状态。
- `ov chat` 会从 `ovcli.conf` 读取鉴权配置并自动发送必要请求头。
- OpenClaw 插件默认远端连接行为、鉴权、namespace 和 `role_id` 处理更贴合服务端多租户模型。

### 修复

- 修复 Bot API channel 鉴权检查、启动前端口检查和已安装版本上报。
- 修复 OpenClaw 工具调用消息格式不兼容导致的孤儿 `toolResult`。
- 修复 console `add_resource` target 字段、repo target URI、filesystem `mkdir`、reindex maintenance route 等问题。
- 修复 Windows `.bat` 环境读写、shell escaping、`ov.conf` 校验和硬编码路径问题。
- 修复 Gemini + tools 场景下 LiteLLM `cache_control` 导致的 400 错误，并支持 OpenAI reasoning model family。
- 修复 S3FS 目录 mtime 稳定性、Rust native build 环境污染、SQLite 数据库扩展名解析等问题。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.3.9...v0.3.10)

## v0.3.9 (2026-04-18)

### 重点更新

- **Memory**：Memory V2 设为默认，包含完整测试套件、session 行迁移，修复并发场景下的文件锁冲突。
- **OpenClaw**：上下文分区重构为 Instruction/Archive/Session 层，插件统一 `ov_import` 和 `ov_search`，延长 Phase 2 commit 等待超时。
- **Bot & MCP**：从 HKUDS/nanobot v0.1.5 移植 MCP client 支持，新增单 channel 禁用 OpenViking 配置，修复心跳可靠性。
- **检索与搜索**：通过跳过冗余 scope 检查优化大目录搜索性能，修复 sparse embedder 异步初始化，新增 rerank extra-headers 支持。
- **部署与上手**：新增交互式 `openviking-server init` 向导支持本地 Ollama 部署，`ovcli.conf` 新增默认文件/目录忽略配置。
- **基础设施**：新增度量系统，更新默认 Doubao embedding 模型，提升 RAGFS Docker 构建的 Rust toolchain，解析器拆分为 accessor 和 parser 两层。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.3.8...v0.3.9)

## v0.3.8 (2026-04-15)

### Memory V2 专题

Memory V2 现在作为默认记忆管线，采用全新格式、重构的抽取与去重流程，长期记忆质量显著提升。

### 重点更新

- Memory V2 默认开启，格式与抽取管线全面重构。
- 本地部署与初始化体验增强（`openviking-server init`）。
- 插件与 Agent 生态增强（Codex、OpenClaw、OpenCode 示例）。
- 配置与部署体验改进（S3 批量删除开关、OpenRouter `extra_headers`）。
- Memory、Session、存储层性能与稳定性改进。

### 升级提示

- 如果你经常通过 CLI 导入目录资源，建议在 `ovcli.conf` 中配置 `upload.ignore_dirs`。
- 旧行为可通过 `"memory": { "version": "v1" }` 回退。
- `ov init` / `ov doctor` 请改用 `openviking-server init` / `openviking-server doctor`。
- OpenRouter 或其他 OpenAI 兼容 rerank/VLM 服务可通过 `extra_headers` 注入平台要求的 Header。
- S3 兼容实现批量删除有兼容问题时，可开启 `storage.agfs.s3.disable_batch_delete`。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.3.5...v0.3.8)

## v0.3.5 (2026-04-10)

### 重点更新

- **存储**：S3FS 新增 `disable_batch_delete` 选项兼容 OSS，改进 RAGFS 路径 scope 回退到 prefix filters。
- **Session & Memory**：修复首条消息时缺失 session 的自动创建，解决 Memory V2 config 初始化顺序问题。
- **Bot**：修复多用户 memory commit、响应语言处理，确保 `afterTurn` 以正确角色存储消息并跳过心跳条目。
- **安全 & CI**：移除 settings.py 中泄露的 token，bot proxy 响应中清除内部错误细节，CI 优化为条件 OS 矩阵。
- **开发者体验**：新增场景化 API 测试，queue status 中暴露 re-enqueue 计数便于调试。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.3.4...v0.3.5)

## v0.3.4 (2026-04-09)

### 版本亮点

- OpenClaw 插件默认配置调整（`recallPreferAbstract` 和 `ingestReplyAssist` 默认 `false`），新增 eval 脚本和 recall 查询清洗。
- Memory 和会话运行时稳定性增强：request-scoped 写等待、PID lock 回收、孤儿 compressor 引用、async contention 修复。
- 安全边界收紧：HTTP 资源导入 SSRF 防护、无 API key 时 trusted mode 仅允许 localhost、可配置 embedding circuit breaker。
- 生态扩展：Volcengine Vector DB STS Token、MiniMax-M2.7 provider、Lua parser、Bot channel mention。
- CI/Docker：发布时自动更新 `main` 并 Docker Hub push，Gemini optional dependency 纳入镜像。

### 升级说明

- OpenClaw `recallPreferAbstract` 和 `ingestReplyAssist` 现在默认 `false`，如需旧行为需显式配置。
- HTTP 资源导入默认启用私网 SSRF 防护。
- 无 API key 的 trusted mode 仅允许 localhost 访问。
- 写接口引入 request-scoped wait，如有外部编排依赖旧时序需复核。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.3.3...v0.3.4)

## v0.3.3 (2026-04-03)

### 重点更新

- 新增 RAG benchmark 评测框架、OpenClaw LoCoMo eval 脚本、内容写入接口。
- OpenClaw 插件：架构文档补充、安装器不再覆盖 `gateway.mode`、端到端 healthcheck 工具、bypass session patterns、OpenViking 故障隔离。
- 测试覆盖：OpenClaw 插件单测、e2e 测试、oc2ov 集成测试与 CI。
- Session 支持指定 `session_id` 创建；CLI 聊天端点优先级与 `grep --exclude-uri/-x` 增强。
- 安全：任务 API ownership 泄露修复、stale lock 统一处理、ZIP 编码修复、embedder 维度透传。

### 升级说明

- OpenClaw 安装器不再写入 `gateway.mode`，升级后需显式管理。
- `--with-bot` 失败时返回错误码，依赖"失败但继续"行为的脚本需调整。
- OpenAI Dense Embedder 自定义维度现正确传入 `embed()`。
- 基于 tags metadata 的 cross-subtree retrieval 已在本版本窗口内回滚，非最终能力。
- `litellm` 依赖更新为 `>=1.0.0,<1.83.1`。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.3.2...v0.3.3)

## v0.3.2 (2026-04-01)

### 重点更新

- **Docker**：新增 VikingBot 和 Console 服务到 Docker 配置；示例更新为使用 latest 镜像标签。
- **OpenClaw 插件**：新增 ingest reply assist 的 session-pattern guard；统一测试目录结构。
- **VLM**：回滚 ResponseAPI 到 Chat Completions 同时保留 tool call 支持。
- **稳定性**：修复 HTTPX SOCKS5 代理导致的崩溃；改进安装器 PyPI 镜像回退；Windows 上跳过 FUSE 不兼容的文件系统测试。
- **文档**：新增中英文 OVPack 指南；重组可观测性文档；下线过时的集成示例。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.3.1...v0.3.2)

## v0.3.1 (2026-03-31)

### 重点更新

- **语言支持**：新增 PHP tree-sitter AST 解析。
- **存储**：语义摘要生成引入自动语言检测；修复 legacy 记录的 parent URI 兼容性。
- **CI**：API 测试扩展到 5 个平台；切换为按架构原生构建 Docker 镜像；刷新 uv.lock 用于发布构建。
- **配置**：新增可配置 prompt 模板目录；统一 session 管理中的 archive context 处理。
- **OpenClaw 插件**：简化安装流程、加固辅助工具、自动安装时保留已有 `ov.conf`。
- **Memory**：应用 memory 优化改进。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.2.14...v0.3.1)

## v0.2.14 (2026-03-30)

### 重点更新

- 多租户与身份管理：CLI 租户身份默认值与覆盖、`agent-only` memory scope、多租户使用指南。
- 解析与导入：图片 OCR 文本提取、`.cc` 文件识别、重复标题文件名冲突修复、upload-id 方式 HTTP 上传。
- OpenClaw 插件：统一安装器/升级流程、默认按最新 Git tag 安装、session API 与 context pipeline 重构、Windows/compaction/子进程兼容性修复。
- Bot 与 Feishu：proxy 鉴权修复、Moonshot 兼容性改进、Feishu interactive card markdown 升级。
- 存储与运行时：queuefs embedding tracker 加固、vector store `parent_uri` 移除、Docker doctor 对齐、eval token 指标。

### 升级说明

- Bot proxy 接口 `/bot/v1/chat` 和 `/bot/v1/chat/stream` 已补齐鉴权。
- HTTP 导入推荐按 `temp_upload → temp_file_id` 方式接入。
- OpenClaw 插件 compaction delegation 要求 `openclaw >= v2026.3.22`。
- OpenClaw 安装器默认跟随最新 Git tag，如需固定版本可显式指定。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.2.13...v0.2.14)

## v0.2.13 (2026-03-26)

### 重点更新

- **测试**：新增核心工具的全面单元测试；改进 API 测试基础设施支持双模式 CI。
- **平台**：修复 Windows engine wheel 运行时打包。
- **VLM**：LiteLLM thinking 参数限定为 DashScope provider。
- **OpenClaw 插件**：加固重复注册 guard。
- **文档**：新增基础用法示例和中文文档。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.2.12...v0.2.13)

## v0.2.12 (2026-03-25)

此补丁版本通过正确处理 `CancelledError` 稳定了服务器 shutdown 序列，回滚了一个 bot 配置回退，并通过切换到 `uv sync --locked` 加强 Docker 构建的依赖一致性。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.2.11...v0.2.12)

## v0.2.11 (2026-03-25)

### 版本亮点

- 模型与检索生态扩展：MiniMax embedding、Azure OpenAI embedding/VLM、GeminiDenseEmbedder、LiteLLM embedding 和 rerank、OpenAI-compatible rerank、Tavily 搜索后端。
- 内容接入：Whisper ASR 音频解析、飞书/Lark 云文档解析器、可配置文件向量化策略、搜索结果 provenance 元数据。
- 服务端运维：`ov reindex`、`ov doctor`、Prometheus exporter、内存健康统计 API、可信租户头模式、Helm Chart。
- 多租户与安全：多租户文件加密和文档加密、租户上下文透传修复、ZIP Slip 修复、trusted auth API key 强制校验。
- 稳定性：向量检索 NaN/Inf 分数钳制、异步/并发 session commit 修复、Windows stale lock 和 TUI 修复、代理兼容、API 重试风暴保护。

### 升级提示

- `litellm` 安全策略调整：先临时禁用，后恢复为 `<1.82.6` 版本范围。建议显式锁定依赖版本。
- trusted auth 模式需同时配置服务端 API key。
- Helm 默认配置切换为 Volcengine 场景默认值，升级时建议重新审阅 values。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.2.10...v0.2.11)

## v0.2.10 (2026-03-24)

### LiteLLM 安全热修复

由于上游依赖 `LiteLLM` 出现公开供应链安全事件，本次热修复临时禁用所有 LiteLLM 相关入口。

### 建议操作

1. 检查运行环境中是否安装 `litellm`
2. 卸载可疑版本并重建虚拟环境、容器镜像或发布产物
3. 对近期安装过可疑版本的机器轮换 API Key 和相关凭证
4. 升级到本热修复版本

LiteLLM 相关能力会暂时不可用，直到上游给出可信的修复版本和完整事故说明。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.2.9...v0.2.10)

## v0.2.9 (2026-03-19)

此版本聚焦于稳定性和开发者体验改进。关键修复包括：通过在 account backend 间共享单一 adapter 解决 RocksDB 锁竞争、恢复之前合并中丢失的插件 bug fix、改善 vector store 增量更新。新功能包括 bot 调试模式和 `/remember` 命令、semantic pipeline 中基于 summary 的文件 embedding、CI 中全面的 PR-Agent 评审规则。文档新增 Docker Compose 和 Mac 端口转发指引。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.2.8...v0.2.9)

## v0.2.8 (2026-03-19)

### 重点更新

- OpenClaw 插件升级到 2.0（context engine），新增 OpenCode memory plugin，多智能体 memory isolation 基于 `agentId`。
- Memory 冷热分层 archival 和 hotness scoring、长记忆 chunked vectorization、`used()` 使用追踪接口。
- 分层检索集成 rerank、RetrievalObserver 检索质量观测。
- 资源 watch scheduling、reindex endpoint、legacy `.doc`/`.xls` 解析支持、path locking 和 crash recovery。
- 请求级 trace metrics、memory extract telemetry breakdown、OpenAI VLM streaming、`<think>` 标签自动清理。
- 跨平台修复（Windows zip、Rust CLI）、AGFS Makefile 重构、CPU variant vectordb engine、Python 3.14 wheel 支持。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.2.6...v0.2.8)

## v0.2.6 (2026-03-11)

### 重点更新

- CLI 体验：`ov chat` 基于 `rustyline` 行编辑、Markdown 渲染、聊天历史。
- 异步能力：session commit `wait` 参数、可配置 worker count。
- 新增 OpenViking Console Web 控制台，方便调试和 API 探索。
- Bot 增强：eval 能力、`add-resource` 工具、飞书进度通知。
- OpenClaw memory plugin 大幅升级：npm 安装、统一安装器、稳定性修复。
- 平台支持：Linux ARM、Windows UTF-8 BOM 修复、CI runner OS 固定。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.2.5...v0.2.6)

## v0.2.5 (2026-03-06)

### 重点更新

- **PDF & 解析**：基于字体的标题检测和书签提取为结构化 markdown 标题；`add_resource` 支持索引控制并重构 embedding 逻辑，正确处理 ZIP 容器格式。
- **Session & Memory**：`add_message()` 新增 `parts` 参数支持；memory 抽取后为父目录触发语义索引。
- **URI 处理**：短格式 `VikingURI` 支持、CLI 中 `git@` SSH URL 格式、GitHub `tree/<ref>` URL 代码仓库导入。
- **Bot & 集成**：VikingBot 重构包含新评测模块、飞书多用户和 channel 增强、OpenAPI 标准化；Telegram Claude 崩溃修复。
- **基础设施**：`agfs` 新增 ripgrep 加速的 grep 和 async grep，可选 binding client 模式；使用 Doubao 模型的自动 PR 评审工作流和严重度分级。
- **安装**：curl 方式安装在 Ubuntu/Debian 上不再触发系统保护错误；修复 `uv pip install -e .` 的 Rust 编译。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.2.3...v0.2.5)

## v0.2.3 (2026-03-03)

### Breaking Change

升级后，历史版本生成的 datasets/indexes 与新版本不兼容，无法直接复用。升级后需要全量重建数据集以避免检索异常、过滤结果不一致或运行时错误。停止服务，删除 workspace 目录（`rm -rf ./your-openviking-workspace`），然后用 `openviking-server` 重启。

此版本提供 CLI 优化，包括 `glob -n` 标志支持和 `cmd echo`，以及中英文 README 更新。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.2.2...v0.2.3)

## v0.2.2 (2026-03-03)

### Breaking Change

升级前请先停止 VikingDB Server 并清除 workspace 目录。旧版本的索引与此版本不向前兼容。

此版本新增 C# AST 提取器支持代码解析，修复多租户过滤，规范 OpenViking memory target paths，改进 `git@` SSH URL 的 git 仓库检测。`agfs` 依赖的 lib/bin 现在预编译提供，安装时无需构建步骤。文档新增千问模型使用说明。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.2.1...v0.2.2)

## v0.2.1 (2026-02-28)

### 重点更新

- **多租户**：API 层多租户基础能力，支持多用户/团队隔离使用。
- **云原生**：云原生 VikingDB 支持，完善云端部署文档和 Docker CI。
- **OpenClaw/OpenCode**：官方 `openclaw-openviking-plugin` 安装、`opencode` 插件引入。
- **存储**：向量数据库接口重构、AGFS binding client、AST 代码骨架提取、私有 GitLab 域名支持。
- **CLI**：`ov` 命令封装、`add-resource` 增强、`ovcli.conf` timeout 支持、`--version` 参数。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.1.18...v0.2.1)

## cli@0.2.0 (2026-02-27)

更新的 CLI 二进制发布，跨平台支持 macOS 和 Linux，与 v0.1.18 功能集对齐，包含 Rust 实现和扩展的文件解析器能力。

[完整变更记录](https://github.com/volcengine/OpenViking/releases/tag/cli%400.2.0)

## v0.1.18 (2026-02-23)

此版本为 OpenViking 带来重大新能力。引入高性能 Rust CLI 和终端文件系统浏览器 UI。文件解析大幅扩展，支持 Word、PowerPoint、Excel、EPub 和 ZIP 格式。新增多 provider 支持用于 embedding 和 VLM 后端。Memory 处理重新设计为具有冲突感知的去重和新抽取流程。

### 重点更新

- **Rust CLI**：全新高速 CLI 实现。
- **文件解析器**：通过 markitdown 风格解析器支持 Word、PowerPoint、Excel、EPub、ZIP。
- **TUI**：基础终端 UI 文件系统导航（`ov tui`）。
- **多 Provider**：支持多个 embedding 和 VLM provider。
- **Memory**：重新设计的抽取和去重流程，具备冲突感知能力。
- **Skills**：新增 memory、resource 和 search skills；改进 skill 搜索排序。
- **目录解析**：新增目录级解析支持。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.1.17...v0.1.18)

## cli@0.1.0 (2026-02-14)

初始 CLI 二进制发布，跨平台支持 macOS 和 Linux，提供独立的 OpenViking 服务管理和资源操作可执行文件。

[完整变更记录](https://github.com/volcengine/OpenViking/releases/tag/cli%400.1.0)

## v0.1.17 (2026-02-14)

稳定性修复版本。因不稳定性回滚了 VectorDB 中的动态 project name 配置，修复 CI workspace 清理，解决 tree URI 输出错误并增加启动时 `ov.conf` 校验。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.1.16...v0.1.17)

## v0.1.16 (2026-02-13)

聚焦 bug 修复与改进的版本。修复 VectorDB 连接问题和 uvloop 与 nest_asyncio 的服务器冲突。临时 URI 现在可读，resource add 超时增大，为 VectorDB 和 Volcengine 后端引入动态 project name 配置。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.1.15...v0.1.16)

## v0.1.15 (2026-02-13)

此版本聚焦架构重构和可靠性改进。HTTP 客户端拆分为独立的嵌入和 HTTP 模式以实现更清晰的关注点分离。通过目录重组提升 CLI 启动速度。解决 VectorDB timestamp 和 collection 创建 bug。

### 重点更新

- **重构**：HTTP 客户端拆分为嵌入和 HTTP 模式；QueueManager 从 VikingDBManager 解耦。
- **CLI**：更快的启动速度；改进 `ls` 和 `tree` 输出。
- **VectorDB**：修复 timestamp 格式和 collection 创建问题。
- **解析器**：支持仓库分支和 commit 引用。
- **OpenClaw**：初步适配 memory 输出语言管线。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.1.14...v0.1.15)

## v0.1.14 (2026-02-12)

重大基础设施版本。引入 HTTP Server 和 Python HTTP Client，实现 OpenViking 服务的远程访问。OpenClaw skill 新增 MCP 集成支持。目录预扫描校验、DAG 触发 embedding 和并行资源添加提升了性能和可靠性。

### 重点更新

- **HTTP Server**：新的服务模式，提供 Python HTTP Client 用于远程访问。
- **OpenClaw Skill**：OpenViking 的 MCP 集成。
- **CLI**：完整的 Bash CLI 框架和全面的命令实现。
- **Embedding**：DAG 触发 embedding 和并行 add 支持。
- **目录扫描**：新增预扫描校验模块。
- **配置**：默认配置目录设为 `~/.openviking`。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.1.12...v0.1.14)

## v0.1.12 (2026-02-09)

此版本改进搜索质量、存储可靠性和代码可维护性。新增 sparse logit alpha 搜索增强检索。在 hierarchical retriever 中复用查询 embedding 提升性能。支持原生 VikingDB 部署。修补了一个严重的 Zip Slip 路径穿越漏洞 (CWE-22)。

### 重点更新

- **搜索**：Sparse logit alpha 支持和优化的查询 embedding 复用。
- **VikingDB**：原生部署支持。
- **安全**：Zip Slip 路径穿越修复 (CWE-22)。
- **重构**：统一异步执行工具；重构 S3 配置。
- **MCP**：新增查询支持并通过 Kimi 验证。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.1.11...v0.1.12)

## v0.1.11 (2026-02-05)

新增对小型 GitHub 代码仓库的导入支持，使 OpenViking 能够直接索引和搜索公开代码库。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.1.10...v0.1.11)

## v0.1.10 (2026-02-05)

修复编译错误和 Windows 二进制发布打包问题的补丁版本。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.1.9...v0.1.10)

## v0.1.9 (2026-02-05)

OpenViking 的初始公开发布。此版本建立了核心项目结构，支持 Linux 和 Intel Mac 跨平台。引入服务层架构，将 embedding 和 VLM 后端分离为可配置的 provider。改进了 Memory 去重并修复了检索递归 bug。包含 Python 3.13 兼容性、S3FS 支持以及 chat 和 memory 工作流的使用示例。

### 重点更新

- **初始发布**：核心 OpenViking server、client 和 CLI 基础。
- **Provider**：可配置的 embedding 和 VLM 后端，provider 抽象层。
- **架构**：从 async client 中提取 Service 层；ObserverService 从 DebugService 分离。
- **平台**：Linux 编译支持、Intel Mac 兼容性、Python 3.13 支持。
- **Memory**：简化去重逻辑并修复检索递归 bug。
- **示例**：Chat 和 chat-with-memory 使用示例。

[完整变更记录](https://github.com/volcengine/OpenViking/releases/tag/v0.1.9)
