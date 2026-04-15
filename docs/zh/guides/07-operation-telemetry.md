# 操作级 Telemetry 参考

操作级 telemetry 用来让 OpenViking 在请求结果里额外返回一份结构化摘要，帮助你了解这次操作实际发生了什么，例如耗时、token 消耗、向量检索情况、队列处理进度，以及资源导入阶段统计。

适合这些场景：

- 排查请求为什么变慢
- 观察 token 或检索行为
- 把结构化执行摘要接入你自己的日志或观测系统

更完整的观测入口说明，包括健康检查、`ov tui` 和 `OpenViking Console`，请先看 [可观测性与排障](05-observability.md)。

## 基本说明

Telemetry 是按需返回的。只有你显式请求时，OpenViking 才会在响应顶层返回 `telemetry` 字段。

典型响应结构如下：

```json
{
  "status": "ok",
  "result": {"...": "..."},
  "telemetry": {
    "id": "tm_xxx",
    "summary": {
      "operation": "search.find",
      "status": "ok",
      "duration_ms": 31.2,
      "tokens": {
        "total": 24,
        "llm": {
          "input": 12,
          "output": 6,
          "total": 18
        }
      },
      "vector": {
        "searches": 3,
        "scored": 26,
        "passed": 8,
        "returned": 5
      }
    }
  }
}
```

说明：

- `telemetry.id` 是不透明的关联 ID
- `telemetry.summary` 是面向调用方的结构化摘要
- 只有本次操作实际产出的分组才会返回
- 数值型 `0` 默认不会出现在响应里

## 当前支持范围

### HTTP API

当前这些接口支持 operation telemetry：

- `POST /api/v1/search/find`
- `POST /api/v1/search/search`
- `POST /api/v1/resources/temp_upload`
- `POST /api/v1/resources`
- `POST /api/v1/skills`
- `POST /api/v1/sessions/{session_id}/commit`

### Python SDK

Python 客户端里，下面这些调用支持相同的 telemetry 语义：

- `add_resource(...)`
- `add_skill(...)`
- `find(...)`
- `search(...)`
- `commit_session(...)`
- `Session.commit(...)`

## 如何请求 telemetry

### JSON 请求

对于 JSON body，`telemetry` 支持下面两种常用写法：

```json
{"telemetry": true}
```

```json
{"telemetry": {"summary": true}}
```

`true` 和 `{"summary": true}` 的效果相同，都会返回 `telemetry.id + telemetry.summary`。

对象形态当前只开放 `summary` 这个开关。

如果不想返回 telemetry，可以省略该字段，或者显式传：

```json
{"telemetry": false}
```

```json
{"telemetry": {"summary": false}}
```

### Multipart 上传请求

`POST /api/v1/resources/temp_upload` 是 multipart form 接口。这个接口需要把 telemetry 当作表单字段传入：

```bash
curl -X POST http://localhost:1933/api/v1/resources/temp_upload \
  -H "X-API-Key: your-key" \
  -F "file=@./notes.md" \
  -F "telemetry=true"
```

这个接口当前只支持布尔形态的表单参数。

## 常见 summary 分组

summary 顶层这 3 个基础字段总会存在：

- `operation`
- `status`
- `duration_ms`

根据不同操作，还可能出现这些分组：

- `tokens`：LLM 和 embedding 的 token 统计
- `vector`：向量检索与过滤统计
- `resource`：资源导入与处理阶段摘要
- `queue`：等待模式下的队列处理统计
- `semantic_nodes`：语义节点提取统计
- `memory`：记忆提取或去重摘要
- `errors`：聚合后的错误信息

如果某个分组对本次操作不适用，就不会返回。

## 字段说明

只有这次操作实际产出的字段才会返回。某个分组缺失时，应理解为“不适用”，而不是默认等于 0。

### 顶层 telemetry 字段

| 字段 | 含义 |
| --- | --- |
| `telemetry.id` | 本次操作的不透明关联 ID |
| `summary.operation` | 操作名，例如 `search.find`、`resources.add_resource`、`session.commit` |
| `summary.status` | telemetry 最终状态，通常是 `ok` 或 `error` |
| `summary.duration_ms` | 本次操作的端到端总耗时，单位毫秒 |

### `summary.tokens`

| 字段 | 含义 |
| --- | --- |
| `summary.tokens.total` | 本次操作累计 token 总量 |
| `summary.tokens.llm.input` | LLM 输入 token 总量 |
| `summary.tokens.llm.output` | LLM 输出 token 总量 |
| `summary.tokens.llm.total` | LLM token 总量 |
| `summary.tokens.embedding.total` | embedding 模型 token 总量 |

### `summary.vector`

| 字段 | 含义 |
| --- | --- |
| `summary.vector.searches` | 向量检索调用次数 |
| `summary.vector.scored` | 被打分的候选数量 |
| `summary.vector.passed` | 通过阈值或后续过滤的候选数量 |
| `summary.vector.returned` | 最终返回给上层逻辑的结果数量 |
| `summary.vector.scanned` | 底层实际扫描的向量数量 |
| `summary.vector.scan_reason` | 本次扫描策略或扫描原因说明 |

### `summary.resource`

这个分组常见于 `resources.add_resource` 这类资源导入操作。

