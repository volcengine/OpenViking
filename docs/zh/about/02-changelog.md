# 更新日志

OpenViking 的所有重要变更都将记录在此文件中。
此更新日志从 [GitHub Releases](https://github.com/volcengine/OpenViking/releases) 自动生成。

## v0.3.12 (2026-04-24)

## What's Changed
* feat(semantic): add output_language_override to pin summary/overview language
* docs(bot): sync gateway config example with #1640 security defaults
* fix(parser): validate feishu config limits
* fix(code-hosting): recognize SSH repository hosts with userinfo
* docs(design): sync §4.6 role_id rules with #1643 passthrough behavior
* feat: 补充百炼记忆库 LoCoMo benchmark 评测脚本
* docs(contributing): add maintainer routing map
* Revert "feat: 补充百炼记忆库 LoCoMo benchmark 评测脚本"
* [fix] Fix/oc2ov test stability
* fix(server): map AGFS URI errors
* feat(git): added azure devops support
* feat: support trusted admin tenant management
* fix(session): count tool parts in pending tokens
* fix(user_id): support dot
* Add VitePress docs site and Pages deployment
* feat(parse): support larkoffice.com Feishu document URLs
* feat(ragfs): add s3 key normalization encoding
* fix: apikey security: API Key 管理重构与安全增强
* add Copy Markdown button and llms.txt support
* fix(api_keys): proxy get_user_role in NewAPIKeyManager (trusted-mode 500 regression)
* Fix docs npm registry for release builds
* fix: account name security

## v0.3.10 (2026-04-23)

### 版本概览

v0.3.10 重点增强了 VLM provider、OpenClaw 插件生态、VikingDB 数据面接入，以及文件写入、QueueFS、Bot/CLI 的稳定性。本次发布包含 46 个提交，覆盖新功能、兼容性修复、安全修复和测试补强。

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

### 文档、测试与安全

- 补充 VLM provider、Codex OAuth、Kimi/GLM、`write(mode=create)`、`tools.mcp_servers`、`ov_tools_enable`、Feishu thread 和 VLM timeout 文档。
- 新增资源构建、Context Engine、OpenClaw 插件、内容写入、VLM provider、setup wizard、server bootstrap 和安全相关测试。
- 新增 `SECURITY.md` 并更新 README、多语言文档和社群二维码。
- 修复多项 code scanning 和 runtime 安全告警。
- 增强 Bot gateway、OpenAPI auth、werewolf demo、配置校验和本地命令执行相关安全测试。

## v0.3.9 (2026-04-18)

## What's Changed
* reorg: remove golang depends
* Feat/mem opt
* fix: openai like embedding models fix, no more matryoshka error
* feat(bot): Add disable OpenViking config for channels.
* fix(config): point missing-config help messages to openviking.ai docs
* fix(embedder): initialize async client state in VolcengineSparseEmbedder
* feat(examples): add Codex memory plugin example
* feat(openclaw-plugin): add unified ov_import and ov_search
* feat(bot): add MCP client support (port from HKUDS/nanobot v0.1.5)
* feat(eval):Readme add qa
* feat(cli): support for default file/dir ignore config in `ovcli.conf`
* benchmark: add LoCoMo evaluation for Supermemory
* fix(embedder): report configured provider in slow-call logs
* fix(queue): preserve embedding message ids across serialization
* test(security): add unit tests for network_guard and zip_safe modules
* fix(semantic): preserve repository hierarchy in overviews
* fix(tests): align pytest coverage docs with required setup
* feat: rerank support extra headers
* fix: reload legacy session rows
* fix: protect global watch-task control files from non-root access
* fix(agfs): enable agfs s3 plugin default
* fix(claude-code-memory-plugin): improve Windows compatibility
* fix(pdf): resolve bookmark page mapping
* fix: update observer test to use /models endpoint instead of non-exis…
* fix(openclaw-plugin): extend default Phase 2 commit wait timeout
* pref(retrieve): Optimize the search performance of larger directories by skipping redundant target_directories scope
* Add third_party directory to Dockerfile
* Fix/openclaw addmsg
* feat(bot):Heartbeat fix
* feat: add `openviking-server init` interactive setup wizard for local Ollama model deployment
* fix(volcengine): update default doubao embedding model
* feat: add Memory V2 full suite test
* update new wechat group qr code
* feat(filesystem): support directory descriptions on mkdir
* feat(memory): default to memory v2
* fix: resolve OpenClaw session file lock conflicts in oc2ov tests
* fix: isolate temp scope by user within an account
* fix(docker): raise Rust toolchain for ragfs image builds
* feat(metric): add metric system
* openclaw refactor: assemble context partitioning (Instruction/Archive/Session/…
* Fix/memory v2
* Fix/memory v2
* reorg: split parser layer to 2-layer: accessor and parser, so that we can reuse more code
* fix: merge error

## v0.3.8 (2026-04-15)

### Memory V2 专题

- 记忆格式：
- 重构与优化：
- 对用户的直接价值：

### 重点更新

- Memory V2 默认开启：
- 本地部署与初始化体验：
- 插件与 Agent 生态增强：
- 配置与部署体验改进：
- 性能与稳定性：
- 其他补充：

### 升级提示

- 如果你经常通过 CLI 导入目录资源，建议在 `ovcli.conf` 中配置 `upload.ignore_dirs`，减少无关目录上传。
- 如果你需要保留旧行为，可在 `ov.conf` 中显式设置 `"memory": { "version": "v1" }` 回退到 legacy memory pipeline。
- 如果你之前使用 `ov init` 或 `ov doctor`，请改用 `openviking-server init` 和 `openviking-server doctor`。
- 如果你使用 OpenRouter 或其他 OpenAI 兼容 rerank/VLM 服务，可以通过 `extra_headers` 注入平台要求的 Header。
- 如果你的对象存储是阿里云 OSS 或其他 S3 兼容实现，且批量删除存在兼容问题，可开启 `storage.agfs.s3.disable_batch_delete`。
- 如果你在做 Agent 集成，建议查看 `examples/codex-memory-plugin` 与 `examples/openclaw-plugin` 中的新示例和工具能力。

## v0.3.5 (2026-04-10)

## What's Changed
* fix(memory): define config before v2 memory lock retry settings access
* fix: 优化测试关键词匹配和移除 Release Approval Gate
* fix: sanitize internal error details in bot proxy responses
* feat: add scenario-based API tests
* fix(security): remove leaked token from settings.py
* Fix/add resource cover
* Revert "Fix/add resource cover"
* fix: litellm embedding dimension adapts
* ci: optimize runner usage with conditional OS matrix and parallel limit
* fix(bot):Response language, Multi user memory commit
* ci: remove lite and full test workflows
* docs: fix docker deployment
* docs(openclaw-plugin): add health check tools guide
* fix(queue): expose re-enqueue counts in queue status
* feat(s3fs): add disable_batch_delete option for OSS compatibility
* Fix/add resource cover
* afterTurn: store messages with actual roles and skip heartbeat messages
* fix: fall back to prefix filters for volcengine path scope
* fix(session): auto-create missing sessions on first add
* Fix/api test issues
* fix: derive context_type from URI in index_resource

## v0.3.4 (2026-04-09)

# OpenViking v0.3.4

## 版本亮点

- **OpenClaw 插件与评测体验继续完善**：调整 `recallPreferAbstract` 与 `ingestReplyAssist` 的默认值以降低意外行为，[PR #1204](https://github.com/volcengine/OpenViking/pull/1204) [PR #1206](https://github.com/volcengine/OpenViking/pull/1206)；新增 OpenClaw eval shell 脚本并修复评测导入问题，[PR #1287](https://github.com/volcengine/OpenViking/pull/1287) [PR #1305](https://github.com/volcengine/OpenViking/pull/1305)；同时补齐 autoRecall 搜索范围与查询清洗、截断能力，[PR #1225](https://github.com/volcengine/OpenViking/pull/1225) [PR #1297](https://github.com/volcengine/OpenViking/pull/1297)。
- **Memory、会话写入与运行时稳定性明显增强**：写接口引入 request-scoped wait 机制，[PR #1212](https://github.com/volcengine/OpenViking/pull/1212)；补强 PID lock 回收、召回阈值绕过、孤儿 compressor 引用、async contention 和 memory 语义批处理等问题，[PR #1211](https://github.com/volcengine/OpenViking/pull/1211) [PR #1301](https://github.com/volcengine/OpenViking/pull/1301) [PR #1304](https://github.com/volcengine/OpenViking/pull/1304)；并继续优化 memory v2 compressor 锁重试控制，[PR #1275](https://github.com/volcengine/OpenViking/pull/1275)。
- **安全与网络边界进一步收紧**：HTTP 资源导入补齐私网 SSRF 防护，[PR #1133](https://github.com/volcengine/OpenViking/pull/1133)；trusted mode 在无 API key 时被限制为仅允许 localhost，[PR #1279](https://github.com/volcengine/OpenViking/pull/1279)；embedding circuit breaker 与日志抑制也变得可配置，[PR #1277](https://github.com/volcengine/OpenViking/pull/1277)。
- **生态与集成能力继续扩展**：新增 Volcengine Vector DB STS Token 支持，[PR #1268](https://github.com/volcengine/OpenViking/pull/1268)；新增 MiniMax-M2.7 与 MiniMax-M2.7-highspeed provider 支持，[PR #1284](https://github.com/volcengine/OpenViking/pull/1284)；AST 侧补充 Lua parser，[PR #1286](https://github.com/volcengine/OpenViking/pull/1286)；Bot 新增 channel mention 能力，[PR #1272](https://github.com/volcengine/OpenViking/pull/1272)。
- **发布、Docker 与 CI 链路更稳健**：发布时自动更新 `main` 并增加 Docker Hub push，[PR #1229](https://github.com/volcengine/OpenViking/pull/1229)；修复 Docker maturin 路径并将 Gemini optional dependency 纳入镜像，[PR #1295](https://github.com/volcengine/OpenViking/pull/1295) [PR #1254](https://github.com/volcengine/OpenViking/pull/1254)；CI 侧优化 API matrix、补充 timeout/SMTP 通知并修复 reusable workflow / action 问题，[PR #1281](https://github.com/volcengine/OpenViking/pull/1281) [PR #1293](https://github.com/volcengine/OpenViking/pull/1293) [PR #1300](https://github.com/volcengine/OpenViking/pull/1300) [PR #1302](https://github.com/volcengine/OpenViking/pull/1302) [PR #1307](https://github.com/volcengine/OpenViking/pull/1307)。

## 升级说明

- OpenClaw 插件默认配置发生调整：`recallPreferAbstract` 与 `ingestReplyAssist` 现在默认均为 `false`。如果你之前依赖默认开启行为，升级后需要显式配置，见 [PR #1204](https://github.com/volcengine/OpenViking/pull/1204) 和 [PR #1206](https://github.com/volcengine/OpenViking/pull/1206)。
- HTTP 资源导入现在默认更严格地防护私网 SSRF；如果你有合法的内网资源采集场景，升级时建议复核现有接入方式与白名单策略，见 [PR #1133](https://github.com/volcengine/OpenViking/pull/1133)。
- trusted mode 在未提供 API key 时已限制为仅允许 localhost 访问；如果你此前通过非本地地址使用 trusted mode，需要同步调整部署与鉴权配置，见 [PR #1279](https://github.com/volcengine/OpenViking/pull/1279)。
- 写接口现在引入 request-scoped wait 机制，相关调用在并发写入下的等待与返回时机会更一致；如果你有依赖旧时序的外部编排逻辑，建议升级后复核行为，见 [PR #1212](https://github.com/volcengine/OpenViking/pull/1212)。
- server 已支持 `host=none` 以使用双栈网络；如果你在 IPv4/IPv6 混合环境中部署，可考虑调整监听配置，见 [PR #1273](https://github.com/volcengine/OpenViking/pull/1273)。
- Docker 镜像已纳入 Gemini optional dependency，并修复了 maturin 路径问题；如果你维护自定义镜像或发布流水线，建议同步检查构建脚本，见 [PR #1254](https://github.com/volcengine/OpenViking/pull/1254) 和 [PR #1295](https://github.com/volcengine/OpenViking/pull/1295)。

### OpenClaw 插件、评测与集成

- 默认将 openclaw-plugin 的 `recallPreferAbstract` 设为 `false`，[PR #1204](https://github.com/volcengine/OpenViking/pull/1204) by @wlff123
- 修复 eval 中的 async import 问题，[PR #1203](https://github.com/volcengine/OpenViking/pull/1203) by @yeshion23333
- 默认将 openclaw-plugin 的 `ingestReplyAssist` 设为 `false`，[PR #1206](https://github.com/volcengine/OpenViking/pull/1206) by @wlff123
- 向 OpenAIVLM client 增加 timeout 参数，[PR #1208](https://github.com/volcengine/OpenViking/pull/1208) by @highland0971
- 防止 redo recovery 中阻塞式 VLM 调用导致启动 hang 住，[PR #1226](https://github.com/volcengine/OpenViking/pull/1226) by @mvanhorn
- 为插件 autoRecall 增加 `skills` 搜索范围，[PR #1225](https://github.com/volcengine/OpenViking/pull/1225) by @mvanhorn
- 新增 bot channel mention 能力，[PR #1272](https://github.com/volcengine/OpenViking/pull/1272) by @yeshion23333
- 新增 OpenClaw eval shell 脚本，[PR #1287](https://github.com/volcengine/OpenViking/pull/1287) by @yeshion23333
- 新增 `add_message` 的 create time，[PR #1288](https://github.com/volcengine/OpenViking/pull/1288) by @wlff123
- 对 OpenClaw recall query 做清洗并限制长度，[PR #1297](https://github.com/volcengine/OpenViking/pull/1297) by @qin-ctx
- 修复 OpenClaw eval 导入到 OpenViking 时默认 user 的问题，[PR #1305](https://github.com/volcengine/OpenViking/pull/1305) by @yeshion23333

### Memory、会话、存储与运行时

- 修复明文文件长度小于 4 字节时 decrypt 抛出 `Ciphertext too short` 的问题，[PR #1163](https://github.com/volcengine/OpenViking/pull/1163) by @yc111233
- 为短明文加密补充单元测试，[PR #1217](https://github.com/volcengine/OpenViking/pull/1217) by @baojun-zhang
- 修复 PID lock 回收、召回阈值绕过与 orphaned compressor refs 等问题，[PR #1211](https://github.com/volcengine/OpenViking/pull/1211) by @JasonOA888
- queuefs 去重 memory semantic parent enqueue，[PR #792](https://github.com/volcengine/OpenViking/pull/792) by @Protocol-zero-0
- 为 decrypt 短明文场景补充回归测试，[PR #1223](https://github.com/volcengine/OpenViking/pull/1223) by @yc111233
- 为写接口实现 request-scoped wait 机制，[PR #1212](https://github.com/volcengine/OpenViking/pull/1212) by @zhoujh01
- 将 agfs 重组为 Rust 实现的 ragfs，[PR #1221](https://github.com/volcengine/OpenViking/pull/1221) by @MaojiaSheng
- 修复 session `_wait_for_previous_archive_done` 无限挂起问题，[PR #1235](https://github.com/volcengine/OpenViking/pull/1235) by @yc111233
- 修复 `#1238`、`#1242` 和 `#1232` 涉及的问题，[PR #1243](https://github.com/volcengine/OpenViking/pull/1243) by @MaojiaSheng
- Memory 侧继续做性能优化，[PR #1159](https://github.com/volcengine/OpenViking/pull/1159) by @chenjw

## v0.3.3 (2026-04-03)

# OpenViking v0.3.3

## Highlights

- **评测与写入能力继续扩展**：新增 RAG benchmark 评测框架 [PR #825](https://github.com/volcengine/OpenViking/pull/825)，补充 OpenClaw 的 LoCoMo eval 脚本与说明 [PR #1152](https://github.com/volcengine/OpenViking/pull/1152)，并新增内容写入接口 [PR #1151](https://github.com/volcengine/OpenViking/pull/1151)。
- **OpenClaw 插件可用性显著增强**：补充架构文档与图示 [PR #1145](https://github.com/volcengine/OpenViking/pull/1145)，安装器不再覆盖 `gateway.mode` [PR #1149](https://github.com/volcengine/OpenViking/pull/1149)，新增端到端 healthcheck 工具 [PR #1180](https://github.com/volcengine/OpenViking/pull/1180)，支持 bypass session patterns [PR #1194](https://github.com/volcengine/OpenViking/pull/1194)，并在 OpenViking 故障时避免阻塞 OpenClaw [PR #1158](https://github.com/volcengine/OpenViking/pull/1158)。
- **测试与 CI 覆盖大幅补强**：OpenClaw 插件新增大规模单测套件 [PR #1144](https://github.com/volcengine/OpenViking/pull/1144)，补充 e2e 测试 [PR #1154](https://github.com/volcengine/OpenViking/pull/1154)，新增 OpenClaw2OpenViking 集成测试与 CI 流水线 [PR #1168](https://github.com/volcengine/OpenViking/pull/1168)。
- **会话、解析与导入链路更稳健**：支持创建 session 时指定 `session_id` [PR #1074](https://github.com/volcengine/OpenViking/pull/1074)，CLI 聊天端点优先级与 `grep --exclude-uri/-x` 能力得到增强 [PR #1143](https://github.com/volcengine/OpenViking/pull/1143) [PR #1174](https://github.com/volcengine/OpenViking/pull/1174)，目录导入 UX / 正确性与扫描 warning 契约也进一步改善 [PR #1197](https://github.com/volcengine/OpenViking/pull/1197) [PR #1199](https://github.com/volcengine/OpenViking/pull/1199)。
- **稳定性与安全性继续加固**：修复任务 API ownership 泄露问题 [PR #1182](https://github.com/volcengine/OpenViking/pull/1182)，统一 stale lock 处理并补充 ownership checks [PR #1171](https://github.com/volcengine/OpenViking/pull/1171)，修复 ZIP 乱码 [PR #1173](https://github.com/volcengine/OpenViking/pull/1173)、embedder dimensions 透传 [PR #1183](https://github.com/volcengine/OpenViking/pull/1183)、语义 DAG 增量更新缺失 summary 场景 [PR #1177](https://github.com/volcengine/OpenViking/pull/1177) 等问题。

## Upgrade Notes

- OpenClaw 插件安装器不再写入 `gateway.mode`。如果你之前依赖安装流程自动改写该配置，升级后需要改为显式管理，见 [PR #1149](https://github.com/volcengine/OpenViking/pull/1149)。
- 如果你使用 `--with-bot` 进行安装或启动，失败时现在会直接返回错误码；依赖“失败但继续执行”行为的脚本需要同步调整，见 [PR #1175](https://github.com/volcengine/OpenViking/pull/1175)。
- 如果你接入 OpenAI Dense Embedder，自定义维度参数现在会正确传入 `embed()`；此前依赖默认维度行为的调用方建议复核配置，见 [PR #1183](https://github.com/volcengine/OpenViking/pull/1183)。
- `ov status` 现在会展示 embedding 与 rerank 模型使用情况，便于排障与环境核对，见 [PR #1191](https://github.com/volcengine/OpenViking/pull/1191)。
- 检索侧曾尝试加入基于 tags metadata 的 cross-subtree retrieval [PR #1162](https://github.com/volcengine/OpenViking/pull/1162)，但已在本版本窗口内回滚 [PR #1200](https://github.com/volcengine/OpenViking/pull/1200)，因此不应将其视为 `v0.3.3` 的最终可用能力。
- `litellm` 依赖范围更新为 `>=1.0.0,<1.83.1`，升级时建议同步检查锁文件与兼容性，见 [PR #1179](https://github.com/volcengine/OpenViking/pull/1179)。

## What's Changed

### Benchmark, Eval, CLI, and Writing

- 新增 RAG benchmark 系统评测框架，[PR #825](https://github.com/volcengine/OpenViking/pull/825) by @sponge225
- 优化 CLI chat endpoint 配置优先级，[PR #1143](https://github.com/volcengine/OpenViking/pull/1143) by @ruansheng8
- 新增 OpenClaw 的 LoCoMo eval 脚本与 README，[PR #1152](https://github.com/volcengine/OpenViking/pull/1152) by @yeshion23333
- 新增内容写入接口，[PR #1151](https://github.com/volcengine/OpenViking/pull/1151) by @zhoujh01
- `ov cli grep` 新增 `--exclude-uri` / `-x` 选项，[PR #1174](https://github.com/volcengine/OpenViking/pull/1174) by @heaoxiang-ai
- 更新 rerank 配置文档，[PR #1138](https://github.com/volcengine/OpenViking/pull/1138) by @ousugo

### OpenClaw Plugin, OpenCode Plugin, Bot, and Console

- 刷新 OpenClaw 插件架构文档并补充图示，[PR #1145](https://github.com/volcengine/OpenViking/pull/1145) by @qin-ctx
- 修复 OpenClaw 插件安装器不应写入 `gateway.mode`，[PR #1149](https://github.com/volcengine/OpenViking/pull/1149) by @LinQiang391
- 处理 OpenViking 故障时不再阻塞 OpenClaw，[PR #1158](https://github.com/volcengine/OpenViking/pull/1158) by @wlff123
- 新增 OpenClaw 插件端到端 healthcheck 工具，[PR #1180](https://github.com/volcengine/OpenViking/pull/1180) by @wlff123
- 新增 bypass session patterns 支持，[PR #1194](https://github.com/volcengine/OpenViking/pull/1194) by @Mijamind719
- 修复 OpenCode 插件在当前 main 上恢复 stale commit state 的问题，[PR #1187](https://github.com/volcengine/OpenViking/pull/1187) by @13ernkastel
- 新增 Bot 单通道（BotChannel）集成与 Werewolf demo，[PR #1196](https://github.com/volcengine/OpenViking/pull/1196) by @yeshion23333
- console 支持 account user agentid，[PR #1198](https://github.com/volcengine/OpenViking/pull/1198) by @zhoujh01

### Sessions, Retrieval, Parsing, and Resource Import

- 支持创建 session 时指定 `session_id`，[PR #1074](https://github.com/volcengine/OpenViking/pull/1074) by @likzn
- 修复 reindex 时 memory `context_type` 的 URI 匹配逻辑，[PR #1155](https://github.com/volcengine/OpenViking/pull/1155) by @deepakdevp

## v0.3.2 (2026-04-01)

## What's Changed
* Chore/pr agent ark token billing
* Fix HTTPX recognition issue with SOCKS5 proxy causing OpenViking crash
* refactor(model): unify config-driven retry across VLM and embedding
* fix(bot): import eval session time
* docs(examples): retire legacy integration examples
* fix(ci): skip filesystem tests on Windows due to FUSE compatibility
* docs(docker): use latest image tag in examples
* upload the new wechat group qrcode
* Unify test directory in openclaw-plugin
* docs(guides):Add concise OVPack guide in Chinese and English
* docs(guides): reorganize observability documentation
* fix(installer): fall back to official PyPI when mirror lags
* feat(docker) add vikingbot and console
* fix(vlm): rollback ResponseAPI to Chat Completions, keep tool calls
* feat(openclaw-plugin): add session-pattern guard for ingest reply assist

## v0.3.1 (2026-03-31)

## What's Changed
* feat(ast): add PHP tree-sitter support
* feat(ci): add multi-platform API test support for 5 platforms
* fix(ci): refresh uv.lock for docker release build
* fix(openclaw-plugin): simplify install flow and harden helpers
* fix(openclaw-plugin): preserve existing ov.conf on auto install
* ci: build docker images natively per arch
* Feature/memory opt
* feat(storage): add auto language detection for semantic summary generation
* feat(prompt) support configurable prompt template directories
* feat: unify config-driven retry across VLM and embedding
* feat(bot): import ov eval script
* Revert "feat: unify config-driven retry across VLM and embedding"
* fix(storage) Fix parent_uri compatibility with legacy records
* fix(session): unify archive context abstracts

## v0.2.14 (2026-03-30)

# OpenViking v0.2.14

## Highlights

- 多租户与身份管理进一步完善。CLI 已支持租户身份默认值与覆盖，文档新增多租户使用指南，memory 也支持仅按 agent 维度隔离的 `agent-only` scope。
- 解析与导入链路更完整。图片解析新增 OCR 文本提取，目录导入识别 `.cc` 文件，重复标题导致的文件名冲突得到修复，HTTP 上传链路改为更稳妥的 upload id 流程。
- OpenClaw 插件显著增强。安装器与升级流程统一，默认按最新 Git tag 安装，session API 与 context pipeline 做了统一重构，并补齐了 Windows、compaction、compact result mapping、子进程重拉起等多处兼容性与稳定性问题。
- Bot 与 Feishu 集成可用性继续提升。修复了 bot proxy 未鉴权问题，改进了 Moonshot 请求兼容性，并升级了 Feishu interactive card 的 markdown 展示体验。
- 存储与运行时稳定性持续提升。包括 queuefs embedding tracker 加固、vector store 移除 `parent_uri`、Docker doctor 检查对齐，以及更细粒度的 eval token 指标。

## Upgrade Notes

- Bot proxy 接口 `/bot/v1/chat` 与 `/bot/v1/chat/stream` 已补齐鉴权，依赖未鉴权访问的调用方需要同步调整，见 [#996](https://github.com/volcengine/OpenViking/pull/996)。
- 裸 HTTP 导入本地文件/目录时，推荐按 `temp_upload -> temp_file_id` 的方式接入上传链路，见 [#1012](https://github.com/volcengine/OpenViking/pull/1012)。
- OpenClaw 插件的 compaction delegation 修复要求 `openclaw >= v2026.3.22`，见 [#1000](https://github.com/volcengine/OpenViking/pull/1000)。
- OpenClaw 插件安装器现在默认跟随仓库最新 Git tag 安装，如需固定版本可显式指定，见 [#1050](https://github.com/volcengine/OpenViking/pull/1050)。

## What's Changed

### Multi-tenant, Memory, and Identity

- 支持 CLI 租户身份默认值与覆盖，[#1019](https://github.com/volcengine/OpenViking/pull/1019) by @zhoujh01
- 支持仅按 agent 隔离的 agent memory scope，[#954](https://github.com/volcengine/OpenViking/pull/954) by @liberion1994
- 新增多租户使用指南文档，[#1029](https://github.com/volcengine/OpenViking/pull/1029) by @qin-ctx
- 在 quickstart 和 basic usage 中补充 `user_key` 与 `root_key` 的区别说明，[#1077](https://github.com/volcengine/OpenViking/pull/1077) by @r266-tech
- 示例中支持将 recalled memories 标记为 used，[#1079](https://github.com/volcengine/OpenViking/pull/1079) by @0xble

### Parsing and Resource Import

- 新增图片解析 OCR 文本提取能力，[#942](https://github.com/volcengine/OpenViking/pull/942) by @mvanhorn
- 修复重复标题导致的合并文件名冲突，[#1005](https://github.com/volcengine/OpenViking/pull/1005) by @deepakdevp
- 目录导入时识别 `.cc` 文件，[#1008](https://github.com/volcengine/OpenViking/pull/1008) by @qin-ctx
- 增加对非空 S3 目录 marker 的兼容性，[#997](https://github.com/volcengine/OpenViking/pull/997) by @zhoujh01
- HTTP 上传链路从临时路径切换为 upload id，[#1012](https://github.com/volcengine/OpenViking/pull/1012) by @qin-ctx
- 修复从目录上传时临时归档文件丢失 `.zip` 后缀的问题，[#1021](https://github.com/volcengine/OpenViking/pull/1021) by @Shawn-cf-o

### OpenClaw Plugin and Installer

- 回滚 duplicate registration guard 的加固改动，[#995](https://github.com/volcengine/OpenViking/pull/995) by @qin-ptr
- Windows 下 mapped session ID 做安全清洗，[#998](https://github.com/volcengine/OpenViking/pull/998) by @qin-ctx
- 使用 plugin-sdk exports 实现 compaction delegation，修复 `#833`，要求 `openclaw >= v2026.3.22`，[#1000](https://github.com/volcengine/OpenViking/pull/1000) by @jcp0578
- 统一 installer 升级流程，[#1020](https://github.com/volcengine/OpenViking/pull/1020) by @LinQiang391
- 默认按最新 Git tag 安装 OpenClaw 插件，[#1050](https://github.com/volcengine/OpenViking/pull/1050) by @LinQiang391
- 统一 session APIs，并重构 OpenClaw context pipeline 以提升一致性、可维护性和测试覆盖，[#1040](https://github.com/volcengine/OpenViking/pull/1040) by @wlff123
- 将 openclaw-plugin 的 `DEFAULT_COMMIT_TOKEN_THRESHOLD` 调整为 `20000`，[#1052](https://github.com/volcengine/OpenViking/pull/1052) by @wlff123

## v0.2.13 (2026-03-26)

## What's Changed
* test: add comprehensive unit tests for core utilities
* fix(vlm): scope LiteLLM thinking param to DashScope providers only
* Api test：improve API test infrastructure with dual-mode CI
* docs: Add basic usage example and Chinese documentation for examples
* Fix Windows engine wheel runtime packaging
* fix(openclaw-plugin): harden duplicate registration guard

## v0.2.12 (2026-03-25)

## What's Changed
* Use uv sync --locked in Dockerfile
* fix(server): handle CancelledError during shutdown paths
* fix(bot):rollback config

## v0.2.11 (2026-03-25)

# OpenViking v0.2.11

## 版本亮点

- **模型与检索生态继续扩展**：新增 MiniMax embedding [#624](https://github.com/volcengine/OpenViking/pull/624)、Azure OpenAI embedding/VLM [#808](https://github.com/volcengine/OpenViking/pull/808)、GeminiDenseEmbedder [#751](https://github.com/volcengine/OpenViking/pull/751)、LiteLLM embedding [#853](https://github.com/volcengine/OpenViking/pull/853) 与 rerank [#888](https://github.com/volcengine/OpenViking/pull/888)，并补充 OpenAI-compatible rerank [#785](https://github.com/volcengine/OpenViking/pull/785) 与 Tavily 搜索后端 [#788](https://github.com/volcengine/OpenViking/pull/788)。
- **内容接入链路更完整**：音频资源现在支持 Whisper ASR 解析 [#805](https://github.com/volcengine/OpenViking/pull/805)，云文档场景新增飞书/Lark 解析器 [#831](https://github.com/volcengine/OpenViking/pull/831)，文件向量化策略变为可配置 [#858](https://github.com/volcengine/OpenViking/pull/858)，搜索结果还新增了 provenance 元数据 [#852](https://github.com/volcengine/OpenViking/pull/852)。
- **服务端运维能力明显补齐**：新增 `ov reindex` [#795](https://github.com/volcengine/OpenViking/pull/795)、`ov doctor` [#851](https://github.com/volcengine/OpenViking/pull/851)、Prometheus exporter [#806](https://github.com/volcengine/OpenViking/pull/806)、内存健康统计 API [#706](https://github.com/volcengine/OpenViking/pull/706)、可信租户头模式 [#868](https://github.com/volcengine/OpenViking/pull/868) 与 Helm Chart [#800](https://github.com/volcengine/OpenViking/pull/800)。
- **多租户与安全能力增强**：新增多租户文件加密 [#828](https://github.com/volcengine/OpenViking/pull/828) 和文档加密能力 [#893](https://github.com/volcengine/OpenViking/pull/893)，修复租户上下文在 observer 与 reindex 流程中的透传问题 [#807](https://github.com/volcengine/OpenViking/pull/807) [#820](https://github.com/volcengine/OpenViking/pull/820)，并修复 ZIP Slip 风险 [#879](https://github.com/volcengine/OpenViking/pull/879) 与 trusted auth 模式下 API key 校验缺失 [#924](https://github.com/volcengine/OpenViking/pull/924)。
- **稳定性与工程体验持续加固**：向量检索 NaN/Inf 分数处理在结果端 [#824](https://github.com/volcengine/OpenViking/pull/824) 和源头 [#882](https://github.com/volcengine/OpenViking/pull/882) 双重兜底，会话异步提交与并发提交问题被修复 [#819](https://github.com/volcengine/OpenViking/pull/819) [#783](https://github.com/volcengine/OpenViking/pull/783) [#900](https://github.com/volcengine/OpenViking/pull/900)，Windows stale lock 与 TUI 输入问题持续修复 [#790](https://github.com/volcengine/OpenViking/pull/790) [#798](https://github.com/volcengine/OpenViking/pull/798) [#854](https://github.com/volcengine/OpenViking/pull/854)，同时补上了代理兼容 [#957](https://github.com/volcengine/OpenViking/pull/957) 与 API 重试风暴保护 [#772](https://github.com/volcengine/OpenViking/pull/772)。

## 升级提示

- 如果你使用 `litellm` 集成，请关注这一版本中的安全策略调整：先临时硬禁用 [#937](https://github.com/volcengine/OpenViking/pull/937)，后恢复为仅允许 `<1.82.6` 的版本范围 [#966](https://github.com/volcengine/OpenViking/pull/966)。建议显式锁定依赖版本。
- 如果你启用 trusted auth 模式，需要同时配置服务端 API key，相关校验已在 [#924](https://github.com/volcengine/OpenViking/pull/924) 中强制执行。
- Helm 默认配置已切换为更适合 Volcengine 场景的默认值，并补齐缺失字段 [#822](https://github.com/volcengine/OpenViking/pull/822)。升级 chart 时建议重新审阅 values 配置。
- Windows 用户建议关注 stale PID / RocksDB LOCK 文件处理增强 [#790](https://github.com/volcengine/OpenViking/pull/790) [#798](https://github.com/volcengine/OpenViking/pull/798) [#854](https://github.com/volcengine/OpenViking/pull/854)。

### 模型、Embedding、Rerank 与检索生态

- 新增 MiniMax embedding provider，支持通过官方 HTTP API 接入 MiniMax 向量模型。[PR #624](https://github.com/volcengine/OpenViking/pull/624)
- 新增 OpenAI-compatible rerank provider。[PR #785](https://github.com/volcengine/OpenViking/pull/785)
- 新增 Azure OpenAI 对 embedding 与 VLM 的支持。[PR #808](https://github.com/volcengine/OpenViking/pull/808)
- 新增 GeminiDenseEmbedder 文本向量模型提供方。[PR #751](https://github.com/volcengine/OpenViking/pull/751)
- 新增 Tavily 作为可配置的 web search backend。[PR #788](https://github.com/volcengine/OpenViking/pull/788)
- 修复 LiteLLM 下 `zai/` 模型前缀处理，避免重复拼接 `zhipu` 前缀。[PR #789](https://github.com/volcengine/OpenViking/pull/789)
- 修复 Gemini embedder 相关问题。[PR #841](https://github.com/volcengine/OpenViking/pull/841)
- 新增 LiteLLM embedding provider。[PR #853](https://github.com/volcengine/OpenViking/pull/853)
- 新增默认向量索引名可配置能力。[PR #861](https://github.com/volcengine/OpenViking/pull/861)
- 新增 LiteLLM rerank provider。[PR #888](https://github.com/volcengine/OpenViking/pull/888)
- 修复 Ollama embedding provider 的维度解析问题。[PR #915](https://github.com/volcengine/OpenViking/pull/915)
- 为 Jina 代码模型补充 code-specific task 默认值。[PR #914](https://github.com/volcengine/OpenViking/pull/914)
- 在 embedder 与 vectordb 维度不匹配时给出警告提示。[PR #930](https://github.com/volcengine/OpenViking/pull/930)
- 为 Jina embedding provider 增加代码模型维度与更可操作的 422 错误提示。[PR #928](https://github.com/volcengine/OpenViking/pull/928)
- 搜索结果新增 provenance 元数据，方便追踪召回来源。[PR #852](https://github.com/volcengine/OpenViking/pull/852)
- 修复 recall limit 逻辑。[PR #821](https://github.com/volcengine/OpenViking/pull/821)
- 在结果序列化前钳制向量检索中的 `inf`/`nan` 分数，避免 JSON 失败。[PR #824](https://github.com/volcengine/OpenViking/pull/824)
- 在相似度分数源头钳制 `inf`/`nan`，进一步避免 JSON crash。[PR #882](https://github.com/volcengine/OpenViking/pull/882)

### 解析、导入与内容处理

- 新增基于 Whisper 的音频解析与 ASR 集成。[PR #805](https://github.com/volcengine/OpenViking/pull/805)
- 修复 PDF 书签目标页为整数索引时的解析问题。[PR #794](https://github.com/volcengine/OpenViking/pull/794)
- 新增飞书/Lark 云文档解析器。[PR #831](https://github.com/volcengine/OpenViking/pull/831)
- 修复 MarkdownParser 的字符数限制处理。[PR #826](https://github.com/volcengine/OpenViking/pull/826)
- 支持可配置的文件向量化策略。[PR #858](https://github.com/volcengine/OpenViking/pull/858)

## v0.2.10 (2026-03-24)

# LiteLLM 安全热修复 Release Note

更新时间：2026-03-24

## 背景

由于上游依赖 `LiteLLM` 出现公开供应链安全事件，OpenViking 在本次热修复中临时禁用所有 LiteLLM 相关入口，以避免继续安装或运行到受影响依赖。

## 变更内容

- 移除根依赖中的 `litellm`
- 移除根 `uv.lock` 中的 `litellm`
- 禁用 LiteLLM 相关的 VLM provider 入口
- 禁用 bot 侧 LiteLLM provider 和图片工具入口
- 增加 LiteLLM 已禁用的回归测试

## 建议操作

建议用户立即执行以下动作：

1. 检查运行环境中是否安装 `litellm`
2. 卸载可疑版本并重建虚拟环境、容器镜像或发布产物
3. 对近期安装过可疑版本的机器轮换 API Key 和相关凭证
4. 升级到本热修复版本

可用命令：

## 兼容性说明

这是一个以止损为目标的防御性热修复版本。LiteLLM 相关能力会暂时不可用，直到上游给出可信的修复版本和完整事故说明。

## 参考链接

- 上游 issue: <https://github.com/BerriAI/litellm/issues/24512>
- PyPI 项目页: <https://pypi.org/project/litellm/>
- GitHub Security: <https://github.com/BerriAI/litellm/security>
- OpenViking 热修分支: <https://github.com/volcengine/OpenViking/tree/hotfix/v0.2.10-disable-litellm>

## v0.2.9 (2026-03-19)

## What's Changed
* fix(resource): enforce agent-level watch task isolation
* feat(embedder): use summary for file embedding in semantic pipeline
* Fix/bot readme
* Fix/increment update dir vector store
* fix(plugin): restore bug fixes from #681 and #688 lost during #662 merge
* docs: add docker compose instructions and mac port forwarding tip to …
* Update docs
* feat(ci): add comprehensive Qodo PR-Agent review rules
* [feat](bot):Add mode config, add debug mode, add /remember cmd
* fix(vectordb): share single adapter across account backends to prevent RocksDB lock contention

## v0.2.8 (2026-03-19)

# OpenViking v0.2.8 发布公告

## 本次更新亮点

### 1. 插件生态继续升级，OpenClaw / OpenCode 集成更完整
- `openclaw-plugin` 升级到 **2.0**，从 memory plugin 进一步演进为 **context engine**。
- 新增并完善 **OpenCode memory plugin example**，补充 attribution 与后续插件更新。
- 支持 **多智能体 memory isolation**，可基于 hook context 中的 `agentId` 做记忆隔离。
- 修复插件自动 recall 可能导致 agent 长时间挂起的问题，并补强 legacy commit 兼容。
- MCP 查询服务补充 `api_key` 支持与默认配置能力，降低接入成本。

### 2. Session / Memory 链路增强，长期记忆能力更稳
- 新增 **memory cold-storage archival**，通过 hotness scoring 管理长期记忆冷热分层。
- 新增长记忆 **chunked vectorization**，改善超长内容的向量化处理能力。
- 增加 `used()` 接口，用于上下文 / skill 使用追踪。
- 修复 async commit 过程中的上下文透传、批次内重复写入、非标准 LLM 响应处理等问题。
- Bot 场景下改用 `commit_async()`，减少阻塞风险，提升长会话稳定性。

### 3. 检索与 Embedding 能力持续增强
- 分层检索中正式集成 **rerank**，并修复无 rerank 场景下的检索可用性问题。
- 新增 **RetrievalObserver**，可用于检索质量指标观测，并已接入 `ov observer`。
- Embedding 侧新增：
- 修复 CJK token 估算偏低等问题，提升多语言场景下的稳定性。

### 4. 资源、存储与解析链路更完善
- 新增 **resource watch scheduling** 与状态跟踪能力，资源同步流程更可控。
- 新增 **reindex endpoint**，支持内容手动修改后的重新 embedding。
- 解析能力新增对 **legacy `.doc` / `.xls`** 格式的支持。
- 修复从 URL 导入文件时文件名与扩展名丢失的问题。
- 存储层新增 **path locking** 与选择性 crash recovery，进一步提升写入安全性。
- 修复 tenant API 的隐式 root fallback、文件系统接口路径处理、工作目录 `~` 展开等问题。

### 5. VLM、Trace 与可观测性能力加强
- 新增 **request-level trace metrics** 与对应 API 支持。
- 新增 **memory extract telemetry breakdown**，帮助更细粒度地分析记忆提取过程。
- OpenAI VLM 支持 **streaming response handling**。
- 补充 `max_tokens` 参数以避免 vLLM 拒绝请求，并支持 OpenAI-compatible VLM 的自定义 HTTP headers。
- 自动清理模型输出中的 `<think>` 标签，减少推理内容污染存储结果。

### 6. 工程兼容性与交付体验继续改进
- 修复 Windows zip 路径、代码仓库索引、Rust CLI 版本等跨平台问题。
- `agfs` Makefile 完成跨平台兼容性重构。
- Vectordb engine 支持按 **CPU variant** 拆分。
- Docker 构建链路持续修复，补齐 `build_support/` 拷贝逻辑。
- Release workflow 支持发布 **Python 3.14 wheels**。

## v0.2.6 (2026-03-11)

# OpenViking v0.2.6 发布公告

## 重点更新

### 1. CLI 与对话体验进一步升级

- `ov chat` 现在基于 `rustyline` 提供更完整的行编辑体验，终端交互更自然，不再出现常见的方向键控制字符问题。
- 新增 Markdown 渲染能力，终端中的回答展示更清晰，代码块、列表等内容可读性更好。
- 支持聊天历史记录，同时提供关闭格式化和关闭历史记录的选项，便于在不同终端环境中按需使用。

### 2. 服务端异步能力增强，长任务不再轻易阻塞

- Session commit 新增异步提交能力，并支持通过 `wait` 参数控制是否同步等待结果。
- 当选择后台执行时，OpenViking 现在会返回可追踪的任务信息，调用方可以通过任务接口查询状态、结果或错误。
- 服务端新增可配置 worker count，进一步缓解单 worker 场景下长任务阻塞请求的问题。

### 3. 新增 Console，并持续增强 Bot 与资源操作能力

- 本版本新增独立的 OpenViking Console，提供更直观的 Web 控制台入口，方便调试、调用和查看接口结果。
- Bot 能力继续增强，新增 eval 能力，开放 `add-resource` 工具，并支持飞书进度通知等扩展能力。
- 资源导入支持 `preserve_structure` 选项，扫描目录时可以保留原始目录层级，适合更复杂的知识组织方式。
- 同时修复了 grep、glob、`add-resource` 等场景下的一些响应问题，提升日常使用稳定性。

### 4. openclaw memory plugin 能力和稳定性大幅完善

- openclaw memory plugin 在这一版中获得了较大幅度增强，补充了更完整的插件能力与相关示例。
- 插件安装链路进一步完善，现已支持通过 npm 包方式安装 setup-helper，部署和升级都更直接。
- 修复了本地运行、端口管理、配置保留、缺失文件下载、配置覆盖等一系列影响安装和使用体验的问题。
- Memory consolidation 与 skill tool memory 相关问题也得到修复，进一步提升记忆链路的稳定性。
- Vikingbot 作为 OpenViking 可选依赖的集成方式也做了完善，降低了插件和 bot 协同使用时的接入门槛。

### 5. 安装、跨平台与工程质量继续补强

- 新增 Linux ARM 支持，进一步扩展了 OpenViking 的部署平台范围。
- 修复了 Windows 下 UTF-8 BOM 配置文件的兼容问题，减少配置读取失败的情况。
- 修复了 `install.sh`、`ov.conf.example`、setup 失败退出等问题，并补充了 openclaw memory plugin 的 npm 包安装路径，提升首次安装和配置过程的成功率。
- CI 侧将 GitHub Actions runner 固定到明确 OS 版本，减少环境漂移带来的构建与发布不确定性。

## 总结

## What's Changed
* feat: improve ov chat UX with rustyline and markdown rendering
* fix: grep for binding-client
* feat(server): add configurable worker count to prevent single-worker blocking
* feat(sessions): add async commit support with wait parameter

## v0.2.5 (2026-03-06)

## What's Changed
* docs: use openviking-server to launch server
* fix: Session.add_message() support parts parameter
* feat: support GitHub tree/&lt;ref&gt; URL for code repository import
* fix: improve ISO datetime parsing
* feat(pdf): extract bookmarks as markdown headings for hierarchical parsing
* feat: add index control to add_resource and refactor embedding logic
* fix: support short-format URIs in VikingURI and VikingFS access control
* fix: handle None data in skill_processor._parse_skill()
* fix: fix add-resource
* fix: treat only .zip as archive; avoid unzipping ZIP-based container …
* fix(cli): support git@ SSH URL format in add-resource
* feat(pdf): add font-based heading detection and refactor PDF/Markdown parsing
* 支持通过curl方式安装部署openclaw+openviking插件
* Feature/vikingbot_opt： OpenAPI interface standardization；Feishu multi-user experience;  observability enhancements; configuration system modernization.
* docs: pip upgrade suggestion
* feat: define a system path for future deployment
* chore: downgrade golang version limit, update vlm version to seed 2.0
* [fix]: 修复通过curl命令安装场下下ubuntu/debian等系统触发系统保护无法安装的openviking的问题
* fix(session): trigger semantic indexing for parent directory after memory extraction
* feat(bot):Refactoring, New Evaluation Module, Feishu channel Opt & Feature Enhancements
* chore: agfs-client默认切到binding-client模式
* feat(agfs): add ripgrep-based grep acceleration and fix vimgrep line parser
* [WIP] ci: add automated PR review workflow using Doubao model
* fix(ci): use OPENAI_KEY instead of OPENAI.KEY in pr-review workflow
* build(deps): bump actions/download-artifact from 7 to 8 by @dependabot[bot] in
* build(deps): bump actions/upload-artifact from 6 to 7 by @dependabot[bot] in
* build(deps): bump docker/login-action from 3 to 4 by @dependabot[bot] in
* build(deps): bump docker/setup-qemu-action from 3 to 4 by @dependabot[bot] in
* build(deps): bump docker/setup-buildx-action from 3 to 4 by @dependabot[bot] in
* fix(packaging): sdist 排除运行时二进制
* ci: enhance PR review with severity classification and checklist
* enable injection for Collection and CollectionAdaptor
* chore: 编译子命令失败报错, golang版本最低要求1.22+
* feat(viking_fs) support async grep
* OpenViking Plugin Exception Handling & Fixing
* feat(agfs): make binding client optional, add server bootstrap, tune logging and CI
* fix: rust compile in uv pip install -e .
* [wip]Enhance OpenViking Status Checks in the OpenClaw Plugin
* FIX: fixes multiple issues in the OpenViking chat functionality and unifies session ID generation logic between Python and Rust CLI implementations.
* chore: 增加Makefile,方便build
* fix(bot): fix Telegram channel init crash and empty content for Claude
* fix: suport uv pip install openviking[bot]
* fix: agfs-client默认使用http-client
* fix(agfs): fix agfs binding-client import error

## v0.2.3 (2026-03-03)

## Breaking Change
After upgrading, datasets/indexes generated by historical versions are not compatible with the new version and cannot be reused directly. Please rebuild the datasets after upgrading (a full rebuild is recommended) to avoid retrieval anomalies, inconsistent filtering results, or runtime errors.
Stop the service -> rm -rf ./your-openviking-workspace -> restart the service with the openviking-server command.

## What's Changed
* Feat: CLI optimization
* Update README.md
* Update README_CN.md
* feat: support glob -n and cmd echo
* fix: fix release

## v0.2.2 (2026-03-03)

##  Breaking Change

Warning: This Release includes Breaking Chage! Before upgrading, you should stop VikingDB Server and clear workspace dir first.

## What's Changed
* fix ci
* 在readme补充千问使用方法
* feat(parse): Add C# AST extractor support
* chore: bump CLI version to 0.2.1
* fix: normalize OpenViking memory target paths
* fix: fix filter when multi tenants
* docs/update_wechat
* Fix retriever
* fix(git): support git@ SSH URLs with regression-safe repo detection
* fix(agfs): 预编译agfs所依赖的lib/bin,无需安装时构建

## v0.2.1 (2026-02-28)

## 1. Core Capability Upgrades: Multi-tenancy, Cloud-Native & OpenClaw/OpenCode Adaptation
- **Multi-tenancy**: Implemented foundational multi-tenancy support at the API layer (#260, #283), laying the groundwork for isolated usage across multiple users/teams.
- **Cloud-Native Support**: Added support for cloud-native VikingDB (#279), improved cloud deployment documentation and Docker CI workflows (#320), expanding from local deployment to cloud-edge integration.
- **OpenClaw Integration**: Added official installation for the `openclaw-openviking-plugin` (#307), strengthening integration with OpenClaw.
- **OpenCode Support**: Introduced the `opencode` plugin and updated documentation (#351), extending capabilities for code-related scenarios.

## 2. Deep Optimization of the Database Storage Foundation
- **Architecture Refactor**: Refactored the vector database interface (#327) and removed the `with_vector` parameter from query APIs (#338) to simplify the interface design.
- **Performance Optimizations**:
- **BugFixes**: Resolved missing Volcengine URI prefixes for VikingDB, invalidated `is_leaf` filters (#294), and fixed vector storage lock contention during fast restarts (#343).
- **AGFS Enhancements**: Added AGFS binding client (#304), fixed AGFS SDK installation/import issues (#337, #355), and improved filesystem integration.
- **Code Scenario Improvements**: Added AST-based code skeleton extraction mode (#334), supported private GitLab domains for code repositories (#285), and optimized GitHub ZIP download (#267).

## 3. Improved CLI Toolchain (Greatly Enhanced Usability)
- Added `ov` command wrapper (#325) and fixed bugs in the CLI wrapper, repo URI handling, and `find` command (#336, #339, #347).
- Enhanced `add-resource` functionality with unit tests (#323) and added ZIP upload support for skills via the `add_skill` API (#312).
- Configuration Extensions: Added timeout support in `ovcli.conf` (#308) and fixed agent_id issues in the Rust CLI (#308).
- Version Support: Added the `--version` flag to `openviking-server` (#358) for easy version validation.

## What's Changed
* docs : update wechat
* feat: 多租户 Phase 1 - API 层多租户能力
* 增加openviking/eval模块，用于评估测试
* feat(vectordb): integrate KRL for ARM Kunpeng vector search optimization
* feat: concurrent embedding, GitHub ZIP download, read offset/limit
* fix: claude code memory-plugin example:add_message改为写入TextPart列表，避免session解析异常
* Feat/add parts support to http api
* tests(parsers): add unit tests for office extensions within add_resou…
* feat: break change, remove is_leaf scalar and use level instead
* fix(api): complete parts support in SDK layers and simplify error handling
* feat: support cloud vikingDB
* Fix image_summary bug
* feat(config): expose embedding.max_concurrent and vlm.max_concurrent …
* Multi tenant
* feat: allow private gitlab domain for code repo
* fix(storage): idempotent rm/mv operations with vector index sync
* refactor(filter): replace prefix operator with must
* fix: VikingDB volcengine URI prefix loss and stale is_leaf filter
* fix(vectordb): default x86 build to AVX2 and disable AVX512
* 对齐 docs 和代码里 storage config 的 workspace 属性
* fix: correct storage.update() call signature in _update_active_counts()
* feat(memory): add hotness scoring for cold/hot memory lifecycle
* feat(client): ovcli.conf 支持 timeout 配置 + 修复 Rust CLI agent_id
* fix(server): 未配置 root_api_key 时仅允许 localhost 绑定
* fix: resolve Gemini 404, directory collision, and Unicode decoding er…

## cli@0.2.0 (2026-02-27)

# OpenViking CLI v0.2.0

### Quick Install (macOS/Linux)

### Manual Installation
Download the appropriate binary for your platform below, extract it, and add it to your PATH.

The CLI command is simply `ov`:

### Checksums
SHA256 checksums are provided for each binary for verification.

## v0.1.18 (2026-02-23)

## What's Changed
* feat: add Rust CLI implementation [very fast]
* feat: make -o and --json global param
* feat: provide a test_ov.sh scripts as reference
* fix: short markdown parse filename
* Update 03-quickstart-server.md-en version
* feat: add markitdown-inspired file parsers (Word, PowerPoint, Excel, EPub, ZIP)
* feat: rename for consistency
* 上传最新的微信群二维码
* Update 03-quickstart-server.md
* Update 01-about-us.md-微信群二维码地址更新
* fix: build target name
* fix: fix handle github url right
* Update README_CN.md-新增云端部署跳转链接与说明
* Update README.md-新增英文版readme云端部署内容
* Update 01-about-us.md-英文版微信群二维码更新
* fix: fix the difference of python CLI and rust CLI
* Feat/support multi providers ， OpenViking支持多providers
* Fix: 修复 rust CLI 命令中与 python CLI ls/tree 命令不一致的部分；add node limit arg
* feat: add_memory cli with ov-memory SKILL
* feat: add directory parsing support to OpenViking
* fix: auto-rename on duplicate filename conflict
* fix: guard against None candidate in search_by_id
* fix bugs 修复provider为openai，但api_key并不是https://api.openai.com/v1而引起的大模型调用失败情况
* Update open_viking_config.py
* fix(parser): hash & shorten filenames that exceed filesystem limit
* test: add comprehensive edge case tests
* fix: invalid Go version format in agfs-server go.mod
* fix: add input validation to search_by_id method
* fix: improve binary content detection and null byte handling
* suggestion: align grep and glob options
* suggestion: fix empty in --simple ls response
* feat: support basic ov tui for fs navigator, boost version 0.2.0
* build(deps): bump actions/download-artifact from 4 to 7 by @dependabot[bot] in
* build(deps): bump actions/checkout from 4 to 6 by @dependabot[bot] in
* build(deps): bump actions/upload-artifact from 4 to 6 by @dependabot[bot] in
* refactor(memory): redesign extraction/dedup flow and add conflict-a…
* fix: target directories retrieve
* fix: skill search ranking - use overview for embedding and fix visited set filtering
* fix: use frontmatter description for skill vectorization instead of overview
* build(deps): bump actions/cache from 4 to 5 by @dependabot[bot] in
* feat: update media parsers
* fix: update memory
* fix: resolve -o option conflict between global output and filesystem output-format
* feat: add memory, resource and search skills

## cli@0.1.0 (2026-02-14)

# OpenViking CLI v0.1.0

### Quick Install (macOS/Linux)

### Manual Installation
Download the appropriate binary for your platform below, extract it, and add it to your PATH.

The CLI command is simply `ov`:

### Checksums
SHA256 checksums are provided for each binary for verification.

## v0.1.17 (2026-02-14)

## What's Changed
* Revert "feat: support dynamic project_name config  in VectorDB / volcengine"
* Fix/ci clean workspace
* fix: tree uri output error, and validate ov.conf before start

## v0.1.16 (2026-02-13)

## What's Changed
* fix: fix vectordb
* feat: make temp uri readable, and enlarge timeout of add-resource
* feat: support dynamic project_name config  in VectorDB / volcengine
* fix: server uvloop conflicts with nest_asyncio

## v0.1.15 (2026-02-13)

## What's Changed

Now you can try Server/CLI mode!

* refactor(client): 拆分 HTTP 客户端，分离嵌入模式与 HTTP 模式
* Transaction store
* fix CI: correct patch targets in test_quick_start_lite.py
* fix/lifecycle
* Fix/lifecycle
* refactor: decouple QueueManager from VikingDBManager
* refactor: to accelerate cli launch speed, refactor openviking_cli dir
* efactor: to accelerate cli launch speed, refactor openviking_cli dir
* fix(vectordb): resolve timestamp format and collection creation issues
* doc: add multi-tenant-design
* fix: fix vectordb sparse
* [WIP]adapt for openclaw: add memory output language pipeline
* CLI commands (ls, tree) output optimize
* fix: replace bare except with Exception in llm utils
* feat(parser): support repo branch and commit refs
* fix: temp dir check failed

## v0.1.14 (2026-02-12)

## What's Changed
* build(deps): bump protobuf from 6.33.2 to 6.33.5 by @dependabot[bot] in
* refactor: cpp bytes rows
* fix: agfs port
* refactor: refactor agfs s3 backend config
* fix(depends): 修复pip依赖的安全漏洞
* feat: add HTTP Server and Python HTTP Client (T2 & T4)
* Add OpenClaw skill for OpenViking MCP integration
* docs: update docs and github workflows python version, python>=3.10
* Doc: suggest use ~/.openviking/ov.conf as a default configure path
* Parallel add
* feat(directory-scan): add directory pre-scan validation module with f…
* fix(docs): fix relative paths in README files to match actual docs st…
* feat: use a default dir ~/.openviking to store configuration, fix win…
* feat: dag trigger embedding
* fix: fix windows
* fix: release py3.13
* fix: remove await asyncio and call agfs directly
* fix(depends): 修复测试引入的依赖
* add_message改为写入TextPart列表，避免session消息解析异常; extract_session增加json转换方法
* feat: 新增 Bash CLI 基础框架与完整命令实现 (T3 + T5)
* fix: test case
* fix: fix release

## v0.1.12 (2026-02-09)

## What's Changed
* feat: add search_with_sparse_logit_alpha
* refactor: Refactor S3 configuration structure and fix Python 3.9 compatibility issues
* fix: fix ci
* refactor: unify async execution utilities into run_async
* docs: update community link
* build(deps): bump actions/download-artifact from 4 to 7 by @dependabot[bot] in
* build(deps): bump actions/github-script from 7 to 8 by @dependabot[bot] in
* build(deps): bump actions/checkout from 4 to 6 by @dependabot[bot] in
* build(deps): bump actions/setup-go from 5 to 6 by @dependabot[bot] in
* build(deps): bump actions/upload-artifact from 4 to 6 by @dependabot[bot] in
* feat: in chatmem example, add /time and /add_resource command
* Feature/new demo
* Feat:支持原生部署的vikingdb
* WIP: fix: run memex locally for #78
* feat(parse): extract shared upload utilities
* docs: fiix related document links in /openviking/parse/parsers/README.md
* fix(parse): prevent Zip Slip path traversal in _extract_zip (CWE-22)
* Path filter
* feat: use tabulate for observer
* fix(parser): fix temporary file leak in HTMLParser download link hand…
* perf: reuse query embeddings in hierarchical retriever
* fix(agfs): close socket on error path in _check_port_available
* fix(storage): make VikingFS.mkdir() actually create the target directory
* fix: fix sparse
* feat: support query in mcp, tested with kimi
* fix: ignore TestVikingDBProject

## v0.1.11 (2026-02-05)

## What's Changed
* support small github code repos

## v0.1.10 (2026-02-05)

## What's Changed
* Fix compile
* fix: fix windows release

## v0.1.9 (2026-02-05)

## What's Changed
* Bump github/codeql-action from 3 to 4 by @dependabot[bot] in
* Bump actions/setup-go from 5 to 6 by @dependabot[bot] in
* Bump actions/setup-python from 5 to 6 by @dependabot[bot] in
* Bump astral-sh/setup-uv from 4 to 7 by @dependabot[bot] in
* Bump actions/download-artifact from 4 to 7 by @dependabot[bot] in
* fix session test
* feat: add GitHub issue and PR templates
* fix: fix_build
* docs: update readme
* fix: Downgraded a log message from warning to info when the Rerank cl…
* docs: update_readme
* Remove agfs
* docs: faq
* fix: fix agfs-server for windows
* Upgrade go
* feat: add more visual example `query`
* fix: support intel mac install
* feat: rename backend to provider for embedding and vlm
* Lint code
* refactor: optimized pyproject.toml with optional groups
* feat: linux compile
* Change 'provider' to 'backend' in configuration
* update version
* Revert "Change 'provider' to 'backend' in configuration"
* chore: use standard logging pkg
* feat: chat & chat w/ mem examples
* feat:常规环境下，增加python3.13适配
* docs: add server cli design
* refactor: refactor ci action
* refactor: extract Service layer from async_client
* fix: fix ci action
* fix: simplify memory dedup decisions and fix retrieval recursion bug
* Fix ci action
* fix: 修复s3fs适配
* refactor: extract ObserverService from DebugService for cleaner status access API
* fix: fix release action
