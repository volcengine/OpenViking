# OpenViking OpenClaw 插件帮助文档

> 本文档面向插件使用者、集成方、排障同学和后续维护者，系统梳理 `@openviking/openclaw-plugin` 的实现原理、执行流程、核心功能、安装配置、构建测试、Debug、发布上线与验证方式，以及它与火山 OpenViking 的联动机制。

## 1. 一句话结论

`@openviking/openclaw-plugin` 是一个 OpenClaw `context-engine` 插件。它把 OpenClaw 的会话生命周期、上下文组装、记忆召回、会话归档、工具结果回读、资源/技能导入等能力，通过 HTTP API 接到远端 OpenViking 服务上，让 Agent 拥有长期记忆、工作记忆、历史压缩、语义检索和 RAG 能力。

它不负责启动本地 OpenViking Server，也不替代 OpenClaw Runtime；OpenClaw 仍负责 Agent 执行、prompt 编排和工具调用，OpenViking 负责上下文数据库、长期记忆、session/archive、resource/skill 检索与服务端抽取。

---

## 2. 插件解决的问题

| 问题 | 没有插件时的表现 | 插件提供的能力 |
| --- | --- | --- |
| 长对话上下文膨胀 | 会话越来越长，token 成本和模型输入风险持续上升 | 通过 OpenViking session/archive 把长历史压缩为工作记忆，并在 `assemble` 时重建可控上下文 |
| 过去偏好/事实容易遗忘 | Agent 需要用户反复提醒 | `autoRecall` 自动搜索长期记忆并注入当前 user message |
| 会话历史压缩后细节丢失 | summary 不含原命令、路径、配置值时难以追溯 | `ov_archive_search` / `ov_archive_expand` 回查归档原文 |
| 大工具结果污染上下文 | 大量工具输出挤占模型窗口 | OpenViking 支持 tool result 外置存储，插件提供读/搜/列工具 |
| 文档、仓库、URL 无法沉淀为知识库 | Agent 临时读取，跨会话不可复用 | 手动 `/add-resource` 导入 resource，`ov_search` / `ov_read` 检索消费；Agent 可见 `add_resource` 默认禁用 |
| Skill 难以沉淀和语义发现 | 技能依赖本地或手工注入 | `add_skill` 导入到 OpenViking user skill 空间 |
| 多租户/多说话人记忆串用 | 不同 user/peer 可能共用错误上下文 | 插件支持 account/user header，并可用 `peer_role` / `peer_prefix` 把 OpenClaw 说话人写入 OpenViking `peer_id` |

---

## 3. 架构定位

### 3.1 插件在 OpenClaw 中的形态

插件清单声明了它是 `context-engine` 插件，并在启动时激活 hook/tool 能力：`openclaw.plugin.json:2`、`openclaw.plugin.json:4`、`openclaw.plugin.json:6`。

包元信息中，插件通过 OpenClaw 扩展入口加载 `./dist/index.js`，并提供 setup CLI 入口 `./dist/commands/setup.js`：`package.json:57`。

插件在运行时主要承担四个角色：

1. **Context Engine**：实现 `assemble`、`afterTurn`、`compact`，并声明自己拥有 compaction。
2. **Hook 集成层**：监听 `session_start`、`session_end`、`before_reset` 等事件。
3. **Tool Provider**：注册 memory、archive、resource、skill、tool-result 相关工具。
4. **Runtime/Setup 管理层**：提供 `openclaw openviking setup/status`，并在服务启动时做 health check。

### 3.2 核心文件职责

| 文件 | 主要职责 |
| --- | --- |
| `index.ts` | 插件注册入口；解析配置；注册工具、命令、hook、context engine 和 service |
| `context-engine.ts` | 实现 ContextEngine：`assemble`、`afterTurn`、`compact`、session ID 映射、消息转换、工作记忆组装 |
| `client.ts` | OpenViking HTTP Client；统一添加认证/租户 header；封装 session、search、resource、skill、tool-result API |
| `config.ts` | 插件配置 schema、默认值、环境变量解析、peer 配置 |
| `auto-recall.ts` | 自动召回查询清洗、召回超时控制、记忆块构建与注入 |
| `memory-ranking.ts` | 召回结果去重、阈值过滤、偏好/事件/词面重合度重排 |
| `text-utils.ts` | 会话文本清洗、metadata/心跳/命令过滤、增量 turn 消息提取、bypass session pattern |
| `commands/setup.ts` | setup/status CLI，配置写入、health check、root/user key 探测、slot 激活 |
| `session-transcript-repair.ts` | 修复 toolCall/toolResult 配对、去重、孤儿 tool result 等 transcript 结构问题 |

---

## 4. 执行流程总览

### 4.1 插件加载流程

1. OpenClaw 根据插件入口加载 `index.ts` 的默认导出。
2. `register(api)` 读取 `api.pluginConfig`，用 `memoryOpenVikingConfigSchema.parse` 解析配置；解析失败时只注册 setup CLI，提示用户运行 setup：`index.ts:558`、`index.ts:576`。
3. 创建 `OpenVikingClient`，注入 `baseUrl`、`apiKey`、超时与租户配置：`index.ts:625`。
4. 注册工具、slash command、hook、context engine 与 service：`index.ts:872`、`index.ts:962`、`index.ts:1913`、`index.ts:1945`、`index.ts:1970`。
5. Service 启动时调用 `/health` 做一次非阻塞 health check，并输出初始化日志：`index.ts:1970`。

### 4.2 会话 ID 与 peer 路由流程

OpenClaw 的 `sessionId/sessionKey` 不能总是直接作为 OpenViking 存储路径。插件用 `openClawSessionToOvStorageId` 生成安全稳定的 OpenViking session id：

- 如果 `sessionId` 是 UUID，直接小写复用。
- 如果有 `sessionKey`，用 SHA-256 生成稳定 id。
- 如果非 UUID 的 `sessionId` 包含 Windows 路径不安全字符，也用 SHA-256。
- 否则使用原 `sessionId`。

实现位置：`context-engine.ts:342`。

插件仍会从 OpenClaw runtime/session context 解析一个稳定的 agent id，用于 `viking://agent/memories` 的兼容检索目标和 `peer_role=assistant` 时的 assistant `peer_id`。`peer_prefix` 非空时，assistant peer id 会变成 `<peer_prefix>_<agentId>`；当前实现不发送 `X-OpenViking-Agent` header。

### 4.3 `assemble`：回复前组装上下文

OpenClaw 会在 context engine 上调用 `assemble`。当前实现把 assemble 分成两类：

| 调用形态 | 判断方式 | 插件行为 |
| --- | --- | --- |
| 主 assemble / preflight | 参数带 `prompt`、`availableTools` 或 `citationsMode` | 从 OpenViking 获取 session context，回放 archive summary + active messages |
| transformContext assemble | 不带上述字段，通常最后一条已经是当前 user | 执行 auto recall，把长期记忆块 prepend 到最新 user message |

判断逻辑在 `context-engine.ts:1097`。

主 assemble 流程：

1. 解析 session 身份，计算 token budget，记录诊断日志。
2. 调用 `GET /api/v1/sessions/{sessionId}/context?token_budget=...`：`context-engine.ts:1193`、`client.ts:873`。
3. 如果 OpenViking 没有可用 archive/session 数据，直接 passthrough，不影响主链路。
4. 将 `latest_archive_overview` 转成 `[Session History Summary]`。
5. 将 OpenViking parts 消息转换为 OpenClaw `AgentMessage`，包括 tool part → `toolCall` + `toolResult`。
6. 修复 transcript：合并连续 user/assistant、修复 toolCall/toolResult 配对，必要时插入占位 user 以满足 provider 交替约束。
7. 返回组装后的 messages 和可选 `systemPromptAddition`。

transformContext auto recall 流程：

1. 从最新 user message 提取查询文本。
2. 清洗 metadata、心跳、已注入记忆块等噪音。
3. 快速 precheck，OpenViking 不可用时跳过召回，避免拖慢模型请求。
4. 按 `recallTargetTypes` 并行搜索召回目标；默认搜索 `viking://user/memories`、`viking://agent/memories`，显式设为 `resource` 时只搜索 `viking://resources`；旧字段 `recallResources=true` 仅在未显式配置 `recallTargetTypes` 时把 `resource` 追加到默认集合。
5. 去重、阈值过滤、leaf 优先、偏好/时间问题 boost、词面重合度 boost。
6. 在 `recallMaxInjectedChars` 限制内构建完整记忆行，不截断单条记忆。
7. 用 `<relevant-memories>` 块 prepend 到最新 user message。

自动召回实现入口：`context-engine.ts:1125`、`auto-recall.ts:159`。

### 4.4 `afterTurn`：每轮对话后自动捕获

`afterTurn` 负责把本轮新增消息写入 OpenViking session，并在 `pending_tokens` 超过阈值时异步 commit。

流程：

1. 若 `autoCapture=false`、heartbeat 或 session 被 bypass，直接跳过。
2. 根据 `prePromptMessageCount` 只提取本轮新增消息，不重写全量 transcript。
3. `extractNewTurnMessages` 将 user/assistant 文本和 toolResult 转成 OpenViking parts：`text-utils.ts:342`。
4. 清理 `<relevant-memories>`、metadata、时间戳、心跳等噪音。
5. 逐条调用 `POST /api/v1/sessions/{sessionId}/messages`：`context-engine.ts:1378`、`client.ts:703`。
6. 调 `GET /api/v1/sessions/{sessionId}` 读取 `pending_tokens`：`context-engine.ts:1389`、`client.ts:770`。
7. 若 `pending_tokens < commitTokenThreshold`，本轮结束。
8. 否则调用 `commitSession(wait=false, keepRecentCount=cfg.commitKeepRecentCount)`；服务端 Phase 2 记忆抽取异步继续执行：`context-engine.ts:1403`。
9. 开启 `logFindRequests` 时，插件轮询 task 结果并打印 Phase 2 抽取状态：`context-engine.ts:1424`。

### 4.5 `compact`：主动压缩边界

`compact` 是同步边界，用于 `/compact` 或 OpenClaw 触发压缩时阻塞等待服务端 commit 完成。

