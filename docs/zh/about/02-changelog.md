# 更新日志

OpenViking 的所有重要变更都将记录在此文件中。
此更新日志从 [GitHub Releases](https://github.com/volcengine/OpenViking/releases) 自动生成。

## v0.3.14 (2026-04-30)

### 重点更新

- **可观测性增强**：OTLP 导出支持自定义 `headers`，覆盖 traces、logs、metrics 三条链路，便于直连需要额外鉴权头或 gRPC metadata 的观测后端。
- **上传体验优化**：本地目录扫描和上传现在遵循根目录及子目录中的 `.gitignore`，减少无关文件、构建产物和临时文件被误导入。
- **检索能力补强**：`search` / `find` 支持一次传入多个 target URI，适合跨目录、跨仓库范围检索。
- **多租户与插件**：OpenClaw 插件明确 `agent_prefix` 仅作为前缀使用；OpenCode memory plugin 补上 tenant headers 透传。
- **管理能力**：新增 agent namespace 发现能力，服务端、CLI 和文档同步支持列出指定 account 下已有的 agent namespace。

### 升级说明

- OTLP 后端接入可通过 `headers` 统一配置鉴权信息（gRPC 模式为 metadata，HTTP 模式为请求头）。
- 本地目录上传默认遵循 `.gitignore` 规则，此前被导入的临时文件可能被自动过滤。
- OpenClaw 插件 `agent_prefix` 仅表示前缀，文档中 `agentId` 已统一迁移为 `agent_prefix`。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.3.13...v0.3.14)

## v0.3.13 (2026-04-29)

### 重点更新

- **内置 MCP 端点**：`openviking-server` 在同一进程、同一端口暴露 `/mcp`，复用 REST API 的 API-Key 鉴权，提供 `search`、`read`、`list`、`store`、`add_resource`、`grep`、`glob`、`forget`、`health` 9 个工具。
- **用户级隐私配置**：新增 `/api/v1/privacy-configs` API 和 `openviking privacy` CLI，按 `category + target_key` 保存、轮换、回滚 skill 等敏感配置。
- **可观测性升级**：统一 `server.observability` 配置，支持 Prometheus `/metrics` 和 OpenTelemetry metrics/traces/logs 导出。
- **检索与 embedding 可调优**：新增 `embedding.text_source`、`embedding.max_input_tokens`、`retrieval.hotness_alpha`、`retrieval.score_propagation_alpha` 等配置。
- **API 与错误语义收敛**：搜索空 query 提前拒绝；公开 `viking://` URI 校验更严格；错误统一进入标准 error envelope。
- **Docker 与初始化**：持久化状态收敛到 `/app/.openviking`；缺少 `ov.conf` 时容器存活并返回 503 初始化指引。
- **安全修复**：bot 图片工具禁止读取沙箱外文件；health check 无凭证时跳过身份解析；API key 字段哈希拆分为独立开关。

### 升级说明

- `encryption.api_key_hashing.enabled` 需要显式配置（默认 `false`）。如依赖旧的隐式哈希行为，需手动开启。
- OpenClaw 插件仅保留远程模式，不再启动本地子进程；`agentId` → `agent_prefix`，`recallTokenBudget` → `recallMaxInjectedChars`。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.3.12...v0.3.13)

## v0.3.12 (2026-04-24)

重点包括解析器加固、VitePress 文档站上线、API Key 安全增强、Azure DevOps git 支持。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.3.10...v0.3.12)

## v0.3.10 (2026-04-23)

### 主要更新

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

重点包括 Memory V2 设为默认、Bot MCP client 支持、Codex memory plugin 示例、OpenClaw 统一 `ov_import`/`ov_search`、本地 Ollama 交互式部署向导、度量系统新增。

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

- 如果你经常通过 CLI 导入目录资源，建议在 `ovcli.conf` 中配置 `upload.ignore_dirs`，减少无关目录上传。
- 如果你需要保留旧行为，可在 `ov.conf` 中显式设置 `"memory": { "version": "v1" }` 回退到 legacy memory pipeline。
- `ov init` / `ov doctor` 请改用 `openviking-server init` / `openviking-server doctor`。
- OpenRouter 或其他 OpenAI 兼容 rerank/VLM 服务可通过 `extra_headers` 注入平台要求的 Header。
- 阿里云 OSS 或其他 S3 兼容实现批量删除有兼容问题时，可开启 `storage.agfs.s3.disable_batch_delete`。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.3.5...v0.3.8)

## v0.3.5 (2026-04-10)

修复 memory v2 锁重试配置、bot proxy 错误信息泄露、session 自动创建、embedding 维度适配等问题。新增场景化 API 测试和 OSS 批量删除兼容选项。

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

VLM 和 embedding 统一配置驱动重试、OVPack 指南、可观测性文档重组、Docker vikingbot/console 新增、OpenClaw session-pattern guard。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.3.1...v0.3.2)

## v0.3.1 (2026-03-31)

PHP tree-sitter 支持、多平台 API 测试、语义摘要自动语言检测、可配置 prompt 模板目录、OpenClaw 安装加固。

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

