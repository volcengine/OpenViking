# 更新日志

OpenViking 的所有重要变更都将记录在此文件中。
此更新日志从 [GitHub Releases](https://github.com/volcengine/OpenViking/releases) 自动生成。

## v0.3.23 (2026-06-03)

### 重点更新

- **原生 `ov` CLI 体验重构**：`ov config` 现在是配置管理入口，可交互式添加、编辑、删除、切换配置；`ov config show`、`ov config validate`、`ov config switch` 保留为显式子命令。新增 `ov language` / `ov lang` 选择显示语言，`ov status [--verbose]` 提供聚合诊断视图，`ov health` 与错误提示改为更可读的渲染。
- **Web Studio Playground 与身份管理**：Studio 侧边栏新增 Playground，可查看上下文树、运行 Terminal 操作并与 Agent 面板交互；Connection & Identity 页面支持保存连接、选择 account/user/agent、创建 account/user、复制或重新生成 API key。
- **VikingBot 经验召回配置化**：新增 `bot.ov_server.recall_exp_first_round_only`、`exp_recall_limit`、`exp_recall_max_chars`，用于在单任务/评测场景中只在第一轮注入 agent experience；本地与远端模式都按传入 `agent_id` 做经验命名空间隔离。
- **资源 Watch 更易用**：`add_resource` 设置 `watch_interval > 0` 时不再强制要求显式 `to`；如果导入结果返回稳定 `root_uri`，watch task 会自动绑定到该 URI，CLI/MCP/文档示例同步更新。
- **插件结构化工具结果与 CJK token 估算**：Claude Code / OpenClaw 插件改为向 OpenViking 写入结构化 tool parts，工具调用与结果不再只能内联到文本；CJK-aware token 估算覆盖 Python 与插件侧，降低中文、日文、韩文会话的预算低估风险。

### 升级说明

- `ov config setup-cli` 已移除，请使用裸 `ov config` 进行配置。首次使用新 CLI 时，交互环境会提示选择显示语言；非交互自动化应先运行 `ov language en` 或 `ov language zh-CN`。
- `ov status` 默认展示整理后的诊断视图；需要原始组件数据时使用 `ov status --verbose` 或 `-o json`。
- `ovcli.conf` 默认 URL 统一为 `http://127.0.0.1:1933`，配置序列化会跳过默认值和空字段。
- 语义处理默认并发从 100 调整为 64，文档中 `vlm.max_concurrent` 默认值同步修正为 64；本地目录上传现在会跳过 symlink。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.3.22...v0.3.23)

## v0.3.22 (2026-05-29)

### 重点更新

- **检索 query planner 可配置**：新增轻量 query planner 配置，可选择并调整检索阶段意图分析所用的模型。
- **移除 legacy Memory V1**：删除已废弃的 memory v1 路径，memory `version` 字段现在会拒绝 `v1` 负载。
- **LangChain 可靠性**：自动恢复失效的 OpenViking client，并支持 LangChain 集成的本地批量消息写入。
- **VikingDB 健壮性**：向量检索会跳过 fields 损坏的候选，并为 VikingDB 增加 `ap-southeast-1` region host 映射。
- **CLI 与 server 打磨**：`ov` CLI 在向 server 发请求前先报告缺失的 CLI 配置，server mode 术语从 `dev-implicit` 统一为 `dev`，并统一 embedding 输入截断逻辑。

### 升级说明

- Memory V1 已移除；调用方需使用当前的 memory `version`，`v1` 负载会被拒绝。
- server mode 术语由 `dev-implicit` 改为 `dev`；请更新匹配旧术语的脚本或仪表盘。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.3.21...v0.3.22)

## v0.3.21 (2026-05-27)

### 重点更新