流程：

1. 解析 OpenViking session id。
2. 调用 `commitSession(wait=true, keepRecentCount=0)`，要求服务端归档所有当前消息：`context-engine.ts:1500`。
3. 如果 Phase 2 failed/timeout，返回失败原因。
4. 如果没有生成 archive，返回 `commit_no_archive`。
5. 如果归档成功，再回读 `getSessionContext`，获取最新 `latest_archive_overview` 作为 summary：`context-engine.ts:1605`。
6. 返回 tokensBefore/tokensAfter、latest archive id 和 summary。

### 4.6 `before_reset`：重置前保护性提交

插件监听 `before_reset`，在 reset 前尽量 commit 当前 OpenViking session，避免对话被重置时未归档内容丢失：`index.ts:1919`。

---

## 5. 核心功能

### 5.1 长期记忆自动召回

默认开启 `autoRecall`。模型回复前，插件会根据当前用户问题搜索长期记忆，并注入相关上下文。

关键配置：

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| `autoRecall` | `true` | 是否启用自动召回 |
| `recallLimit` | `6` | 最终注入记忆条数上限 |
| `recallScoreThreshold` | `0.15` | 候选过滤阈值 |
| `recallMaxInjectedChars` | `4000` | 注入总字符上限；单条记忆不截断，不完整则跳过 |
| `recallPreferAbstract` | `false` | 是否优先使用 abstract，而非读取 leaf 记忆全文 |
| `recallTargetTypes` | `["user","agent"]` | 自动召回和默认显式召回目标类型；可选 `resource`、`user`、`agent` |
| `recallResources` | `false` | 旧兼容开关；仅在未显式配置 `recallTargetTypes` 时把 `resource` 追加到默认 `user` + `agent` |

配置默认值在 `config.ts:58`。

### 5.2 会话归档与 Working Memory

插件把 OpenClaw turn 持续写入 OpenViking session，由服务端维护 `pending_tokens` 与 archive。超过阈值时：

- `afterTurn` 路径：`wait=false`，异步 Phase 2，默认保留最近 10 条消息。
- `compact` 路径：`wait=true`，同步等待 Phase 2，`keepRecentCount=0`，形成明确压缩边界。

`commitKeepRecentCount` 默认 10，`commitTokenThreshold` 默认 20000：`config.ts:68`。

### 5.3 显式记忆工具

插件注册了三个长期记忆工具：

| 工具 | 用途 | 典型场景 |
| --- | --- | --- |
| `memory_recall` | 显式搜索长期记忆 | 用户问“你还记得我之前说过什么吗” |
| `memory_store` | 把文本立即写入 session 并同步 commit | 用户明确说“记住…” |
| `memory_forget` | 按 URI 删除，或搜索唯一高置信候选后删除 | 用户要求忘记某条信息 |

注册位置：`index.ts:1022`、`index.ts:1190`、`index.ts:1309`。

### 5.4 Archive 回查工具

| 工具 | 用途 | 注意事项 |
| --- | --- | --- |
| `ov_archive_search` | 在当前 session 的 archive 原始消息中关键词 grep | 用于 summary 没有具体细节时；建议尝试 2-3 个关键词 |
| `ov_archive_expand` | 展开某个 archive 的原始消息 | 需要 archive id，例如 `archive_005` |

注册位置：`index.ts:1421`、`index.ts:1522`。

### 5.5 Resource / Skill 导入与检索

| 工具/命令 | 用途 | 落点 |
| --- | --- | --- |
| `/add-resource`（手动）/ `add_resource`（opt-in） | 导入本地文件、目录、URL、Git 仓库、媒体附件；`add_resource` 默认不注册，需 `enableAddResourceTool=true` | `viking://resources/...` |
| `add_skill` / `/add-skill` | 导入 `SKILL.md` 或 skill 目录 | `viking://user/skills/...` |
| `ov_search` / `/ov-search` | 搜索 resources 和 skills | 默认同时搜 resources + user skills |
| `ov_read` | 读取 `ov_search` / trace 命中的完整内容 | 只接受精确 `viking://...` OpenViking 虚拟 URI |

本地文件/目录不会把原路径直接传给服务端，而是先 temp upload；目录会用纯 JS zip 打包后上传：`client.ts:609`、`client.ts:552`。

### 5.6 外置 Tool Result 回读

当 OpenViking 服务端将大工具结果外置为 `viking://user/sessions/.../tool-results/...` 时，插件提供：

| 工具 | 用途 |
| --- | --- |
| `openviking_tool_result_list` | 列出当前 session 已外置的 tool result |
| `openviking_tool_result_search` | 在某个外置 tool result 内关键词搜索，返回 offset 和上下文片段 |
| `openviking_tool_result_read` | 按 offset/limit 读取完整或分页内容 |

注册位置：`index.ts:1601`、`index.ts:1698`、`index.ts:1802`。插件会拒绝跨 session 读取 tool result，避免越权或串会话：`index.ts:1639`、`index.ts:1735`。

---

## 6. 与火山 OpenViking 的联动方式

### 6.1 HTTP Client 与认证头

插件是 OpenViking 的纯 HTTP Client。所有请求统一走 `OpenVikingClient.request`：`client.ts:313`。

请求头逻辑：

| Header | 来源 | 说明 |
| --- | --- | --- |
| `X-API-Key` | `apiKey` / `OPENVIKING_API_KEY` | OpenViking API Key |
| `X-OpenViking-Account` | `accountId` / `OPENVIKING_ACCOUNT_ID` | Root key 或 trusted 部署需要的租户 account |
| `X-OpenViking-User` | `userId` / `OPENVIKING_USER_ID` | Root key 或 trusted 部署需要的用户 |

`peer_role=assistant/person` 时，插件通过请求 body 的 `peer_id` 字段隔离 session 写入和 recall/search；当前实现不发送 `X-OpenViking-Agent` header。

### 6.2 OpenViking 官方 API 完整清单与插件映射

官方 HTTP API 统一前缀为 `/api/v1/`，成功响应一般为 `{ "status": "ok", "result": ..., "time": ... }`，错误响应为 `{ "status": "error", "error": { "code", "message" }, "time" }`。插件只做 HTTP Client，不嵌入 OpenViking SDK；统一封装点是 `OpenVikingClient.request`：`client.ts:313`。

#### 6.2.1 System / Observer

| API | 官方用途 | 当前插件映射 | 说明 |
| --- | --- | --- | --- |
| `GET /health` | 无认证健康检查 | `healthCheck`、`openclaw openviking status` | 用于判断服务是否可达：`client.ts:365` |
| `GET /ready` | 无认证 readiness probe | 暂未直接封装 | K8s/负载均衡可用；会检查 AGFS、VectorDB、API key manager |
| `GET /api/v1/system/status` | 获取初始化状态和当前 user | `getRuntimeIdentity` | 插件用返回的 `user` 参与 canonical URI 展开：`client.ts:369` |
| `POST /api/v1/system/wait` | 等待 semantic/vector 队列处理完成 | 暂未单独封装；`/add-resource`、opt-in `add_resource`、`add_skill` 可用 `wait=true` | 导入后马上检索时建议等待 |
| `GET /api/v1/observer/queue` | 队列指标 | 暂未封装 | 排查资源/skill 处理积压 |
| `GET /api/v1/observer/vikingdb` | VikingDB collection/vector 状态 | 暂未封装 | 排查向量库连接和索引数量 |
| `GET /api/v1/observer/vlm` | VLM token 用量 | 暂未封装 | 观测摘要、抽取、视觉处理成本 |
| `GET /api/v1/observer/system` | 汇总 observer 状态 | 暂未封装 | 生产监控推荐项 |
| `GET /api/v1/debug/health` | 认证版健康检查 | 暂未封装 | 返回 `{ healthy: true/false }` |

#### 6.2.2 Retrieval / Search

| API | 官方用途 | 当前插件映射 | 关键参数 / 返回 |
| --- | --- | --- | --- |
| `POST /api/v1/search/find` | 快速语义检索，不依赖 session context | 自动召回、`memory_recall`、`ov_search`、`memory_forget` | body: `query`、`target_uri`、`limit`、`score_threshold`；返回 `memories[]`、`resources[]`、`skills[]`，每项含 `uri`、`level`、`abstract`、`score`、`category`、`relations`：`client.ts:428` |
| `POST /api/v1/search/search` | 带 session context 和 intent analysis 的检索 | 暂未使用 | body 可带 `session_id`；返回 `query_plan` / `query_results`。当前插件为了稳定和低延迟统一用 `find()`，session context 由插件自己组装 |
| `POST /api/v1/search/grep` | 正则/关键词内容搜索 | `ov_archive_search` | body: `uri`、`pattern`、`case_insensitive`、`node_limit`；插件限定在 `viking://user/sessions/{id}/history` 内搜 archive：`client.ts:897` |
| `POST /api/v1/search/glob` | glob 文件匹配 | 暂未封装 | body: `pattern`、`uri`、`node_limit`；适合按 `**/*.md`、`src/**/*.ts` 找资源路径 |

#### 6.2.3 Filesystem / Content

