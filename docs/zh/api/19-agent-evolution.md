# Agent 进化

Agent Evolution API 用于查询某条 Experience 被实际应用后的 Trajectory 记录及结果分布。当前仅提供 HTTP API。

## API 参考

### 查询 Experience 应用轨迹

分页返回成功读取过指定 Experience 的 Trajectory。查询仅匹配当前调用用户空间内的 Experience 和 Trajectory。

**代码入口**：

- `openviking/server/routers/agent_evolution.py:list_experience_trajectories` - HTTP 路由
- `openviking/service/agent_evolution_service.py:AgentEvolutionService.list_trajectories_by_experience` - 核心实现

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| experience_uri | string | 是 | - | 当前用户空间内的 Experience 文件 URI |
| limit | integer | 否 | 50 | 单页数量，范围为 1～1000 |
| offset | integer | 否 | 0 | 从零开始的结果偏移量 |

**HTTP API**

```
GET /api/v1/agent-evolution/experiences/trajectories?experience_uri={experience_uri}&limit=50&offset=0
```

```bash
curl -X GET "http://localhost:1933/api/v1/agent-evolution/experiences/trajectories?experience_uri=viking://user/default/memories/experiences/exchange.md&limit=50&offset=0" \
  -H "X-API-Key: your-key"
```

**响应示例**

```json
{
  "status": "ok",
  "result": {
    "experience_uri": "viking://user/default/memories/experiences/exchange.md",
    "items": [
      {
        "uri": "viking://user/default/memories/trajectories/exchange_20260805020000.md",
        "name": "exchange_20260805020000.md",
        "description": "处理换货请求",
        "created_at": "2026-08-05T02:00:00Z",
        "updated_at": "2026-08-05T02:00:00Z"
      }
    ],
    "total": 1,
    "limit": 50,
    "offset": 0,
    "has_more": false
  },
  "time": 0.01
}
```

`items` 中仅返回索引记录实际存在的 `uri`、`name`、`description`、`created_at` 和 `updated_at` 字段。

---

### 查询 Experience 应用结果分布

统计应用过指定 Experience 的 Trajectory 在五种结果状态下的数量。该查询使用精确标量标签聚合，不读取全部 Trajectory 文件。

**代码入口**：

- `openviking/server/routers/agent_evolution.py:get_experience_outcome_distribution` - HTTP 路由
- `openviking/service/agent_evolution_service.py:AgentEvolutionService.get_experience_outcome_distribution` - 核心实现

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| experience_uri | string | 是 | - | 当前用户空间内的 Experience 文件 URI |

**HTTP API**

```
GET /api/v1/agent-evolution/experiences/outcomes?experience_uri={experience_uri}
```

```bash
curl -X GET "http://localhost:1933/api/v1/agent-evolution/experiences/outcomes?experience_uri=viking://user/default/memories/experiences/exchange.md" \
  -H "X-API-Key: your-key"
```

**响应示例**

```json
{
  "status": "ok",
  "result": {
    "experience_uri": "viking://user/default/memories/experiences/exchange.md",
    "outcome_distribution": [
      {"outcome": "success", "count": 4},
      {"outcome": "failure", "count": 1},
      {"outcome": "partial", "count": 0},
      {"outcome": "unknown", "count": 0},
      {"outcome": "unfinished", "count": 0}
    ]
  },
  "time": 0.01
}
```

结果固定包含 `success`、`failure`、`partial`、`unknown` 和 `unfinished`。旧版创建且尚未重新索引的 Trajectory 没有 outcome 标签，因此不会计入分布。

## MCP 工具契约

上面两个查询接口的数据来自 Agent 在会话中实际调用的 MCP 工具。工具由服务端 `/mcp` 端点统一提供，所有接入 OpenViking MCP 的 harness 都能直接使用，无需插件侧再实现。

会话 commit 后，服务端按记录下来的工具调用做归因：`search_experience` 输出里的每条结果产出一个 `memory.recalled` 事件，每次成功的 `read_experience` 产出一个 `memory.injected` 事件，并把该 Experience 写成 Trajectory 的来源标签。因此**工具名和 JSON 输出格式是固定契约**，改动会直接让统计归零。归因时会剥离 harness 给 MCP 工具加的命名空间前缀（如 `mcp__openviking__`），所以裸名和带前缀名都能正确计数。

### `search_experience`

| 字段 | 类型 | 说明 |
|------|------|------|
| `query` | string | 必填。要检索的任务或场景描述。 |
| `limit` | integer | 可选。取值裁剪到 `[1, 20]`，默认 `5`。 |

检索范围固定为当前用户的 `viking://user/<user>/memories/experiences/`，不设分数阈值。

```json
{"results": [{"uri": "viking://user/alice/memories/experiences/no-order-exchange.md", "title": "no-order-exchange", "score": 0.61, "snippet": "用户未提供订单号但要求换货……"}]}
```

`uri` 是规范形式且归当前用户所有，`.abstract.md` / `.overview.md` / `.relations.json` 等内部文件不会出现在结果里。`snippet` 截断到 120 字符。

### `read_experience`

| 字段 | 类型 | 说明 |
|------|------|------|
| `uri` | string | 必填。`search_experience` 返回的规范 URI，必须归当前用户所有。 |

```json
{"uri": "viking://user/alice/memories/experiences/no-order-exchange.md", "content": "## Situation\n……"}
```

传入非规范形式（例如带 `?`/`#` 后缀）、跨用户或非 Experience 的 URI 会返回工具错误而非空结果——错误调用不会被计成一次注入。

## 相关文档

- [会话](05-sessions.md) - 提交会话并生成 Agent Evolution 记忆
- [记忆](16-memory.md) - 记忆读取与召回
