# OpenClaw ContextEngine Plugin（OpenViking）设计实现方案

> 日期：2026-03-12
> 目标仓库：`/data/OpenViking`（插件实现） + `/data/openclaw`（接口对齐）

## 1. 背景与目标

本方案目标是在 OpenViking 中新增 `openclaw-contextengine-plugin`，通过 OpenClaw 的 `context-engine` 插件槽位接入，完整实现 RFC 讨论中的能力：

- Session Start 用户画像注入
- Per-turn 记忆检索注入（当轮生效）
- Compact 批量写入 OpenViking 记忆
- 主动记忆工具（commit/search）
- Skill Memory 增强
- Tool Memory 增强
- ov CLI 能力注入（CLI 优先，工具回退）

实现目录：`/data/OpenViking/examples/openclaw-contextengine-plugin`。

---

## 2. 设计依据与代码对齐

### 2.1 OpenClaw 侧关键能力

- ContextEngine 接口定义：`/data/openclaw/src/context-engine/types.ts`
  - 关键生命周期：`bootstrap`、`assemble`、`ingest`、`afterTurn`、`compact`
- ContextEngine 注册与解析：`/data/openclaw/src/context-engine/registry.ts`
  - `plugins.slots.contextEngine` 决定使用哪个引擎
- 插件 API 注册能力：`/data/openclaw/src/plugins/types.ts`
  - `registerContextEngine`、`registerTool`、`on(...)`
- 插件 manifest 规范：`/data/openclaw/docs/plugins/manifest.md`
  - 必须声明 `openclaw.plugin.json`，`kind: "context-engine"`

### 2.2 OpenViking 侧可复用能力

- 参考旧插件：`/data/OpenViking/examples/openclaw-memory-plugin`
  - `config.ts`（配置解析模式）
  - `client.ts`（OpenViking API 包装）
  - `process-manager.ts`（local 模式进程与健康检查）
  - `memory-ranking.ts`（检索结果后处理）
- OpenViking HTTP 能力（参考）：`/data/OpenViking/openviking_cli/client/http.py`
  - `find/search`
  - `create_session/add_message/commit_session/delete_session`
  - `health`

---

## 3. 总体架构（单插件双角色）

采用**单插件双角色**方案（推荐且已确认）：

1. 作为 `kind: "context-engine"` 注册，承接核心上下文生命周期
2. 同时注册 hooks + tools，承接 skill/tool memory 与主动检索/写入能力

### 3.1 分层

1) Engine Adapter 层
2) Retrieval Orchestrator 层
3) Ingestion/Compaction Writer 层
4) Memory Capability 层（Tools + CLI Guidance）
5) Skill/Tool Memory Enricher 层
6) Infra 层（Client/Config/Telemetry/Fallback）

### 3.2 关键原则

- 写入异步化、读取前置化
- 注入最小化（阈值 + 长度预算）
- 失败可降级，不阻断主对话
- 全能力开关化
- 首版不改 OpenClaw 核心

---

## 4. 目录与模块设计

目标目录：`/data/OpenViking/examples/openclaw-contextengine-plugin`

- `openclaw.plugin.json`
- `package.json`
- `tsconfig.json`
- `index.ts`
- `types.ts`
- `config.ts`
- `client.ts`
- `context-engine.ts`
- `retrieval.ts`
- `injection.ts`
- `ingestion.ts`
- `tools.ts`
- `skill-tool-memory.ts`
- `telemetry.ts`
- `fallback.ts`
- `README.md`
- `INSTALL.md`

### 4.1 各模块职责

- `index.ts`：插件入口，注册 context-engine / tools / hooks
- `config.ts`：配置解析、默认值、边界校验
- `client.ts`：OpenViking API 调用封装与错误规范化
- `context-engine.ts`：实现 OpenClaw ContextEngine 生命周期
- `retrieval.ts`：构 query、检索、过滤、排序、去重
- `injection.ts`：构造 `systemPromptAddition` 与模拟工具结果注入
- `ingestion.ts`：批量写入 session + commit + 清理
- `tools.ts`：`commit_memory` 与 `search_memories`
- `skill-tool-memory.ts`：技能/工具经验记忆增强
- `telemetry.ts`：日志与指标
- `fallback.ts`：超时/失败降级策略

---

## 5. 核心流程设计（时序）

### 5.1 Session Start

1. `bootstrap` 初始化会话状态
2. 读取 profile memory（质量门控）
3. 生成 `systemPromptAddition`（profile + tool memory + 可选 CLI guidance）

### 5.2 Per-turn Retrieval（读路径）

1. `assemble` 获取最近 N 条用户消息
2. 调 OpenViking `search/find`
3. 阈值过滤 + 去重 + 分层格式化
4. 以 `simulated_tool_result`（默认）注入当前轮
5. 不写回长期 transcript

### 5.3 Compact Batch Write（主写路径）

1. `compact` 收集本周期消息
2. 清洗与裁剪（含可选系统提示/工具调用）
3. 写入 OpenViking session
4. `commit_session` 触发抽取
5. 记录指标，必要时降级

### 5.4 Active Commit（主动写）

1. agent 调 `commit_memory`
2. 直接写入并 commit
3. 返回可观测结果

---

## 6. 配置模型（建议）

### 6.1 连接

- `mode`: `local | remote`（默认 `local`）
- `baseUrl`
- `port`（默认 `1933`）
- `apiKey`
- `agentId`（默认 `default`）
- `timeoutMs`（默认 `15000`）

### 6.2 retrieval