| API | 官方用途 | 当前插件映射 | 关键参数 / 返回 |
| --- | --- | --- | --- |
| `GET /api/v1/fs/ls?uri=...` | 列目录 | skill 列表官方页本质也复用该 API；插件暂未通用封装 | 支持 `simple`、`recursive`、`output=agent/original`、`abs_limit`、`show_all_hidden`、`node_limit` |
| `GET /api/v1/fs/tree?uri=...` | 递归树 | 暂未封装 | 支持 `level_limit`、`node_limit`，返回 flat array + `rel_path` |
| `GET /api/v1/fs/stat?uri=...` | 查元信息/是否存在 | 暂未封装 | 返回 `name`、`size`、`mode`、`isDir`、`uri`、`mtime`、`ctime` |
| `POST /api/v1/fs/mkdir` | 创建目录 | 暂未封装 | body: `uri`，父目录自动创建 |
| `POST /api/v1/fs/mv` | 移动/重命名 | 暂未封装 | body: `from_uri`、`to_uri`，会保留元数据和 relations |
| `DELETE /api/v1/fs?uri=...&recursive=...` | 删除资源/目录 | `memory_forget`、`deleteUri` | 插件默认 `recursive=false`，用于删除具体 memory URI：`client.ts:934` |
| `GET /api/v1/content/abstract?uri=...` | 读取 L0 abstract | 暂未封装 | 约 100 token 摘要，适合快速判断目录/文件主题 |
| `GET /api/v1/content/overview?uri=...` | 读取 L1 overview | 暂未封装 | 目录级结构化概览，适合介于 abstract 和 full content 之间的排查 |
| `GET /api/v1/content/read?uri=...&offset=...&limit=...` | 读取 L2 full content | 自动召回读取 level=2 命中内容、`ov_read` | `ov_read` 暴露 `uri` 参数，未暴露 `offset/limit`：`client.ts:470` |
| `GET /api/v1/relations?uri=...` | 查看资源关系 | 暂未封装 | 返回 `from_uri`、`to_uri`、`reason`、`created_at` |
| `POST /api/v1/relations/link` | 创建有向关系 | 暂未封装 | body: `from_uri`、`to_uri`/`uris`、`reason` |
| `POST /api/v1/relations/unlink` | 删除有向关系 | 暂未封装 | body: `from_uri`、`to_uri`；幂等 |

#### 6.2.4 Resources / Skills Import

| API | 官方用途 | 当前插件映射 | 关键参数 / 返回 |
| --- | --- | --- | --- |
| `POST /api/v1/resources/temp_upload` | 临时上传本地文件 | `/add-resource`、opt-in `add_resource`、`add_skill` 的本地文件/目录路径 | 插件本地目录会先 zip，再上传，服务端返回 `temp_file_id`：`client.ts:533`、`client.ts:552` |
| `POST /api/v1/resources` | 导入文件、目录、URL、Git 仓库等 resource | `/add-resource` 命令；`add_resource` 工具仅在 `enableAddResourceTool=true` 时注册 | body 官方字段包括 `path`/`temp_file_id`、`target`/插件兼容 `to`、`parent`、`reason`、`instruction`、`wait`、`timeout`、`strict`、`ignore_dirs`、`include`、`exclude`；返回 `root_uri`、`source_path`、`errors`、`queue_status`：`client.ts:609` |
| `POST /api/v1/skills` | 导入 skill，支持 dict、MCP tool、SKILL.md 字符串、文件/目录 | `add_skill` 工具、`/add-skill` 命令 | body: `data` 或 `temp_file_id`、`wait`、`timeout`；返回 `uri`/`skill_uri`、`name`、`auxiliary_files`、`queue_status`：`client.ts:663` |
| `POST /api/v1/pack/export` | 导出 `.ovpack` | 暂未封装 | 官方 API Overview 有列出；当前插件没有 pack 管理工具 |
| `POST /api/v1/pack/import` | 导入 `.ovpack` | 暂未封装 | 官方 API Overview 有列出；当前插件没有 pack 管理工具 |

#### 6.2.5 Sessions / Working Memory

| API | 官方用途 | 当前插件映射 | 关键参数 / 返回 |
| --- | --- | --- | --- |
| `POST /api/v1/sessions` | 创建新 session | 暂未显式调用 | 官方创建后返回 `session_id`；当前插件用 OpenClaw session id 映射成 OpenViking storage id，服务端 `GET`/写消息可自动创建 |
| `GET /api/v1/sessions` | 列出当前用户 session | 暂未封装 | 返回 `session_id`、`uri`、`is_dir` |
| `GET /api/v1/sessions/{sessionId}` | 获取 session 元信息 | `afterTurn` 元信息检查 | 返回 `message_count`，插件兼容读取 `commit_count`、`pending_tokens`、`llm_token_usage`：`client.ts:770` |
| `DELETE /api/v1/sessions/{sessionId}` | 删除 session | `deleteSession`（内部能力，未暴露普通用户工具） | 删除 active messages、archives、tools、元数据；不删除已抽取 memories：`client.ts:931` |
| `POST /api/v1/sessions/{sessionId}/messages` | 追加 user/assistant 消息 | `afterTurn` 增量提交 | body 支持 `role`、`content` 或 `parts`；插件使用 `parts` 保存 text/tool/context，另扩展 tool result 外置字段：`client.ts:703` |
| `POST /api/v1/sessions/{sessionId}/commit` | 归档消息、抽取长期记忆、清空/保留 active buffer | `afterTurn` 异步 commit、`compact` 同步 wait | 插件会传 `keep_recent_count`；若服务端返回 `task_id`，插件可轮询 Phase 2：`client.ts:798` |
| `GET /api/v1/tasks/{taskId}` | 查询异步任务 | commit Phase 2 轮询 | 官方导航未单列，但插件依赖该端点判断 memory extraction 完成/失败：`client.ts:864` |
| `GET /api/v1/sessions/{sessionId}/context?token_budget=...` | 获取 session working memory 上下文 | `assemble` / `compact` | 返回 latest archive overview、pre archive abstracts、active messages 和 token 估算：`client.ts:873` |
| `GET /api/v1/sessions/{sessionId}/archives/{archiveId}` | 展开 archive 原文 | `ov_archive_expand` | 用于从有损 summary 回查原始消息：`client.ts:885` |
| `GET /api/v1/sessions/{sessionId}/tool-results` | 列外置工具结果 | `openviking_tool_result_list` | 支持 `tool_name`、`limit`：`client.ts:517` |
| `GET /api/v1/sessions/{sessionId}/tool-results/{toolResultId}` | 分页读取外置工具结果 | `openviking_tool_result_read` | 支持 `offset`、`limit`、`include_metadata`：`client.ts:478` |
| `GET /api/v1/sessions/{sessionId}/tool-results/{toolResultId}/search?q=...` | 搜索外置工具结果 | `openviking_tool_result_search` | 支持 `limit`、`context_chars`：`client.ts:498` |

#### 6.2.6 Skills Runtime

| API | 官方用途 | 当前插件映射 | 说明 |
| --- | --- | --- | --- |
| `GET /api/v1/fs/ls?uri=viking://user/skills/` | 列 skill | `ov_search` 默认会搜 skills；未单独 list | 官方 `List Skills` 页面本质复用 `fs/ls` |
| `POST /api/v1/skills` | Add Skill / MCP tool conversion | `add_skill` | 与资源导入章节相同 |
| 读取 `viking://user/skills/{name}/SKILL.md` | 读 skill 全文 | `ov_read` 或 `content/read` 手工读取 | 官方建议按 L0/L1/L2 逐级读取 |
| `call-skill` 页面 | 官方导航存在但当前内容实际为 Add Skill | 插件不通过 OpenViking 执行 skill | OpenClaw 自己负责工具执行，OpenViking 主要存储/检索 skill 文档 |

#### 6.2.7 Admin / Authentication

| API | 角色 | 官方用途 | 插件关系 |
| --- | --- | --- | --- |
| `POST /api/v1/admin/accounts` | ROOT | 创建 workspace/account 和首个 admin | 部署初始化时使用；插件运行期不调用 |
| `GET /api/v1/admin/accounts` | ROOT | 列出 workspaces | 运维使用 |
| `DELETE /api/v1/admin/accounts/{account_id}` | ROOT | 删除 workspace 及全部数据 | 高风险运维操作，插件不调用 |
| `POST /api/v1/admin/accounts/{account_id}/users` | ROOT/ADMIN | 注册用户并生成 user key | 为 OpenClaw agent 预置 API key 时使用 |
| `GET /api/v1/admin/accounts/{account_id}/users` | ROOT/ADMIN | 列用户 | 运维排查租户/用户 |
| `DELETE /api/v1/admin/accounts/{account_id}/users/{user_id}` | ROOT/ADMIN | 移除用户并吊销 key | 运维使用 |
| `PUT /api/v1/admin/accounts/{account_id}/users/{user_id}/role` | ROOT | 修改角色 | 运维使用 |
| `POST /api/v1/admin/accounts/{account_id}/users/{user_id}/key` | ROOT/ADMIN | 重置用户 API key | key 泄露/轮换时使用 |

认证方式：OpenViking HTTP 支持 `X-API-Key: <key>` 和 `Authorization: Bearer <key>`；插件固定使用 `X-API-Key`。如果服务端启用了多租户且当前 key 需要显式租户上下文，插件还会附加 `X-OpenViking-Account`、`X-OpenViking-User`。

### 6.3 URI 与命名空间

插件使用 OpenViking 的 filesystem paradigm，常见 URI：

| URI | 含义 |
| --- | --- |
| `viking://user/memories` | 当前用户长期记忆别名 |
| `viking://agent/memories` | 当前 agent 长期记忆别名 |
| `viking://resources` | account/resource 知识库 |
| `viking://user/skills` | 当前用户 skill 空间 |
| `viking://user/sessions/{sessionId}/history` | session archive 历史 |
| `viking://user/sessions/{sessionId}/tool-results/{id}` | 外置工具结果 |

`viking://agent/memories` 仍可作为 agent 记忆检索目标；session 消息和 recall/search 的说话人隔离由 `peer_role` / `peer_prefix` 解析出的 `peer_id` 控制。

---

## 7. 安装与使用

### 7.0 五分钟快速路径

如果你只想先把插件跑起来，按这 4 步执行：

```bash
# 1. 确认 OpenViking Server 已启动
curl http://127.0.0.1:1933/health

# 2. 安装插件
openclaw plugins install clawhub:@openviking/openclaw-plugin

# 3. 写入 OpenViking 连接配置并激活 contextEngine slot
openclaw openviking setup --base-url http://127.0.0.1:1933 --api-key <OPENVIKING_API_KEY> --json

# 4. 重启并验证
openclaw gateway restart
openclaw openviking status --json
openclaw config get plugins.slots.contextEngine
```

期望结果：`status` 中 `configured=true`、`slotActive=true`、`health.ok=true`，并且 `plugins.slots.contextEngine` 输出 `openviking`。

