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

## 相关文档

- [会话](05-sessions.md) - 提交会话并生成 Agent Evolution 记忆
- [记忆](16-memory.md) - 记忆读取与召回
