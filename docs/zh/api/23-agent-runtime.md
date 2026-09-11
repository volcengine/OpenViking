# Agent Runtime API

Agent Runtime Server 负责执行 Agent 任务，当前支持 Compile。应用通过 OpenViking 的 Compile API 提交任务，OpenViking 负责校验请求、持久化任务和管理生命周期，再调用 Runtime 执行接口；内置 VikingBot 也实现了同一执行协议，可用于本地部署。

内置 VikingBot 的 Compile 通过现有 `exec` 工具调用运行环境中的 `ov` CLI，按需读取 Skill、来源和已有目标内容。运行环境须启用命令执行、安装 `ov`，并配置好 CLI 的连接和身份；Compile 沿用该配置。任务启动时不下载输入文件、预加载目录摘要或注入 Skill 正文。Agent 生成结果后仍调用统一提交工具，由服务校验并写入目标；Resource 输出暂存于任务工作区的 `__compile_staging__/output/`，未提交的已有目标文件保持不变。

开启 `bot.agents.subagent_enabled`（默认 `true`）时，Compile 使用 `spawn` 分工，通过 `wait_subagents` 领取文件清单、摘要和失败信息。每个子 Agent 独立维护上下文，写入路径和默认 shell 工作目录绑定到 `__compile_staging__/drafts/<id>/`；`read_file` 也支持按返回的完整草稿路径读取同任务的其他子 Agent 产物。子 Agent 使用目标相对路径写入，以 `submit_compile_draft(summary=...)` 提交，不能继续派生或提交最终结果。

Resource 编译采用两阶段分工。来源子任务提交内容页，运行时返回稳定草稿 ID、真实路径和字符数。主 Agent 按主题、别名和相关旧页面分组，通过 `merge_compile_drafts(groups=[{name, task, draft_ids, existing_pages, reuse}, ...])` 增量保存归属。组名稳定，省略的组、字段和草稿分配保持不变；同组重复 ID 自动去重，跨组重复记录为冲突，未知 ID 单独报告，不丢弃其他正确分配。只需补交缺失或冲突项；尚未处理的草稿可移动到另一组。调用 `run=true` 前必须完成全部来源的无歧义分配，主 Agent 不逐个调度或复制成品。

合并不限制文件数量，每批完整任务文本限制为 60,000 字符，包含新输入片段、已有页面片段、上一批完整暂存结果和 JSON 开销；新片段至多使用预算的一半。运行时复用 `split_source` 按字符区间无重叠切片，大文件和已选旧页面也参与预算。不同主题并行，同一主题逐批更新暂存结果；每批校验输入到输出的对应关系及来源保留，成功后保存字符进度，失败重试从上一批继续。标记 `reuse=true` 的独立单稿，仅在校验通过且原目标路径不存在时直接收集。整个组完成后才复制成品；最终提交检查全部输入、输出和来源引用。若暂存结果已占满预算，停止该组并保留进度、报告超出预算，不再派发超大任务。该检查保证流转和引用完整，不等同于逐条事实的语义等价证明。主 Agent 默认 120 轮，子 Agent 70 轮。

Resource 最终校验失败后，主 Agent 在最多 3 轮修复的每一轮收到具体错误和剩余修复轮数；第二次无效提交也会结束修复。修复结束时，运行时将 `__compile_staging__/output/` 中所有文件按原始字节 upsert 到 `to`，不再执行内容、链接或合并覆盖校验。写入成功后任务标为 `completed`，不附加未完成提示；未涉及的目标文件保持不变。文件读取、权限和实际写入失败仍会导致任务失败。

Resource 子任务编译在最终提交前达到总轮次上限时，任务返回 `failed/COMPILE_INCOMPLETE`。失败或取消保留任务工作区中的草稿及 `__compile_staging__/merge-state.json`，供恢复使用。