本仓库示例仅保留 ClawHub / 本地开发安装说明；TOS release 包安装、上传、下载和回滚流程不在 `examples/openclaw-plugin` 范围内。

### 7.1 前置要求

| 组件 | 要求 |
| --- | --- |
| Node.js | >= 22 |
| OpenClaw | >= 2026.4.8 |
| OpenViking Server | >= 0.2.9 |

兼容性声明在 `install-manifest.json:43`。

### 7.2 启动 OpenViking Server

插件只连接远端 OpenViking，不启动服务端。先启动服务端：

```bash
pip install openviking --upgrade --force-reinstall
openviking-server init
openviking-server doctor
openviking-server --host 0.0.0.0 --port 1933
```

`openviking-server init` 会生成 OpenViking 服务端配置；`openviking-server doctor` 会检查模型 provider、embedding provider、workspace 权限等基础依赖；`openviking-server` 才是真正启动 HTTP API 的进程。OpenClaw 使用插件期间，这个服务进程需要一直运行。

验证服务：

```bash
curl http://127.0.0.1:1933/health
```

后台启动可以用：

```bash
mkdir -p ~/.openviking/data/log
nohup openviking-server > ~/.openviking/data/log/openviking.log 2>&1 &
```

如果 OpenViking 跑在另一台机器或容器中，需要监听可访问地址：

```bash
openviking-server --host 0.0.0.0 --port 1933
```

此时 OpenClaw 插件的 `baseUrl` 要配置为调用方可访问的地址，例如 `http://your-server:1933`，而不是服务端本机视角的 `127.0.0.1`。

### 7.3 OpenViking 服务端配置文件

OpenViking 服务端配置与 OpenClaw 插件配置是两层配置，位置不同、作用也不同：

| 配置层 | 默认位置 | 作用 | 常见写入方式 |
| --- | --- | --- | --- |
| OpenViking 服务端 | `~/.openviking/ov.conf` | 配置服务端 workspace、日志、embedding、VLM/model provider | `openviking-server init` 交互生成；也可提前创建文件 |
| OpenViking 服务端自定义路径 | `OV_CONFIG=/path/to/ov.conf` | 指定非默认配置文件 | 启动 `openviking-server` 前导出环境变量 |
| OpenClaw 插件层 | `~/.openclaw/openclaw.json` | 配置插件连接哪个 OpenViking HTTP 服务、API key、account/user、召回/捕获策略 | `openclaw openviking setup` 或 `openclaw config set` |
| 一键安装脚本环境文件 | `~/.openclaw/openviking.env` | 保存一键安装脚本使用过的 OpenViking 连接参数，便于排查/复用 | `scripts/volcengine-openviking-install.sh` |

最小 `~/.openviking/ov.conf` 示例：

```json
{
  "storage": {
    "workspace": "/Users/bytedance/.openviking/data"
  },
  "log": {
    "level": "INFO",
    "output": "stdout"
  },
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "api_base": "https://ark.cn-beijing.volces.com/api/v3",
      "api_key": "$ARK_API_KEY",
      "model": "doubao-embedding-vision-250615",
      "dimension": 2048
    },
    "max_concurrent": 10
  },
  "vlm": {
    "provider": "volcengine",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3",
    "api_key": "$ARK_API_KEY",
    "model": "doubao-seed-2-0-pro-260215",
    "max_concurrent": 20
  }
}
```

也可以使用 OpenAI / LiteLLM 等 provider，例如：

```json
{
  "storage": {
    "workspace": "/Users/bytedance/.openviking/data"
  },
  "embedding": {
    "dense": {
      "provider": "openai",
      "api_base": "https://api.openai.com/v1",
      "api_key": "$OPENAI_API_KEY",
      "model": "text-embedding-3-large",
      "dimension": 1536
    }
  },
  "vlm": {
    "provider": "openai",
    "api_base": "https://api.openai.com/v1",
    "api_key": "$OPENAI_API_KEY",
    "model": "gpt-4o"
  }
}
```

提前设置方式：

```bash
mkdir -p ~/.openviking
$EDITOR ~/.openviking/ov.conf

# 不建议把真实 key 写进文档或命令历史；优先通过环境变量注入
export ARK_API_KEY=<your-ark-key>
# 或 OpenAI provider：export OPENAI_API_KEY=<your-openai-key>

openviking-server doctor
openviking-server --host 127.0.0.1 --port 1933
```

如果要使用自定义配置文件：

```bash
export OV_CONFIG=/path/to/ov.conf
openviking-server doctor
openviking-server --host 127.0.0.1 --port 1933
```

注意事项：

- `ov.conf` 是服务端模型与存储配置，决定服务端如何做 embedding、VLM 抽取、resource 解析和 session archive；插件不会读取或修改这个文件。
- `api_key` 字段建议写成 `$ARK_API_KEY`、`$OPENAI_API_KEY` 这类环境变量占位，并在启动服务前导出真实 key，避免密钥落盘或进入 Git。
- 切换 embedding 模型或 `dimension` 后，历史向量索引可能不兼容；本地测试环境可清理 workspace 后重建，生产环境需要按服务端迁移/重建索引方案处理。
- `workspace` 要放在服务端进程有读写权限且磁盘容量足够的位置，长期记忆、资源索引、归档和日志都会持续增长。

### 7.4 本机拉起单机版测试

本机单机版适合开发、调试和端到端验证。推荐最小链路如下：

```bash
# 1. 安装 OpenViking Python 包
python3 -m pip install openviking --upgrade --force-reinstall

# 2. 初始化服务端配置
openviking-server init

# 3. 导出模型 provider key，或提前写入自定义 ov.conf
export ARK_API_KEY=<your-ark-key>

# 4. 检查服务端配置和 provider 可用性
openviking-server doctor

# 5. 启动本地 HTTP 服务
openviking-server --host 127.0.0.1 --port 1933
```

另开一个终端验证：

```bash
curl http://127.0.0.1:1933/health
```

然后配置 OpenClaw 插件连接本机服务：

```bash
openclaw openviking setup \
  --base-url http://127.0.0.1:1933 \
  --api-key <OPENVIKING_API_KEY> \
  --json

openclaw gateway restart
openclaw openviking status --json
```

如果本机 OpenViking 服务没有开启 API key 校验，可按服务端实际策略传空 key 或测试 key；如果使用火山 OpenViking Service / root key / trusted server 流程，则按服务端要求补充 `--account-id` 和 `--user-id`。

单机版联调检查点：

1. `curl /health` 返回正常。
2. `openclaw openviking status --json` 中 `configured=true`、`health.ok=true`。
3. `openclaw config get plugins.slots.contextEngine` 输出 `openviking`。
4. 与 Agent 对话一轮后，服务端日志 `~/.openviking/data/log/openviking.log` 或前台输出能看到 session/message/commit 相关请求。
5. 触发 `/compact` 或等待 `pending_tokens` 超过阈值后，在 OpenViking Console/TUI 或插件工具中能检索到 archive/memory。

### 7.5 安装插件

```bash
openclaw plugins install clawhub:@openviking/openclaw-plugin
```

不要使用 `clawhub install openviking` 安装本插件；那是另一个 AgentSkill，不是 OpenClaw 插件。

### 7.6 配置插件

交互式：

```bash
openclaw openviking setup
```

非交互式：

```bash
openclaw openviking setup \
  --base-url http://127.0.0.1:1933 \
  --api-key sk-xxx \
  --json
```

如果使用 root key：

```bash
openclaw openviking setup \
  --base-url http://127.0.0.1:1933 \
  --api-key <ROOT_API_KEY> \
  --account-id <ACCOUNT_ID> \
  --user-id <USER_ID> \
  --json
```

如果已有别的 context engine 占用 slot，确认要替换时才加：

```bash
openclaw openviking setup --base-url <URL> --api-key <KEY> --force-slot --json
```

### 7.7 重启与验证

```bash
openclaw gateway restart
openclaw openviking status --json
openclaw config get plugins.slots.contextEngine
```

期望：

- `configured=true`
- `slotActive=true`
- `health.ok=true`
- `plugins.slots.contextEngine` 输出 `openviking`

### 7.8 本地安装脚本边界

`examples/openclaw-plugin` 不包含 TOS release 安装脚本。需要验证本地包时，优先使用 ClawHub 安装或在插件目录内运行 `npm run build` 后用 OpenClaw 本地插件能力加载；TOS 下载、校验、灰度和回滚流程已从本示例中排除。

构建和安装时应确认运行时依赖 `@sinclair/typebox` 位于 `dependencies`，避免 OpenClaw 加载插件时报缺失依赖。

---

## 8. 常用命令与工具用法

### 8.1 插件命令

```bash
# 配置
openclaw openviking setup

# 非交互配置
openclaw openviking setup --base-url http://127.0.0.1:1933 --api-key sk-xxx --json

# 状态检查
openclaw openviking status --json

# 查看配置
openclaw config get plugins.entries.openviking.config
openclaw config get plugins.slots.contextEngine
```

### 8.2 Slash Commands

```text
/add-resource ./README.md --to viking://resources/openviking-readme --wait
/add-resource https://example.com/spec.html --parent viking://resources/project-docs --wait
/add-skill ./skills/install-openviking-memory --wait
/ov-search "OpenViking install" --uri viking://resources/openviking-readme
/ov-search "memory install skill" --uri viking://user/skills
```

### 8.3 Agent 工具触发场景

| 用户意图 | 推荐工具 |
| --- | --- |
| “记住这条偏好/事实” | `memory_store` |
| “你还记得我之前说过什么吗” | `memory_recall` |
| “忘掉那条记忆” | `memory_forget` |
| “把这个文档/目录/URL/仓库加入知识库” | 手动 `/add-resource`；只有显式开启 `enableAddResourceTool=true` 时才使用 `add_resource` |
| “把这个 skill 导入 OpenViking” | `add_skill` |
| “在 OpenViking 里搜一下资源/技能” | `ov_search` |
| “读取这个 OpenViking 命中 URI 的完整内容” | `ov_read` |
| “summary 里没有细节，回查历史” | `ov_archive_search` / `ov_archive_expand` |
| “这个 tool result 被截断了，读取完整内容” | `openviking_tool_result_read` / `search` / `list` |