核心工具单测补充、LiteLLM thinking 参数限定为 DashScope provider、API 测试双模式 CI、Windows engine wheel 修复、OpenClaw 重复注册 guard。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.2.12...v0.2.13)

## v0.2.12 (2026-03-25)

Docker `uv sync --locked`、shutdown CancelledError 处理、bot 配置回滚。

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

Agent 级 watch task 隔离、基于 summary 的文件 embedding、Bot mode 配置和调试模式、共享 adapter 的 RocksDB 锁竞争修复。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.2.8...v0.2.9)

## v0.2.8 (2026-03-19)

### 重点更新

- OpenClaw 插件升级到 2.0（context engine），新增 OpenCode memory plugin，多智能体 memory isolation。
- Memory 冷热分层 archival、长记忆 chunked vectorization、`used()` 使用追踪接口。
- 分层检索集成 rerank、RetrievalObserver 检索质量观测。
- 资源 watch scheduling、reindex endpoint、legacy `.doc`/`.xls` 解析支持、path locking 和 crash recovery。
- 请求级 trace metrics、memory extract telemetry、OpenAI VLM streaming、`<think>` 标签自动清理。
- 跨平台修复（Windows zip、Rust CLI）、AGFS Makefile 重构、CPU variant vectordb engine、Python 3.14 wheel 支持。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.2.6...v0.2.8)

## v0.2.6 (2026-03-11)

### 重点更新

- CLI 体验：`ov chat` 基于 `rustyline` 行编辑、Markdown 渲染、聊天历史。
- 异步能力：session commit `wait` 参数、可配置 worker count。
- 新增 OpenViking Console Web 控制台。
- Bot 增强：eval 能力、`add-resource` 工具、飞书进度通知。
- OpenClaw memory plugin 大幅升级：npm 安装、统一安装器、稳定性修复。
- 平台支持：Linux ARM、Windows UTF-8 BOM 修复、CI runner OS 固定。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.2.5...v0.2.6)

## v0.2.5 (2026-03-06)

PDF 书签标题提取、GitHub tree/ref URL 导入、`add_resource` 索引控制、curl 方式安装 OpenClaw、Bot 重构与 eval 模块、ripgrep 加速 grep。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.2.3...v0.2.5)

## v0.2.3 (2026-03-03)

### Breaking Change

升级后，历史版本生成的 datasets/indexes 与新版本不兼容，无法直接复用。请在升级后重建数据集（建议全量重建）。

停止服务 → `rm -rf ./your-openviking-workspace` → 使用 `openviking-server` 重启服务。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.2.2...v0.2.3)

## v0.2.2 (2026-03-03)

### Breaking Change

此版本包含 Breaking Change。升级前请先停止 VikingDB Server 并清除 workspace 目录。

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

OpenViking CLI v0.2.0 发布，跨平台二进制。

[完整变更记录](https://github.com/volcengine/OpenViking/releases/tag/cli%400.2.0)

## v0.1.18 (2026-02-23)

Rust CLI 实现、markitdown 风格解析器（Word、PowerPoint、Excel、EPub、ZIP）、多 provider 支持、TUI 文件浏览器、memory 去重重设计。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.1.17...v0.1.18)

## cli@0.1.0 (2026-02-14)

OpenViking CLI v0.1.0 初始发布，跨平台二进制。

[完整变更记录](https://github.com/volcengine/OpenViking/releases/tag/cli%400.1.0)

## v0.1.17 (2026-02-14)

回滚动态 `project_name` 配置，CI workspace 清理修复，tree URI 输出校验。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.1.16...v0.1.17)

## v0.1.16 (2026-02-13)

VectorDB 修复、temp URI 可读性、VectorDB/volcengine 动态 `project_name` 配置、uvloop 冲突修复。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.1.15...v0.1.16)

## v0.1.15 (2026-02-13)

Server/CLI 模式现已可用。HTTP 客户端重构、QueueManager 解耦、CLI 启动速度优化、memory 输出语言管线、parser 分支/commit 引用支持。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.1.14...v0.1.15)

## v0.1.14 (2026-02-12)

HTTP Server 和 Python HTTP Client、OpenClaw MCP skill、目录预扫描校验、DAG 触发 embedding、Bash CLI 框架、并行 add 支持。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.1.12...v0.1.14)

## v0.1.12 (2026-02-09)

Sparse logit alpha 搜索、S3 配置重构、异步执行统一、原生 VikingDB 部署、Zip Slip 防护、MCP 查询支持。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.1.11...v0.1.12)

## v0.1.11 (2026-02-05)

支持小型 GitHub 代码仓库。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.1.10...v0.1.11)

## v0.1.10 (2026-02-05)

编译修复和 Windows 发布修复。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.1.9...v0.1.10)

## v0.1.9 (2026-02-05)

初始公开发布。GitHub 模板、多 provider embedding/VLM 支持、Intel Mac 支持、Linux 编译、Python 3.13 支持、chat 示例、日志标准化。

[完整变更记录](https://github.com/volcengine/OpenViking/releases/tag/v0.1.9)