`bot.agents.subagent_max_concurrency` 控制每个 Compile 同时运行的子 Agent 数量（默认 `8`，不包含主 Agent）。运行中、排队和已完成但未领取的结果合计不超过该值的两倍；排队任务会自动启动。来源阶段由主 Agent 按 `wait_subagents` 返回的容量派发；Resource 合并阶段由运行时自动调度完整计划，并仅重试未完成输入。主题判断由 Agent 负责，运行时校验草稿覆盖、输出路径、来源保留和 OKF 格式。

`wait_subagents()` 默认等待到有子任务完成、失败或已无子任务；空等期间不再触发主 Agent 的模型调用，也不增加循环轮次，取消可中断等待。主 Agent 还有其他工作可做时，使用 `wait_subagents(block=false)` 立即领取已有结果。

同一个 VikingBot 服务内的所有 Compile 任务共享 `vlm.max_concurrent` 指定的模型请求并发额度（默认 `32`，必须为正数），包含主 Agent、子 Agent 和 compact 请求；流式响应结束或关闭后才释放额度。执行工具和等待子任务不占模型额度。该额度不包含普通聊天、语义处理及其他服务进程，也不限制每分钟 Token 数。

**代码入口**：

- `openviking/server/routers/compile.py` - 创建 Compile 任务
- `openviking/server/routers/tasks.py` - 查询和取消任务
- `openviking/service/compile_service.py` - Runtime 调用与任务状态收敛

## Compile 任务接口

### 创建任务

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `from` | string[] | 是 | - | 一个或多个来源目录 |
| `to` | string | 是 | - | 目标 Resource 或 Memory 目录，或受支持的 Skill namespace |
| `skill` | string | 是 | - | Skill 目录或其 `SKILL.md` URI |
| `instruction` | string | 否 | Skill 驱动的默认值 | 本次 Compile 的补充指令 |
| `args` | object | 否 | - | 执行端扩展参数；`model_name` 可传模型 Endpoint ID |

`args` 整体可省略，模型 Endpoint ID 也不是顶层字段。需要指定模型时使用 `args.model_name`；不传时由执行端使用其默认模型配置。

**HTTP API**

```http
POST /api/v1/compile
```

```bash
curl -X POST http://localhost:1933/api/v1/compile \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "from": ["viking://resources/research"],
    "to": "viking://resources/research-wiki",
    "skill": "viking://user/default/skills/research-compiler",
    "instruction": "追踪历史进展，并保留支撑证据。",
    "args": {"model_name": "your-model-endpoint-id"}
  }'
```

接口返回 `202 Accepted` 和 OV TaskRecord：

```json
{
  "status": "ok",
  "result": {
    "task_id": "cmp_01abc",
    "task_type": "compile",
    "status": "pending",
    "stage": "queued",
    "resource_id": "viking://resources/research"
  }
}
```

**CLI**

```bash
ov compile \
  --from viking://resources/research \
  --to viking://resources/research-wiki \
  --skill viking://user/default/skills/research-compiler \
  --instruction "追踪历史进展，并保留支撑证据。" \
  --args '{"model_name":"your-model-endpoint-id"}'
```

`--args` 必须是 JSON object。命令提交后立即返回 Task ID。

**SDK**

Python、TypeScript 和 Go SDK 都通过各自的 Compile options 传递 `instruction` 和 `args`：

::: code-group

```python [Python]
task = client.compile(
    ["viking://resources/research"],
    "viking://resources/research-wiki",
    "viking://user/default/skills/research-compiler",
    {"args": {"model_name": "your-model-endpoint-id"}},
)
```

```ts [TypeScript]
const task = await client.compile(
  ["viking://resources/research"],
  "viking://resources/research-wiki",
  "viking://user/default/skills/research-compiler",
  { args: { model_name: "your-model-endpoint-id" } },
);
```