---

## 9. 配置参数说明

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `mode` | `remote` | 兼容字段；当前仅支持 remote |
| `baseUrl` | `http://127.0.0.1:1933` | OpenViking HTTP 地址 |
| `apiKey` | 环境变量或空 | OpenViking API Key |
| `accountId` | 空 | Root key/trusted 部署需要 |
| `userId` | 空 | Root key/trusted 部署需要 |
| `peer_role` | `none` | 控制 session message 和 recall/search 请求是否写 `peer_id`：`none`、`assistant` 或 `person` |
| `peer_prefix` | 空 | `peer_role=assistant` 时 assistant `peer_id` 的可选前缀 |
| `targetUri` | `viking://user/memories` | 默认 memory search 目标 |
| `timeoutMs` | `15000` | HTTP 请求超时 |
| `autoCapture` | `true` | 是否每轮后写入 OpenViking session |
| `captureMode` | `semantic` | `semantic` 全量候选；`keyword` 先过触发词 |
| `captureMaxLength` | `24000` | 自动捕获文本最大长度 |
| `autoRecall` | `true` | 是否回复前自动召回 |
| `recallTargetTypes` | `["user","agent"]` | 自动召回和默认显式召回目标类型；可选 `resource`、`user`、`agent` |
| `recallResources` | `false` | 旧兼容开关；仅在未显式配置 `recallTargetTypes` 时追加 `resource` |
| `recallLimit` | `6` | 召回条数 |
| `recallScoreThreshold` | `0.15` | 召回阈值 |
| `recallMaxInjectedChars` | `4000` | 注入字符预算 |
| `commitTokenThreshold` | `20000` | `pending_tokens` 超过该值触发 afterTurn commit；设 0 可每轮 commit |
| `commitKeepRecentCount` | `10` | afterTurn commit 后保留最近消息数；compact 固定 0 |
| `bypassSessionPatterns` | `[]` | 匹配 sessionKey/sessionId 时完全绕过 OpenViking |
| `emitStandardDiagnostics` | `false` | 输出 `openviking: diag {...}` 结构化诊断日志 |
| `logFindRequests` | `false` | 输出 routing/search/session 写入日志；也可用 `OPENVIKING_LOG_ROUTING=1` 或 `OPENVIKING_DEBUG=1` |
| `traceRecall` | `false` | Recall trace 总开关；不开启时不记录、不建目录、查询只返回未启用提示 |
| `traceRecallPersist` | `false` | 是否把 trace 按日期追加写入 JSONL |
| `traceRecallDir` | `~/.openclaw/openviking/recall-traces` | trace JSONL 保存目录 |
| `traceRecallRetentionDays` | `14` | trace 文件保留天数 |
| `traceRecallLoadRecentDays` | `2` | gateway 启动时预加载最近多少天 trace |
| `traceRecallMaxEntries` | `1000` | 内存 ring buffer 最大 trace 条数 |
| `traceRecallMaxResultsPerSearch` | `20` | 每个 search 保存的候选结果上限 |
| `traceRecallPreviewChars` | `240` | trace 摘要 preview 截断长度 |
| `traceRecallQueryMaxChars` | `4000` | trace 中保存 query 的最大长度 |
| `traceRecallQueryMaxDays` | `14` | 查询持久化 trace 时默认最多扫描多少天 |
| `traceRecallIncludeContentByDefault` | `false` | 查询 trace 时是否默认读取完整内容 |
| `traceRecallIncludeRawUserPreview` | `false` | 是否把原始用户消息 preview 写入持久化层 |

环境变量解析逻辑在 `config.ts:139`、`config.ts:147`。

### 9.1 搜索 / 召回相关配置总表

如果你关注的是“插件里的搜索能力可以怎么从外部设定”，可以按下面这张表看。这里的“搜索”包括三类：

- 自动召回：回复前自动查长期记忆 / resources 并注入 `<relevant-memories>`
- 显式检索：`memory_recall`、`ov_search`、`ov_archive_search`
- 搜索诊断：打开 routing / find 日志，排查“为什么没搜到 / 搜到了但没注入”

| 配置项 | 作用范围 | 默认值 | 可否写入插件配置文件 | 可否用环境变量 | 环境变量名 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| `baseUrl` | 所有搜索/召回请求 | `http://127.0.0.1:1933` | 是 | 是 | `OPENVIKING_BASE_URL` / `OPENVIKING_URL` | OpenViking 服务地址；所有 `find/read/grep/session` 都依赖它：`config.ts:139` |
| `apiKey` | 所有搜索/召回请求 | 空 | 是 | 是 | `OPENVIKING_API_KEY` | HTTP 认证 key；不配通常只能访问关闭认证的本地服务：`config.ts:202` |
| `accountId` | 多租户搜索路由 | 空 | 是 | 是 | `OPENVIKING_ACCOUNT_ID` | Root key / trusted 部署下显式指定 account，影响搜索命中空间：`config.ts:212` |
| `userId` | 多租户搜索路由 | 空 | 是 | 是 | `OPENVIKING_USER_ID` | Root key / trusted 部署下显式指定 user，影响 user memory 检索范围：`config.ts:216` |
| `peer_role` | session 写入和 recall/search peer 路由 | `none` | 是 | 否 | — | `none` 不写 `peer_id`；`assistant` 使用 runtime agent；`person` 使用 OpenClaw sender 身份：`config.ts:430` |
| `peer_prefix` | assistant peer id 前缀 | 空 | 是 | 否 | — | `peer_role=assistant` 时可把 assistant `peer_id` 写成 `<peer_prefix>_<agentId>`：`config.ts:431` |
| `targetUri` | `memory_recall` / `memory_forget` 默认搜索范围 | `viking://user/memories` | 是 | 否 | — | 未显式传 `targetUri` 时的默认 memory 搜索位置：`config.ts:275`、`index.ts:1366` |
| `timeoutMs` | 所有搜索/读取请求超时 | `15000` | 是 | 否 | — | 控制 `find/read/grep/session` 等 HTTP 请求超时：`config.ts:276` |
| `autoRecall` | 自动召回总开关 | `true` | 是 | 否 | — | 关闭后插件不再在 `assemble()` 阶段自动发起 recall：`config.ts:283`、`context-engine.ts:1132` |
| `recallTargetTypes` | 自动召回 + 默认 `memory_recall` 资源类型集合 | `["user","agent"]` | 是 | 安装脚本/setup 参数支持 | `OPENVIKING_RECALL_TARGET_TYPES`（安装脚本写入 setup 参数） | 当前默认只查 `user` + `agent` 记忆。设置为 `["resource"]` 才会切成 resource-only；可组合 `resource,user,agent`：`config.ts:174`、`config.ts:360` |
| `recallResources` | 自动召回 + 默认 `memory_recall` resources 兼容开关 | `false` | 是 | 是 | `OPENVIKING_RECALL_RESOURCES` | 旧兼容字段；只有未显式配置 `recallTargetTypes` 时才把 `resource` 追加到默认 `user` + `agent`，不会覆盖显式 resource-only：`config.ts:360` |
| `recallLimit` | 自动召回 / `memory_recall` 返回条数 | `6` | 是 | 否 | — | 最终注入或展示的 recall 条数上限；内部请求会放大到 `max(limit*4, 20)` 先召回再重排：`config.ts:285`、`auto-recall.ts:181` |
| `recallScoreThreshold` | 自动召回 / `memory_recall` 过滤阈值 | `0.15` | 是 | 否 | — | 低于阈值的结果会在后处理阶段被丢弃：`config.ts:286`、`auto-recall.ts:218`、`index.ts:1132` |
| `recallMaxInjectedChars` | 自动召回 / `memory_recall` 注入预算 | `4000` | 是 | 否 | — | 控制最终可注入/展示的总字符数；放不下的完整 memory 会被跳过，不再截断单条：`config.ts:252`、`auto-recall.ts:228` |
| `recallPreferAbstract` | 自动召回读取策略 | `false` | 是 | 否 | — | 为 `true` 时优先使用 abstract，不强制回源读取 L2 全文，能减少 token 和读取耗时：`config.ts:294`、`auto-recall.ts:232` |
| `recallTokenBudget` | 自动召回预算旧别名 | 跟随 `recallMaxInjectedChars` | 是 | 否 | — | 已废弃，仅兼容旧配置；解析时会折叠为 `recallMaxInjectedChars`：`config.ts:257`、`config.ts:299` |
| `recallMaxContentChars` | 旧版单条截断兼容项 | `5000` | 是 | 否 | — | 已废弃；当前自动召回不再裁剪单条 memory 内容：`config.ts:290` |
| `captureMode` | 间接影响可搜索记忆的入库方式 | `semantic` | 是 | 否 | — | 虽然不是“搜索参数”，但它决定哪些用户内容会先被写入 session 并进入后续可检索空间：`config.ts:203`、`config.ts:278` |
| `captureMaxLength` | 间接影响可搜索记忆来源长度 | `24000` | 是 | 否 | — | 超过该长度的用户文本不会完整进入自动捕获链路：`config.ts:279` |
| `bypassSessionPatterns` | 绕过搜索/召回 | `[]` | 是 | 否 | — | 命中指定 sessionId / sessionKey 时，插件整条 OpenViking 链路直接跳过，包括 recall、store、archive search：`config.ts:311` |
| `logFindRequests` | 搜索调试日志 | `false` | 是 | 是 | `OPENVIKING_LOG_ROUTING` / `OPENVIKING_DEBUG` | 打开后会记录 `POST /api/v1/search/find`、session 写入和 commit 路由信息，便于排查检索空间错误：`config.ts:322` |
| `enabledTools` | Agent 可见工具白名单 | `default` 工具组 | 是 | 否 | — | 支持工具名或分组：`default`、`all`、`memory`、`resource_query`、`import`、`recall_trace`、`archive`、`tool_result`。例如只保留资源查询：`["resource_query"]`。`add_resource` 即使被选中仍需 `enableAddResourceTool=true`：`config.ts:119`、`index.ts:688` |
| `disabledTools` | Agent 可见工具黑名单 | `[]`（`add_resource` 默认仍禁用） | 是 | 否 | — | 在 `enabledTools` 之后应用，支持同样的工具名或分组。例如保留默认工具但隐藏记忆相关工具：`["memory"]`，会禁用 `memory_recall` / `memory_store` / `memory_forget`：`config.ts:136`、`config.ts:268` |