- `retrieval.enabled`（默认 `true`）
- `retrieval.lastNUserMessages`（默认 `5`）
- `retrieval.limit`（默认 `8`）
- `retrieval.scoreThreshold`（默认 `0.15`）
- `retrieval.targetUris`（默认 user+agent）
- `retrieval.formatLevel`（`L0/L1/L2`，默认 `L1`）
- `retrieval.injectMode`（默认 `simulated_tool_result`）
- `retrieval.skipGreeting`（默认 `true`）
- `retrieval.minQueryChars`（默认 `4`）

### 6.3 ingestion

- `ingestion.writeMode`（默认 `compact_batch`）
- `ingestion.includeSystemPrompt`（默认 `true`）
- `ingestion.includeToolCalls`（默认 `true`）
- `ingestion.maxBatchMessages`（默认 `200`）
- `ingestion.dedupeWindowMs`（默认 `300000`）
- `ingestion.fallbackToAfterTurn`（默认 `true`）

### 6.4 profile / skill / tool memory

- `profileInjection.enabled`（默认 `true`）
- `profileInjection.qualityGate.minScore`（默认 `0.7`）
- `profileInjection.maxChars`（默认 `1200`）
- `skillMemory.enabled`（默认 `true`）
- `skillMemory.maxItems`（默认 `3`）
- `skillMemory.maxChars`（默认 `800`）
- `toolMemory.enabled`（默认 `true`）
- `toolMemory.maxItemsPerTool`（默认 `3`）
- `toolMemory.maxChars`（默认 `1000`）

### 6.5 主动能力与观测

- `agenticRetrieval.enabled`（默认 `true`）
- `activeCommit.enabled`（默认 `true`）
- `ovCli.preferCli`（默认 `true`）
- `ovCli.injectGuidance`（默认 `true`）
- `telemetry.logLevel`
- `telemetry.emitMetrics`（默认 `true`）
- `privacy.redactPii`（默认 `true`）

---

## 7. 工具与注入设计

### 7.1 Tools

1) `commit_memory`
- 参数：`memory_content`, `memory_type`, `priority`, `category`, `targetUri?`
- 行为：即时写入与提交，返回提取/状态结果

2) `search_memories`
- 参数：`query`, `limit?`, `scoreThreshold?`, `targetUri?`
- 行为：主动检索，结构化返回

### 7.2 注入策略

- 默认 `simulated_tool_result` 注入自动检索结果
- 可切换纯文本注入（兼容模式）
- 注入预算超限时按优先级裁剪：
  1) tool memory
  2) skill memory
  3) retrieval hits
  4) profile（最后保留核心摘要）

---

## 8. 错误处理与降级

### 8.1 约束

- 任何 OpenViking 不可用不应阻断主对话
- 所有失败必须可观测（日志 + 计数）

### 8.2 矩阵

- `health` 失败：关闭当轮检索注入，继续会话
- `search/find` 超时：本轮无检索注入，下一轮重试
- `compact` 写入失败：按配置回退到 `afterTurn` 轻写
- `commit_memory` 失败：返回明确错误，不崩插件
- 注入超长：裁剪并记录原因

---

## 9. 可观测性设计

### 9.1 指标

- `retrieval.requests/hits/filtered/latency_ms`
- `injection.chars_total`
- `ingestion.compact_batches/messages_written/commit_failures`
- `tools.commit_memory.calls/success/fail`
- `tools.search_memories.calls/success/fail`
- `degradation.events`（按 reason）

### 9.2 日志关键字段

- `sessionId`, `agentId`, `runId`, `mode`, `targetUris`, `threshold`, `fallbackReason`

---

## 10. 测试策略

### 10.1 单元测试

- 配置解析与默认值
- query 构造与检索后处理
- 注入格式与裁剪
- 批量写入幂等、重试与回退

### 10.2 集成测试

- `plugins.slots.contextEngine` 可正确选中插件
- `assemble` 注入生效
- `compact` 写入+commit 生效
- tools 可调用且返回结构稳定

### 10.3 E2E

- 多轮对话记忆连续性
- OpenViking 故障不阻断对话
- 大上下文场景下稳定 compaction/retrieval

---

## 11. 迁移策略（相对旧 memory 插件）

### 11.1 复用

- 配置与 client 封装模式
- 健康检查/本地进程管理思路
- 检索后处理策略

### 11.2 升级

- 主流程由 `tools+hooks` 转为 `context-engine lifecycle`
- 写入触发由逐条转为 compact 批量
- 检索注入以 `assemble` 为核心入口

---

## 12. 里程碑

- M1：插件骨架与注册通路
- M2：核心闭环（session-start + per-turn + compact-batch）
- M3：扩展能力（skill/tool memory + agentic tools + CLI guidance）
- M4：稳定化（降级、指标、测试与文档）

---

## 13. 风险与规避

1. 注入过重影响回答质量
   - 规避：阈值、预算、裁剪优先级

2. compact 写入时延导致短期“记不住”
   - 规避：保留 `commit_memory` 即时写

3. skill memory 命中误判
   - 规避：仅在技能读取链路触发，不泛化普通文档读

4. local 模式稳定性波动
   - 规避：健康检查、启动超时、自动降级

---

## 14. 非目标（V1 不做）

- timeout 触发型抽取策略（作为 vNext）
- OpenClaw 核心代码改造（首版不依赖）
- 复杂策略学习闭环（仅保留轻量可观测）

---

## 15. 验收标准（Definition of Done）

- 插件以 `context-engine` 身份可被 OpenClaw 正常选中并运行
- 核心链路（start/retrieve/compact/active commit）全部可用
- OpenViking 故障时对话不中断
- 关键测试通过，文档可用于安装与验证