- **Trajectory 记忆更适合检索与复盘**：trajectory schema 新增 `retrieval_anchor` 和 `embedding_template`，索引文本从完整内容收敛为 `trajectory_name + retrieval_anchor`；experience 与 trajectory 之间改为系统维护的 `derived_from` `StoredLink`，写入正向 `links` 与反向 `backlinks`，替代易丢失的 `source_trajectories` 元数据。
- **会话消息支持批量写入**：REST API 新增 `POST /api/v1/sessions/{session_id}/messages/batch`，CLI 新增 `ov session add-messages`，适合导入历史对话或一次写入多轮消息；`ov add-memory` 也复用同一套严格 JSON 消息解析。
- **OpenClaw 搜索工具改名为 `ov_search`**：OpenViking OpenClaw 插件不再注册 `memory_search`，避免与 OpenClaw 内置工具冲突；导入 resource/skill 后统一使用 `ov_search` 和 `/ov-search`。
- **资源解析与二进制 URL 判断增强**：HTTP accessor 扩展图片、音频、视频和 Office/EPUB/zip 文档类型识别；当 `HEAD` 不可靠时会在 `GET` 后用响应头重新判断。Word、PowerPoint、Excel、EPUB、legacy doc 等本地转换路径改为线程中执行，不再阻塞事件循环。
- **Web Studio 随 Python 安装包分发**：`setup`/`build` 会构建并打包 Web Studio 静态资源，pip/pipx 安装后 `/studio` 可直接使用，无需 Docker。
- **LiteLLM VLM 增加 NVIDIA NIM 路由**：模型名中包含 `nvidia_nim` 或 `nemotron` 时可自动走 NVIDIA NIM 的 LiteLLM 前缀和 `NVIDIA_NIM_API_KEY` 环境变量。
- **tau2/VikingBot 评测升级**：新增 `benchmark/tau2/vikingbot` 端到端 runner，支持 cold start、train trajectory commit、test 多次平均和跨 epoch 自改进评测；原 tau2 LLM harness 移到 `benchmark/tau2/llm`。

### 升级说明

- OpenClaw 用户需要把旧的 `/memory-search` 和 `memory_search` 调用迁移到 `/ov-search` 与 `ov_search`。
- pip/pipx 和 Docker 构建链路现在统一通过 Python build 流程产出 Web Studio bundle；本地开发若不希望构建 Studio，可使用 `OV_SKIP_STUDIO_BUILD=1` 跳过。
- `content.read` 新增 `raw=true` 参数；默认行为仍会隐藏 memory 内部字段，兼容已有调用方。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.3.20...v0.3.21)

## v0.3.20 (2026-05-25)

### 重点更新

- **请求级 HTTP profiling**：服务端新增 `server.profile_enabled` 开关。开启后，请求带 `profile=1` 时会对当前 HTTP 请求启用 `cProfile`，并在 JSON 响应中追加 `profile` 行数组。`ov` CLI 新增 `--profile` 入口并能保留、展示 profile 输出。
- **批量 Session 消息写入**：新增 `POST /api/v1/sessions/{session_id}/messages/batch` 和 Python HTTP client / Session wrapper 的 `batch_add_messages`，一次请求最多写入 100 条消息，减少 LangChain/LangGraph 等集成连续写消息时的 HTTP 往返。
- **记忆向量化输入模板**：记忆 schema 新增顶层 `embedding_template`，替代字段级 `searchable` 标记。默认的 `entities`、`events`、`preferences` 模板现在会把关键字段和正文一起用于 embedding，提高语义召回命中。
- **语义索引与锁稳定性**：resource 处理会先把 temp source 同步到 target 后再执行语义 DAG，diff 结果使用 target URI；语义锁 handoff 失效时会尝试重新获取 tree lock，锁冲突类错误会重排队而不是误触发 API circuit breaker。
- **Embedding 输入保护**：embedding 队列会按 `embedding.max_input_tokens` 截断输入，并把过大输入错误分类为 `input_too_large`，避免对不可恢复的大输入反复重试。

### 升级说明

- 自定义 memory schema 如果还在字段上使用 `searchable: true`，应迁移到顶层 `embedding_template`。字段级 `searchable` 已不再参与 embedding 文本生成。
- 配置项 `memory.enable_role_id_memory_isolate` 已统一为 `memory.role_id_memory_isolation_enabled`，请更新自定义 `ov.conf`。
- `profile=1` 是调试能力，不建议在高流量生产路径默认开启；返回内容最多保留约 16 KiB profile 文本。
- 批量消息 API 单次最多接受 100 条消息。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.3.19...v0.3.20)

## v0.3.19 (2026-05-22)

### 重点更新

- **Console BFF 时区语义 breaking change**：`/api/v1/console/dashboard/summary`、`/tokens`、`/context-commits` 现在支持 IANA `timezone` 查询参数，并由服务端按 viewer timezone 返回分桶；调用方应把返回的 `date` / `hour` 视为已本地化，不再在客户端二次 shift。
- **Usage/Audit 统一按 UTC 写入**：Token、检索、上下文提交、Agent 活跃和请求审计 rollup 现在写入 UTC `date_utc`、`hour_utc`、`created_at`，查询时再通过 `zoneinfo` 按用户时区重分桶，覆盖 DST 和半小时时区场景。
- **本地 Usage/Audit schema reset**：SQLite store 新增 schema version v3，升级时会重置不兼容的本地 Usage/Audit 表，避免短保留、pre-GA 数据里混入 local/UTC 字段或半迁移的日/小时表。
- **Web Studio heatmap 对齐新语义**：Web Studio 会把浏览器时区传给 Console BFF，heatmap 直接使用服务端返回的 bucket date，修复 UTC+ 用户下“今天”被二次平移到“明天”的问题。
- **相邻更新**：新增通过 `memory.session_skill_extraction_enabled` 控制的 session skill 提取链路，补充 Hermes OpenViking LoCoMo benchmark scripts，修正 Studio OAuth setup 入口文档，并刷新 LiteLLM 依赖范围。