### 9.2 哪些配置只能走配置文件，哪些可以直接走环境变量

#### 9.2.1 可直接通过环境变量生效的搜索相关项

| 环境变量 | 对应配置项 | 作用 |
| --- | --- | --- |
| `OPENVIKING_BASE_URL` | `baseUrl` | 指定 OpenViking 服务地址 |
| `OPENVIKING_URL` | `baseUrl` | `baseUrl` 的兼容别名 |
| `OPENVIKING_API_KEY` | `apiKey` | 指定 OpenViking API key |
| `OPENVIKING_ACCOUNT_ID` | `accountId` | 指定租户 account |
| `OPENVIKING_USER_ID` | `userId` | 指定租户 user |
| `OPENVIKING_RECALL_RESOURCES` | `recallResources` | 是否把 resources 纳入自动召回和默认 memory_recall |
| `OPENVIKING_LOG_ROUTING` | `logFindRequests` | 打开检索/路由日志 |
| `OPENVIKING_DEBUG` | `logFindRequests` | 同时作为调试总开关，当前也会打开 routing/find 日志 |

#### 9.2.2 只能通过插件配置文件设定的搜索行为项

这些项当前**没有独立环境变量**，需要写到 `~/.openclaw/openclaw.json` 里的 `plugins.entries.openviking.config`：

- `targetUri`
- `timeoutMs`
- `autoRecall`
- `recallTargetTypes`（可通过安装脚本 `--recall-target-types` 或 setup CLI 参数写入配置，但运行时不是直接读环境变量）
- `recallLimit`
- `recallScoreThreshold`
- `recallMaxInjectedChars`
- `recallPreferAbstract`
- `recallTokenBudget`（废弃兼容）
- `recallMaxContentChars`（废弃兼容）
- `captureMode`
- `captureMaxLength`
- `bypassSessionPatterns`

### 9.3 推荐配置示例

#### 9.3.1 Resource-only 召回配置

当前默认召回目标是用户记忆 + Agent 记忆：`["user","agent"]`。如果你的场景主要是“导入文档 / 知识库问答”，并希望默认召回只查 `viking://resources`，需要显式配置 `recallTargetTypes`：

```bash
openclaw config set plugins.entries.openviking.config.recallTargetTypes '["resource"]'
openclaw gateway restart
```

安装后也可以通过 `openclaw config set` 显式写入；本示例不包含 TOS 安装脚本参数。

注意：`recallResources=true` 是旧兼容加法开关，只会在未显式配置 `recallTargetTypes` 时把 `resource` 追加到默认 `user` + `agent`，不会把默认召回改成 resource-only。

#### 9.3.2 仅通过环境变量快速打开“额外可搜 resources 的自动召回”

```bash
export OPENVIKING_BASE_URL="http://127.0.0.1:1933"
export OPENVIKING_API_KEY="<YOUR_KEY>"
export OPENVIKING_RECALL_RESOURCES=1
```

适合：你已经有稳定的 `openclaw.json`，只想临时把 `viking://resources` 追加到自动召回和默认 `memory_recall`，同时保留默认 `user` + `agent` 记忆召回。

#### 9.3.3 通过插件配置文件精细控制召回和 trace

```json
{
  "plugins": {
    "entries": {
      "openviking": {
        "config": {
          "baseUrl": "${OPENVIKING_BASE_URL}",
          "apiKey": "${OPENVIKING_API_KEY}",
          "targetUri": "viking://user/memories",
          "autoRecall": true,
          "recallTargetTypes": ["user", "agent", "resource"],
          "recallLimit": 8,
          "recallScoreThreshold": 0.2,
          "recallMaxInjectedChars": 6000,
          "recallPreferAbstract": true,
          "logFindRequests": true,
          "traceRecall": true,
          "traceRecallPersist": true
        }
      }
    }
  }
}
```

这里有两个关键点：

- `baseUrl` / `apiKey` 支持在配置文件里写 `${ENV}` 占位，加载时会做环境变量替换：`config.ts:82`
- 但 `recallTargetTypes` / `recallLimit` / `recallScoreThreshold` / `autoRecall` / `traceRecall` 这类行为项不会从环境变量自动读取，仍以配置文件为准。
- 开启 recall trace 必须显式设置 `traceRecall=true`；只设置 `recallTargetTypes` 或 `recallResources` 不会启用 trace。

### 9.4 外部配置生效顺序

搜索相关配置的实际生效顺序可以概括为：

1. **显式工具参数优先**：例如 `memory_recall(limit=3, scoreThreshold=0.4, targetUri=...)`、`/ov-search --limit 20 --uri ...` 会优先覆盖默认配置：`index.ts:1046`、`index.ts:1050`、`index.ts:1054`、`index.ts:824`、`index.ts:408`
2. **插件配置文件其次**：`plugins.entries.openviking.config.*`
3. **环境变量补默认值**：只对少数支持 env 的项生效，如 `OPENVIKING_BASE_URL`、`OPENVIKING_API_KEY`、`OPENVIKING_RECALL_RESOURCES`：`config.ts:139`、`config.ts:202`、`config.ts:284`
4. **代码默认值兜底**：例如 `recallLimit=6`、`recallScoreThreshold=0.15`、`recallMaxInjectedChars=4000`：`config.ts:63`、`config.ts:64`、`config.ts:67`

### 9.5 搜索相关配置的排查建议

| 现象 | 优先看哪些配置 | 典型原因 |
| --- | --- | --- |
| `memory_recall` 能搜到 memory，但自动回复前没有注入 | `autoRecall`、`recallScoreThreshold`、`recallMaxInjectedChars` | recall 命中了，但因阈值或预算被过滤掉 |
| `memory_recall` 默认搜不到 resources | `recallResources` | 默认是 `false`，不会自动查 `viking://resources` |
| 同样的 query 在不同 agent / user 命中不一致 | `accountId`、`userId`、`peer_role`、`peer_prefix` | 租户或 `peer_id` 路由不一致 |
| `/ov-search` 查不到刚导入的内容 | `baseUrl`、`apiKey`、服务端队列状态 | 导入后语义/向量处理还没完成，或连到了错误服务 |
| 明明命中结果很多，但注入数量少 | `recallLimit`、`recallMaxInjectedChars`、`recallPreferAbstract` | limit 太小或预算太紧，必要时改成 abstract 优先 |
| 不知道插件到底向哪个 target_uri 发了检索 | `logFindRequests` | 开启后查看插件日志中的 `find POST` |

---

## 10. Debug 与排障

### 10.1 快速状态检查

```bash
openclaw openviking status --json
openclaw plugins list
openclaw config get plugins.entries.openviking.config
openclaw config get plugins.slots.contextEngine
curl http://127.0.0.1:1933/health
```

### 10.2 打开插件侧路由日志

配置方式：

```bash
openclaw config set plugins.entries.openviking.config.logFindRequests true
openclaw gateway restart
```

或临时环境变量：

```bash
OPENVIKING_LOG_ROUTING=1 openclaw gateway restart
```

日志会打印 account/user header 是否设置、target_uri、`peer_id`、query preview、session commit 等信息，但不会打印 apiKey。

### 10.3 打开标准诊断日志

```bash
openclaw config set plugins.entries.openviking.config.emitStandardDiagnostics true
openclaw gateway restart
```

然后在日志中搜索：

```text
openviking: diag {"stage":"assemble_entry"...}
openviking: diag {"stage":"assemble_result"...}
openviking: diag {"stage":"afterTurn_entry"...}
openviking: diag {"stage":"afterTurn_commit"...}
openviking: diag {"stage":"compact_result"...}
```

### 10.4 对话时观测召回与文档命中

如果需要在与 OpenClaw 对话时确认“本轮到底从 OpenViking 召回了哪些数据、用了哪些文档、对应路径是什么”，推荐按以下顺序排查。

#### 10.4.0 启用 recall trace

Recall trace 是独立的可观测能力，必须打开 `traceRecall=true` 才会记录；只设置 `recallResources` 或 `recallTargetTypes` 只会改变召回范围，不会自动启用 trace。

```bash
# 只保留当前 gateway 进程内的内存 trace
openclaw config set plugins.entries.openviking.config.traceRecall true

# 如需 gateway 重启后还能查历史 trace，再打开持久化
openclaw config set plugins.entries.openviking.config.traceRecallPersist true
openclaw config set plugins.entries.openviking.config.traceRecallDir ~/.openclaw/openviking/recall-traces

openclaw gateway restart
```

查询方式：

```text
# Agent tool
ov_recall_trace(query="用户问题关键词", limit=10)

# Slash command
/ov-recall-trace --query "用户问题关键词" --limit 10

# Gateway route adapter 可用时
GET /api/openviking/recall-traces
GET /api/openviking/recall-traces/<traceId>
```

排查边界：

| 配置状态 | 行为 |
| --- | --- |
| `traceRecall=false` | 不记录 trace，不创建 trace 目录；查询工具返回 trace 未启用提示 |
| `traceRecall=true && traceRecallPersist=false` | 只查当前 gateway 进程内的 ring buffer；重启后丢失 |
| `traceRecall=true && traceRecallPersist=true` | trace 追加写入按日期分片的 JSONL；重启后按配置预加载最近记录，并可查询 retention 范围内文件 |

#### 10.4.1 先打开插件侧可观测配置

```bash
# 打印 OpenViking search/session 路由、target_uri、query、agent/account/user header 等
openclaw config set plugins.entries.openviking.config.logFindRequests true

# 打印 assemble/afterTurn/compact 标准诊断
openclaw config set plugins.entries.openviking.config.emitStandardDiagnostics true

# 如果希望自动召回只查导入的文档/URL/目录资源，设置 resource-only
openclaw config set plugins.entries.openviking.config.recallTargetTypes '["resource"]'

# 如果希望保留默认 user/agent 记忆，同时额外查 resources，也可使用旧兼容加法开关
openclaw config set plugins.entries.openviking.config.recallResources true

# 如果希望记录本轮召回详情，必须显式打开 trace
openclaw config set plugins.entries.openviking.config.traceRecall true

openclaw gateway restart
```