```go [Go]
task, err := client.Compile(
    ctx,
    []string{"viking://resources/research"},
    "viking://resources/research-wiki",
    "viking://user/default/skills/research-compiler",
    &openviking.CompileOptions{
        Args: map[string]any{"model_name": "your-model-endpoint-id"},
    },
)
```

:::

### 查询任务

任务仅对创建它的 principal 可见；任务不存在或属于其他 principal 时均返回 `404`。

```http
GET /api/v1/tasks/{task_id}
```

```bash
ov task status cmp_01abc
```

任务进入终态后，响应还会包含结果或错误。

### 取消任务

```http
POST /api/v1/tasks/{task_id}/cancel
```

```bash
ov task cancel cmp_01abc
```

任务会先进入 `cancelling`，待当前进程内工作和清理完成后进入 `cancelled`；已经完成的写入不会回滚。重复取消已经 `cancelled` 的任务是幂等的。

| Status | 常见 Stage |
|--------|------------|
| `pending` | `queued` |
| `running` | 执行端返回的执行 Stage，例如 `agent`、`writing` |
| `cancelling` | 收敛当前进程内工作和清理资源 |
| `completed` | `completed` |
| `failed` | 失败 Stage，或 `partial`、`salvaged`；包含 `error`，部分保存时还包含 `result` |
| `cancelled` | `cancelled` |

### 旧接口

OV 上的以下旧 VikingBot 代理路由已经停用，只返回迁移提示：

```http
POST /bot/v1/compile
GET /bot/v1/compile/{task_id}
POST /bot/v1/compile/{task_id}/cancel
```

创建任务使用 `/api/v1/compile`，查询和取消统一使用 `/api/v1/tasks/{task_id}`。

## Runtime 执行接口

以下接口由 `compile_api.base_url` 指向的执行服务提供，供 OV 调用。应用通过前述 Compile API 提交任务。

### 创建执行任务

```http
POST /runtime/v1/tasks
Idempotency-Key: <OV task_id>
```

```json
{
  "task_type": "compile",
  "payload": {
    "from": ["viking://resources/research"],
    "to": "viking://resources/research-wiki",
    "skill": "viking://agent/skills/wiki",
    "instruction": "整理成知识库",
    "args": {"model_name": "your-model-endpoint-id"}
  }
}
```

`task_type` 和 `payload` 均必填。当前只支持 `task_type="compile"`；`payload` 使用本页创建任务的字段及校验规则，`instruction`、`args` 可省略。内置 VikingBot 不支持非空 `args`。不支持的类型或无效的 payload 返回 `4xx`，不创建执行任务。

OV 通过 `X-API-Key` 传递当前用户的 OV API Key；配置 `compile_api.gateway_token` 时还会发送 `X-Gateway-Token`。这些凭证不能出现在公开任务结果中。

接受请求后返回 `202 Accepted`，响应包含执行端标识：

```json
{"session_id": "session-123"}
```

同一用户的相同 `Idempotency-Key` 必须返回同一个 `session_id`，不能重复执行。`session_id` 用于执行端查询和取消；应用查询 OV 任务时使用 `task_id`。

### 查询与取消执行任务

```http
POST /runtime/v1/tasks/status
POST /runtime/v1/tasks/cancel
```

两个接口均使用以下请求体：

```json
{"session_id": "session-123"}
```

两个接口均返回执行状态，例如：

```json
{
  "status": "running",
  "stage": "compile: agent",
  "error": null,
  "meta": {},
  "result": null
}
```

`status` 可为 `pending`、`running`、`cancelling`、`completed`、`failed` 或 `cancelled`。取消请求可以先返回 `cancelling`，实际执行停止并完成清理后再返回 `cancelled`；重复取消已结束任务返回其当前终态。查询和取消均须校验任务归属。

## 相关文档

- [后台任务](17-tasks.md) - 通用任务查询、取消和列表接口
- [上下文编译](../context-compilation/01-overview.md) - Compile 使用场景和示例
- [Skills API](04-skills.md) - 管理 Compile 使用的 Skill