### 升级说明

- 本版本包含 BFF breaking change：自定义 Console 客户端、生成 SDK 或仪表盘如果调用 `/api/v1/console/*`，应按需传入 `timezone=<IANA name>`，并移除客户端侧 UTC 到本地时区的二次分桶逻辑。
- `tokens` 和 `context-commits` 返回的 `date` / `hour` 已是 viewer timezone 下的 bucket；`audit.created_at` 仍为 UTC ISO，仅在展示层格式化。
- 首次使用新的 Usage/Audit SQLite schema 启动时，不兼容的本地 usage/audit 表会被删除并重建；已有短保留 usage rollup 和 request audit 记录可能被丢弃。
- 如果自动化侧固定了 OpenAPI client 或 Console API 类型，需要重新生成，以包含新的 `timezone` 参数。
- 未传 `timezone` 时，Console BFF 会回退到 `server.observability.usage_audit.timezone`（默认 `local`）；服务端集成若需要稳定的用户日边界，应显式配置或传参。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.3.18...v0.3.19)

## v0.3.18 (2026-05-22)

### 重点更新

- **Web Studio 成为默认 Console**：新增 `web-studio` 前端 console workspace，随 Docker 与 pip 分发并通过 `/studio` 提供服务；OAuth authorize UI 迁入 Web Studio，legacy console 下线，同时保留 favicon 兼容路由。
- **MCP / API / CLI 自动化能力**：Watch Management 覆盖 REST、`ov` CLI 与 MCP；新增本地文件 progressive single-entrypoint upload；增加 `code_outline`、`code_search`、`code_expand` 代码导航工具，并修正 upload-only 与 zip `--ignore-dirs` 的作用域处理。
- **Agent 与 OpenClaw 生态**：OpenClaw setup helper 支持 npm 插件安装，插件文档对齐 ClawHub package metadata，新增 `ov_dream` OpenClaw skill，并支持将过大的 OpenClaw tool result externalize 到 OpenViking。
- **Memory 与检索**：升级 trajectory extraction，新增 memory link 能力，支持通过开关启用 Vaka memory templates，修复缺失 tool-call 计数和消息 peer 检索缺失问题，并行化 hierarchical child search。
- **Storage、VectorDB 与模型链路稳定性**：存储锁与 IO 异步化，异步客户端按 event loop 隔离；修复 semantic lock ownership、`mv not found` 误报、URI remapping、S3 grep 性能、VectorDB Unicode recovery、超大 bytes row、embedding 错误透出和 VLM LiteLLM native routes 等问题。
- **可观测性、文档与部署打磨**：新增 VikingBot feedback observability，集中化 metric registry，usage audit SQLite 迁入 system data，刷新 Helm chart 默认配置，更新品牌资产与二维码，并补齐 public base URL、signed upload TTL、Watch API、MCP code tools、ready 探针和 `/studio` 迁移文档。

### 升级说明

- 旧 console 与 `8020` 引用应迁移到 `/studio` 的 Web Studio；自定义反代、书签和部署文档需要同步更新。
- Docker 与 pip 包现在包含 Web Studio 静态资产；使用自定义 Dockerfile、Caddy 或 Helm overlay 的部署应先复核新的默认配置。
- Usage audit SQLite 现在存放在 system data 目录；手动管理本地 audit 文件的部署需要确认新路径和保留策略。
- Embedding 上游失败不再被静默超时掩盖；调用方和健康探针应按显式 provider 错误处理。
- Watch Management、MCP upload 与代码导航工具扩展了公共集成面；如自动化侧固化了 API/MCP schema，需要同步重新生成。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.3.17...v0.3.18)

## v0.3.17 (2026-05-15)

### 重点更新