也可以临时用环境变量打开路由日志：

```bash
OPENVIKING_LOG_ROUTING=1 openclaw gateway restart
# 或
OPENVIKING_DEBUG=1 openclaw gateway restart
```

#### 10.4.2 看日志中的关键字段

一次自动召回通常会产生三类可观测信息：

| 日志/字段 | 含义 | 关键路径 |
| --- | --- | --- |
| `openviking: find POST .../api/v1/search/find {...}` | 插件向 OpenViking 发起了语义检索 | `target_uri` / `target_uri_input` / `query` / `X_OpenViking_Agent` |
| `openviking: injecting N memories ...` | 插件决定向本轮 prompt 注入 N 条召回内容 | `N`、注入字符数、估算 token |
| `openviking: inject-detail {...}` | 本轮实际注入模型的召回条目摘要 | `memories[].uri`、`category`、`score`、`abstract`、`is_leaf` |
| `openviking: diag {"stage":"assemble_result"...}` | assemble 阶段是否发生自动召回 | `phase=transform_context`、`autoRecallMemoryCount` |

其中 `inject-detail` 是排查“本轮模型实际看到了哪些 OpenViking 召回内容”的首选入口。它会列出每条被注入内容的 `uri`，例如：

```text
openviking: inject-detail {"count":2,"memories":[{"uri":"viking://user/default/memories/preferences/...","category":"preferences","abstract":"...","score":0.82,"is_leaf":true},{"uri":"viking://resources/project-docs/api.md#chunk-3","category":"resource","abstract":"...","score":0.71,"is_leaf":true}]}
```

注意：自动注入到模型输入里的 `<relevant-memories>` 块默认只包含类别和内容，不直接暴露 URI；URI/路径主要从插件日志、`memory_recall` / `ov_search` 工具 `details`、或 OpenViking API 返回中获取。

#### 10.4.3 用 OpenClaw 工具显式复现召回

如果想把“命中的路径”直接展示给 Agent 或用户，可以让 Agent 显式调用工具：

```text
# 查长期记忆，默认查 user/agent memories；recallTargetTypes 可切换默认范围
memory_recall(query="用户问题关键词", limit=10)

# 查导入的文档、URL、目录、仓库资源
ov_search(query="用户问题关键词", uri="viking://resources", limit=10)

# 查 OpenViking skills
ov_search(query="用户问题关键词", uri="viking://user/skills", limit=10)
```

`ov_search` 的文本结果会显示 `type`、`uri`、`level`、`score` 和摘要；工具 `details` 里也会保留原始 `resources[]` / `skills[]` / `memories[]` 数组。

注意：这些 `uri` 是 OpenViking 虚拟 URI，不是本地文件路径。需要完整内容时，让 Agent 调用 `ov_read(uri="viking://...")`，不要把 `viking://...` 或历史兼容展示里的 `openviking://...` 当作本地路径交给文件读取工具。

#### 10.4.4 直接调用 OpenViking API 获取路径和内容

插件调用 OpenViking 时统一携带认证和路由 header。手工排查时也要保持一致：

```bash
export OPENVIKING_BASE_URL="http://127.0.0.1:1933"
export OPENVIKING_API_KEY="<your-api-key>"

# root key / trusted server 场景按需补充
export OPENVIKING_ACCOUNT_ID="<account-id>"
export OPENVIKING_USER_ID="<user-id>"
```

语义检索并获取命中 URI：

```bash
headers=(
  -H "Content-Type: application/json"
  -H "X-API-Key: $OPENVIKING_API_KEY"
)

if [ -n "${OPENVIKING_ACCOUNT_ID:-}" ]; then
  headers+=( -H "X-OpenViking-Account: $OPENVIKING_ACCOUNT_ID" )
fi
if [ -n "${OPENVIKING_USER_ID:-}" ]; then
  headers+=( -H "X-OpenViking-User: $OPENVIKING_USER_ID" )
fi

curl -sS "$OPENVIKING_BASE_URL/api/v1/search/find" \
  "${headers[@]}" \
  -d '{
    "query": "用户问题关键词",
    "target_uri": "viking://resources",
    "limit": 10,
    "score_threshold": 0.15
  }'
```

典型返回中需要关注：

```json
{
  "resources": [
    {
      "uri": "viking://resources/project-docs/api.md#chunk-3",
      "level": 2,
      "score": 0.71,
      "abstract": "命中的段落摘要",
      "category": "resource"
    }
  ],
  "memories": [],
  "skills": [],
  "total": 1
}
```

拿到 `uri` 后读取完整内容：

```bash
curl -sS "$OPENVIKING_BASE_URL/api/v1/content/read?uri=$(python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=""))' 'viking://resources/project-docs/api.md#chunk-3')" \
  "${headers[@]}"
```

#### 10.4.5 OpenViking API 速查表