| 字段 | 含义 |
| --- | --- |
| `summary.resource.request.duration_ms` | add-resource 请求主流程总耗时 |
| `summary.resource.process.duration_ms` | 资源处理主流程耗时 |
| `summary.resource.process.parse.duration_ms` | 资源解析阶段耗时 |
| `summary.resource.process.parse.warnings_count` | 解析阶段 warning 数量 |
| `summary.resource.process.finalize.duration_ms` | 资源树 finalize 阶段耗时 |
| `summary.resource.process.summarize.duration_ms` | summarize 或 vectorize 阶段耗时 |
| `summary.resource.wait.duration_ms` | `wait=true` 时等待下游处理完成的耗时 |
| `summary.resource.watch.duration_ms` | 创建、更新或移除 watch 任务的耗时 |
| `summary.resource.flags.wait` | 本次请求是否使用了 `wait=true` |
| `summary.resource.flags.build_index` | 本次请求是否启用了 `build_index` |
| `summary.resource.flags.summarize` | 本次请求是否显式启用了 `summarize` |
| `summary.resource.flags.watch_enabled` | 本次请求是否启用了 watch 管理 |

### `summary.queue`

这个分组常见于需要等待队列任务完成的操作。

| 字段 | 含义 |
| --- | --- |
| `summary.queue.semantic.processed` | 已处理的 semantic queue 消息数 |
| `summary.queue.semantic.error_count` | semantic queue 错误数 |
| `summary.queue.embedding.processed` | 已处理的 embedding queue 消息数 |
| `summary.queue.embedding.error_count` | embedding queue 错误数 |

### `summary.semantic_nodes`

| 字段 | 含义 |
| --- | --- |
| `summary.semantic_nodes.total` | DAG 或语义节点总数 |
| `summary.semantic_nodes.done` | 已完成节点数 |
| `summary.semantic_nodes.pending` | 待处理节点数 |
| `summary.semantic_nodes.running` | 正在处理中的节点数 |

### `summary.memory`

这个分组常见于 `session.commit` 这类记忆提取流程。

| 字段 | 含义 |
| --- | --- |
| `summary.memory.extracted` | 本次操作最终抽取出的 memory 数量 |
| `summary.memory.extract.duration_ms` | memory extract 主流程总耗时 |
| `summary.memory.extract.candidates.total` | 最终动作执行前的候选总数 |
| `summary.memory.extract.candidates.standard` | 普通 memory candidate 数量 |
| `summary.memory.extract.candidates.tool_skill` | tool 或 skill candidate 数量 |
| `summary.memory.extract.actions.created` | 新建 memory 数量 |
| `summary.memory.extract.actions.merged` | 合并到已有 memory 的次数 |
| `summary.memory.extract.actions.deleted` | 删除旧 memory 的次数 |
| `summary.memory.extract.actions.skipped` | 被跳过的 candidate 数量 |
| `summary.memory.extract.stages.prepare_inputs_ms` | 提取前准备输入数据的耗时 |
| `summary.memory.extract.stages.llm_extract_ms` | 调用 LLM 做提取的耗时 |
| `summary.memory.extract.stages.normalize_candidates_ms` | 解析并归一化候选的耗时 |
| `summary.memory.extract.stages.tool_skill_stats_ms` | 聚合 tool 或 skill 统计的耗时 |
| `summary.memory.extract.stages.profile_create_ms` | 创建或更新 profile memory 的耗时 |
| `summary.memory.extract.stages.tool_skill_merge_ms` | 合并 tool 或 skill memory 的耗时 |
| `summary.memory.extract.stages.dedup_ms` | candidate 去重耗时 |
| `summary.memory.extract.stages.create_memory_ms` | 创建新 memory 的耗时 |
| `summary.memory.extract.stages.merge_existing_ms` | 合并到已有 memory 的耗时 |
| `summary.memory.extract.stages.delete_existing_ms` | 删除旧 memory 的耗时 |
| `summary.memory.extract.stages.create_relations_ms` | 创建 used-uri relations 的耗时 |
| `summary.memory.extract.stages.flush_semantic_ms` | flush semantic queue 的耗时 |

### `summary.errors`

| 字段 | 含义 |
| --- | --- |
| `summary.errors.stage` | 记录错误时所在的逻辑阶段 |
| `summary.errors.error_code` | 错误码或异常类型 |
| `summary.errors.message` | 人类可读的错误描述 |

## 示例

### 带 telemetry 的检索请求

```bash
curl -X POST http://localhost:1933/api/v1/search/find \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "query": "memory dedup",
    "limit": 5,
    "telemetry": true
  }'
```

### 导入资源并返回 telemetry

```bash
curl -X POST http://localhost:1933/api/v1/resources \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "path": "./docs/readme.md",
    "reason": "telemetry demo",
    "wait": true,
    "telemetry": true
  }'
```

### Python SDK

```python
from openviking import AsyncOpenVikingClient

client = AsyncOpenVikingClient(config_path="/path/to/config.yaml")
await client.initialize()

result = await client.find("memory dedup", telemetry=True)
print(result["telemetry"]["summary"]["operation"])
print(result["telemetry"]["summary"]["duration_ms"])
```

## 限制与注意事项

- 当前对外只提供 summary-only telemetry
- `{"telemetry": {"events": true}}` 不是当前支持的公开请求形态
- 事件流风格的选择参数不属于当前公开接口
- `session.commit` 只有在 `wait=true` 时才支持 telemetry
- 如果 `session.commit` 使用 `wait=false` 并请求 telemetry，服务端会返回 `INVALID_ARGUMENT`
- telemetry 的顶层结构稳定，但具体有哪些 summary 分组取决于实际操作

## 相关文档

- [可观测性与排障](05-observability.md)
- [认证](04-authentication.md)
- [系统 API](../api/07-system.md)