- **Agent 集成**：新增 LangChain 与 LangGraph 集成 `openviking.integrations.langchain`（`OpenVikingRetriever`、`with_openviking_context()`、`OpenVikingChatMessageHistory`、`OpenVikingContextMiddleware`、`OpenVikingStore`（LangGraph store）、`create_openviking_tools()`）；Codex / OpenCode 插件改造为通过生命周期 hooks 做自动召回、逐轮捕获和 PreCompact 前提交，并直接连接 OpenViking 原生 `/mcp` 端点。
- **OVPack v2 与完整备份恢复**：`ov export` / `ov import` 支持 v2 manifest、文件校验、portable index scalar、可选 dense vector snapshot 和冲突策略；新增 `ov backup` / `ov restore` 用于公共 scope 的完整迁移。
- **原生 CLI 分发**：新增 `@openviking/cli` npm 包，可通过 `npm i -g @openviking/cli` 使用 `ov`；Rust CLI 发布流水线扩展 Linux musl 构建、npm trusted publishing 和 CLI 集成测试。
- **检索与文件系统能力**：`find` / `search` 新增 `level` 过滤，可限定 L0 abstract、L1 overview 或 L2 文件命中；资源文件增加 Phase 1 WebDAV 适配；`observer.filesystem` 暴露文件系统观测入口。
- **Console 与 Usage/Audit**：新增 Usage/Audit 模块和 `/api/v1/console/*` BFF，基于现有 observability event bus 统计 token、检索次数、上下文提交热力图、请求审计和上下文库存。
- **存储与并发可靠性**：增强精确路径锁和生命周期锁修复内容写入并发覆盖；阻塞后端调用移出 event loop；QueueFS SQLite 持久化扩展；task 记录现在会持久化以支持多实例查询；Git 仓库 `add_resource(wait=false)` 会返回已预占的 `root_uri`，并在导入完成前提供持久化 task 进度。

### 升级说明

- `storage.task_tracker` 已废弃并会被忽略。Task 记录始终持久化到各账号的 `_system/tasks` 目录。
- `vlm.backup` 只支持一层 backup，且只在 rate limit、`5xx`、连接失败和 timeout 等可重试错误上触发；认证、权限和计费类错误不会自动切换。
- `vlm.extra_request_body` 会合并到 OpenAI SDK / LiteLLM 的 `extra_body`，适合接入 Ollama、OpenAI-compatible gateway 或其他需要额外 JSON 字段的 provider。
- Codex 插件新部署建议使用 `OPENVIKING_*` 环境变量调优；旧的 `ov.conf` 中 `codex.*` 配置仍保留兼容，但不再推荐作为首选。
- OVPack dense vector snapshot 只支持纯 dense index；embedding provider、model、input、参数和 dimension 不兼容时，在 `--vector-mode auto` 下回退重算，在 `--vector-mode require` 下失败。

[完整变更记录](https://github.com/volcengine/OpenViking/compare/v0.3.16...v0.3.17)

## v0.3.14 (2026-04-30)

### 重点更新

- **可观测性**：OTLP 导出支持自定义 `headers`，覆盖 traces、logs、metrics 三条链路，便于直连需要额外鉴权头或 gRPC metadata 的观测后端。
- **上传**：本地目录扫描和上传现在遵循根目录及子目录中的 `.gitignore` 规则，减少构建产物和临时文件被误导入。
- **检索**：`search` / `find` 支持一次传入多个 target URI，适合跨目录、跨仓库范围检索。
- **多租户**：OpenClaw 插件明确 `peer_prefix` 仅作为 peer metadata 使用；OpenCode memory plugin 补上 tenant headers 透传。
- **管理**：废弃的 agent namespace 发现入口已删除。

### 升级说明

- OTLP 后端接入可通过 `headers` 统一配置鉴权信息（gRPC 模式为 metadata，HTTP 模式为请求头）。
- 本地目录上传默认遵循 `.gitignore` 规则，此前被导入的临时/生成文件升级后可能被自动过滤。
- OpenClaw 插件运行时身份通过 `peer_prefix` peer metadata 表达，不再对应 OpenViking agent namespace。

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
- OpenClaw 插件仅保留远程模式，不再启动本地子进程；运行时 agent 身份迁移为 peer metadata，`recallTokenBudget` → `recallMaxInjectedChars`。

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
- OpenClaw 插件默认远端连接行为、鉴权、namespace 和 `peer_id` 处理更贴合服务端多租户模型。

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
- 旧版 memory v1 已移除；记忆抽取现在仅使用 v2。
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

- OpenClaw 插件升级到 2.0（context engine），新增 OpenCode memory plugin，多智能体 memory isolation 基于 peer metadata。
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