完整官方 API 清单、参数说明和插件映射见 [6.2 OpenViking 官方 API 完整清单与插件映射](#62-openviking-官方-api-完整清单与插件映射)。本节只保留排查“召回了哪些数据 / 用了哪些文档”时最常用的调用。

| 目标 | API | 插件入口 | 用途 |
| --- | --- | --- | --- |
| 健康检查 | `GET /health` | `openclaw openviking status` | 判断服务是否可达 |
| 身份/用户探测 | `GET /api/v1/system/status` | `client.getRuntimeIdentity` | 解析服务端当前 user，辅助 canonical URI 展开 |
| 语义检索 | `POST /api/v1/search/find` | auto recall / `memory_recall` / `ov_search` | 返回 `memories[]`、`resources[]`、`skills[]`，每项含 `uri`、`score`、`abstract`、`level` |
| 内容读取 | `GET /api/v1/content/read?uri=...` | 自动召回读取 L2 内容 / `ov_read` / 手工排查 | 根据命中的 `viking://...` URI 读取完整内容 |
| 写入 session 消息 | `POST /api/v1/sessions/{sessionId}/messages` | `afterTurn` | 保存 OpenClaw 本轮 user/assistant/tool 片段 |
| 获取 session 元信息 | `GET /api/v1/sessions/{sessionId}` | `afterTurn` | 查看 `pending_tokens`、message count、commit count |
| 获取组装上下文 | `GET /api/v1/sessions/{sessionId}/context?token_budget=...` | 主 assemble / compact | 获取 archive summary + active messages |
| session commit | `POST /api/v1/sessions/{sessionId}/commit` | `afterTurn` / `compact` | 归档会话并触发 Phase 2 记忆抽取 |
| 查询异步任务 | `GET /api/v1/tasks/{taskId}` | Phase 2 轮询 | 查看 memory extraction 是否完成、失败或超时 |
| 展开 archive | `GET /api/v1/sessions/{sessionId}/archives/{archiveId}` | `ov_archive_expand` | 回看某个 archive 的原始消息 |
| archive grep | `POST /api/v1/search/grep` | `ov_archive_search` | 在 session archive 原文中关键词搜索 |
| 上传本地资源 | `POST /api/v1/resources/temp_upload` | `/add-resource`；`add_resource` 仅 opt-in | 本地文件/目录先临时上传，目录会先 zip |
| 导入 resource | `POST /api/v1/resources` | `/add-resource`；`add_resource` 仅 opt-in | 将文档、URL、目录、仓库导入 `viking://resources` |
| 导入 skill | `POST /api/v1/skills` | `add_skill` / `/add-skill` | 将 skill 写入 `viking://user/skills` |
| 外置工具结果列表 | `GET /api/v1/sessions/{sessionId}/tool-results` | `openviking_tool_result_list` | 查看当前 session 外置工具输出 |
| 外置工具结果搜索 | `GET /api/v1/sessions/{sessionId}/tool-results/{id}/search?q=...` | `openviking_tool_result_search` | 在大工具结果里关键词搜索 |
| 外置工具结果读取 | `GET /api/v1/sessions/{sessionId}/tool-results/{id}` | `openviking_tool_result_read` | 分页读取完整工具输出 |

#### 10.4.6 判断“用了哪些文档”的边界

- 如果文档是通过 **auto recall** 进入模型上下文：看 `inject-detail` 中 `category=resource` 或 `uri` 以 `viking://resources` 开头的条目。
- 如果文档是通过 **显式工具** 进入模型上下文：看 `ov_search` 工具结果里的 `uri` 和工具 `details.resources[]`。
- 如果只看到了 `find POST` 但没有 `injecting` / `inject-detail`：说明插件发起了检索，但最终可能因为分数阈值、去重、leaf 过滤或 `recallMaxInjectedChars` 预算没有注入模型。
- 如果未显式配置 `recallTargetTypes`，自动召回默认只查 `viking://user/memories` 和 `viking://agent/memories`，不会把 `viking://resources` 文档自动注入；resource-only 用 `recallTargetTypes=["resource"]`，默认记忆 + resources 用 `recallResources=true` 或 `recallTargetTypes=["user","agent","resource"]`。
- 如果没查到 recall trace，先检查 `traceRecall=true` 是否已配置并重启 Gateway；`recallTargetTypes` / `recallResources` 不负责启用 trace。
- 当前插件没有单独生成“模型最终引用/采纳哪些文档”的 citation 文件；最可靠的依据是本轮注入内容、工具调用结果、OpenViking API 返回和模型回复本身。

### 10.5 常见问题定位

| 现象 | 优先检查 | 可能原因 |
| --- | --- | --- |
| 插件未生效 | `plugins.slots.contextEngine` | slot 没有指向 `openviking` 或被其他插件覆盖 |
| `setup` 成功但 gateway 中没调用插件 | `openclaw gateway restart` | Gateway 未重启，仍用旧插件状态 |
| `status` 服务不可达 | `baseUrl`、`curl /health` | OpenViking 未启动、端口/网络错误 |
| Root key 报 tenant 错误 | `accountId/userId` | Root key 需要显式租户上下文 |
| 不同说话人记忆串用 | `logFindRequests` 中的 `peer_id` | `peer_role` / `peer_prefix` 或 sender 解析不符合预期 |
| 搜不到刚保存的记忆 | 服务端 task 状态和日志 | afterTurn commit 是异步 Phase 2，记忆抽取可能还未完成或服务端失败 |
| summary 有但细节没有 | `ov_archive_search` / `ov_archive_expand` | Working Memory 是有损摘要，需要 archive 回查 |
| auto recall 没注入 | `autoRecall`、precheck、阈值、预算 | OpenViking 不可达、query 太短、阈值太高、记忆超预算 |
| 工具结果缺完整内容 | tool result ref | 用 `openviking_tool_result_read`，不要反复读截断 preview |
| 本地目录导入失败 | 路径、权限、zip 打包日志 | 目录会先 zip 再 temp upload，需本地可读 |

### 10.6 OpenViking 服务侧排查

```bash
# 服务端日志，路径以实际部署为准
tail -f ~/.openviking/data/log/openviking.log

# Web Console
python -m openviking.console.bootstrap \
  --host 0.0.0.0 \
  --port 8020 \
  --openviking-url http://127.0.0.1:1933

# TUI
ov tui
```

---

## 11. 验证与测试

### 11.1 仓库本地验证

```bash
npm install
npm run typecheck
npm test
npm run build
```

当前 `package.json` 中提供的脚本：`build`、`test`、`typecheck`：`package.json:36`。

### 11.2 关键单测覆盖方向

| 测试文件 | 覆盖重点 |
| --- | --- |
| `tests/ut/config.test.ts` | 配置默认值、环境变量、peer 配置 |
| `tests/ut/setup-command.test.ts` / `setup-cli.test.ts` | setup/status、slot 激活、root key 探测 |
| `tests/ut/context-engine-*.test.ts` | assemble/afterTurn/compact、消息合并、预算、工具配对 |
| `tests/ut/memory-ranking.test.ts` | 召回排序、去重、阈值 |
| `tests/ut/tools.test.ts` | 工具注册、memory/resource/skill/tool-result 行为 |
| `tests/ut/tool-round-trip.test.ts` | toolCall/toolResult 往返与外置 ref 保留 |
| `tests/ut/manifest-contracts.test.ts` | manifest/package contract |
| `tests/ut/package-install-contract.test.ts` | 包安装契约 |

### 11.3 插件链路验证

```bash
openclaw openviking status --json
openclaw config get plugins.slots.contextEngine
```

如果需要完整链路验证，可以运行健康检查脚本：

```bash
python health_check_tools/ov-healthcheck.py
```

该脚本用于注入真实对话，并在 OpenViking 侧验证会话捕获、提交、归档和记忆抽取。说明见 `health_check_tools/HEALTHCHECK-ZH.md`。

### 11.4 手工端到端验证建议

1. 安装并配置插件。
2. 打开 `logFindRequests` 和 `emitStandardDiagnostics`。
3. 与 Agent 对话输入一条明确偏好，例如“记住：我喜欢用中文回复技术文档”。
4. 等待 afterTurn 或手动触发 `/compact`。
5. 新开一轮问“我之前偏好什么语言回复技术文档？”。
6. 观察最新 user message 是否注入 `<relevant-memories>`，或用 `memory_recall` 显式查。
7. 用 OpenViking Console/TUI 检查 `viking://user/.../memories` 是否产生 leaf memory。
8. 对长工具输出场景，确认 preview 中有 `viking://user/sessions/.../tool-results/...`，再用 tool-result 工具读取完整内容。

---

## 12. 注意事项

1. **插件只支持 remote 模式**：旧 local mode 会被迁移提示，不会启动本地 OpenViking 进程。
2. **必须重启 Gateway**：安装或配置后要 `openclaw gateway restart` 才能生效。
3. **不要装错包**：`@openviking/openclaw-plugin` 是插件；`clawhub install openviking` 是 AgentSkill。
4. **API Key 不进日志**：插件路由日志不会打印 key，但仍应避免把 key 写入公开文档或命令历史。
5. **Root Key 需要租户上下文**：若服务端要求 account/user header，必须配置 `accountId` 和 `userId`。
6. **peer 配置要一致**：需要说话人隔离时，确认 `peer_role` / `peer_prefix` 与预期一致，否则会导致写查不一致。
7. **afterTurn commit 是异步抽取**：立即返回不代表长期记忆已可检索；看 task 或服务端日志。
8. **compact 是同步边界**：需要明确压缩和抽取完成时用 compact，但它会阻塞等待服务端 Phase 2。
9. **记忆注入有预算**：`recallMaxInjectedChars` 会跳过放不下的完整记忆，而不是截断。
10. **bypassSessionPatterns 会完全绕过 OpenViking**：匹配后自动捕获、召回、工具都会跳过。
11. **tool result 工具限制当前 session**：插件拒绝读取其他 session 的外置结果。
12. **本地资源导入先上传**：本地文件/目录通过 temp upload，不把本地路径直接交给服务端；目录会 zip，注意权限与体积。

---

## 13. 维护者代码阅读路线

建议按以下顺序阅读：

1. `openclaw.plugin.json`：了解插件声明、工具 contract、配置 schema。
2. `package.json`：了解构建、OpenClaw 入口、兼容版本。
3. `commands/setup.ts`：了解用户安装配置如何写入 OpenClaw config。
4. `index.ts`：了解插件注册、工具、hook 和 service。
5. `client.ts`：了解 OpenViking API 封装和 header/URI 处理。
6. `context-engine.ts`：理解 assemble/afterTurn/compact 主链路。
7. `auto-recall.ts` + `memory-ranking.ts`：理解召回注入和排序。
8. `text-utils.ts` + `session-transcript-repair.ts`：理解消息清洗与 transcript 结构修复。
9. `tests/ut/*`：用测试反向确认 contract。

---

## 14. 快速排障 Checklist

- [ ] OpenViking Server `GET /health` 可达。
- [ ] `openclaw openviking status --json` 中 `configured=true`。
- [ ] `slotActive=true`。
- [ ] Gateway 已重启。
- [ ] `plugins.entries.openviking.config.baseUrl` 指向正确服务。
- [ ] root key 场景已配置 `accountId/userId`。
- [ ] 需要说话人隔离时，`peer_role` / `peer_prefix` 与预期一致。
- [ ] `autoCapture/autoRecall` 未被关闭。
- [ ] 当前 session 没有命中 `bypassSessionPatterns`。
- [ ] `pending_tokens` 是否达到 `commitTokenThreshold`。
- [ ] Phase 2 task 是否 completed。
- [ ] 需要细节时是否使用 archive 工具回查。

---

## 15. 构建、测试和本地验证流程

本章面向维护者，按“改代码 → 本地验证 → 构建 → 本地加载验证”的顺序给出最短路径。TOS 上传、下载、灰度、发布和回滚流程不属于本示例范围。

### 15.1 本地开发准备

```bash
git clone <repo-url>
cd arkclaw-openviking-plugin
node -v        # 需要 Node.js >= 22
npm install
```

核心脚本来自 `package.json`：

| 命令 | 作用 |
| --- | --- |
| `npm run typecheck` | 使用 `tsconfig.json` 做类型检查 |
| `npm test` | 运行 Vitest 单测 |
| `npm run build` | 使用 `tsconfig.build.json` 生成 `dist/` |
| `npm run build` | 使用 `tsconfig.build.json` 生成可由 OpenClaw 加载的 `dist/` |

### 15.2 本地测试

最小代码质量检查：

```bash
npm run typecheck
npm test
npm run build
```

构建后应确认 `dist/index.js` 和 `dist/commands/setup.js` 存在，且 `package.json` 中的 OpenClaw 入口指向 `dist/`。

重要契约：运行时依赖必须留在 `dependencies`，例如 `@sinclair/typebox` 和 `fflate`，避免 OpenClaw 运行时加载插件失败。

### 15.3 本地加载验证

```bash
npm run build
openclaw openviking setup --base-url http://127.0.0.1:1933 --api-key <OPENVIKING_API_KEY> --json
```

安装后检查：

```bash
openclaw openviking status --json
openclaw config get plugins.entries.openviking.config
openclaw config get plugins.slots.contextEngine
```

### 15.4 运行后 Debug Checklist

```bash
openclaw openviking status --json
openclaw config get plugins.entries.openviking.config
openclaw config get plugins.slots.contextEngine
curl <OPENVIKING_BASE_URL>/health
```

建议临时打开：

```bash
openclaw config set plugins.entries.openviking.config.logFindRequests true
openclaw config set plugins.entries.openviking.config.emitStandardDiagnostics true
openclaw gateway restart
```

如果排查“召回到底用了哪些结果”，再打开 trace：

```bash
openclaw config set plugins.entries.openviking.config.traceRecall true
openclaw config set plugins.entries.openviking.config.traceRecallPersist true
openclaw gateway restart
```

然后使用 `ov_recall_trace` / `/ov-recall-trace` 查询。注意：`traceRecall=true` 是 trace 总开关，配置召回范围（如 `recallTargetTypes=["resource"]`）不会自动打开 trace。

### 15.5 常见问题

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| OpenClaw 加载失败并提示缺依赖 | 包内缺运行时依赖 | 重新运行 `npm install` 和 `npm run build`，确认 `@sinclair/typebox` 位于 `dependencies` |
| status 不健康 | OpenViking Server 不可达或 key/租户错误 | 检查 `baseUrl`、`apiKey`、`accountId`、`userId`、服务端 `/health` |

---

## 16. 参考文档

- `README_CN.md`：项目中文快速说明。
- `INSTALL-ZH.md`：安装、升级、卸载指南。
- `INSTALL-AGENT.md`：Agent 自动安装说明。
- `docs/workmemory-v2-design.md`：Working Memory v2 设计。
- `docs/workmemory-v2-test-report.md`：Working Memory v2 测试报告。
- `health_check_tools/HEALTHCHECK-ZH.md`：健康检查脚本说明。
