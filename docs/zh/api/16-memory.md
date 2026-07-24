# 记忆

记忆由会话提交或显式提取生成，存储在用户记忆命名空间中，并可通过内容、文件系统和检索 API 使用。

## 内置记忆类型

| 分类 | 位置 | 说明 |
|------|------|------|
| profile | `user/memories/profile.md` | 用户个人信息 |
| preferences | `user/memories/preferences/` | 按主题分类的用户偏好 |
| entities | `user/memories/entities/` | 重要实体（人物、项目等） |
| events | `user/memories/events/` | 重要事件 |
| identity | `user/memories/identity.md` | 助手身份与自我介绍 |
| soul | `user/memories/soul.md` | 助手原则、边界、风格和连续性 |
| cases | `user/memories/cases/` | 可训练、可评估的任务案例 |
| trajectories | `user/memories/trajectories/` | 可复用的操作契约 |
| experiences | `user/memories/experiences/` | 可复用的执行经验 |
| tools | `user/memories/tools/` | 工具使用经验与最佳实践 |
| skills | `user/memories/skills/` | 技能执行经验与工作流策略 |

以上是当前启用的内置类型；部署可以通过自定义记忆模板扩展或覆盖。

---

## API 参考

### recall()

按记忆类型分别检索，并在字符预算内组合成可直接注入 Agent 上下文的记忆块。默认检索 `events`、`entities` 和 `preferences`；`experiences` 默认配额为 `0`，需要时应显式开启。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `query` | string | 是 | - | 召回查询 |
| `quotas` | object | 否 | `events=10, entities=10, preferences=3, experiences=0` | 各类型最大返回数 |
| `max_chars` | integer | 否 | `6500` | 渲染后记忆块的最大字符数 |
| `min_score` | number | 否 | `0.1` | 最低相关性分数 |
| `peer_scope` | string | 否 | `all` | `actor` 只检索当前 actor peer；`all` 同时检索用户全局和其他 peer |
| `other_peer_penalty` | number/object | 否 | 按类型默认值 | 对其他 peer 结果施加的分数折损 |
| `render` | boolean | 否 | `true` | 是否生成 `rendered` 记忆块 |

**HTTP API**

```http
POST /api/v1/search/recall
Content-Type: application/json
```

```bash
curl -X POST http://localhost:1933/api/v1/search/recall \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "query":"OpenViking API 文档偏好",
    "quotas":{"events":5,"entities":5,"preferences":3,"experiences":2},
    "max_chars":6500,
    "peer_scope":"all"
  }'
```

**MCP**

```text
recall(
  query="OpenViking API 文档偏好",
  quotas={"events": 5, "entities": 5, "preferences": 3, "experiences": 2},
  max_chars=6500,
  peer_scope="all"
)
```

**响应**

返回 `entries`、`rendered` 和 `stats`。`entries` 保留结构化结果；`rendered` 是适合直接注入上下文的有界文本。

公共 Python、TypeScript、Go SDK 和 `ov` CLI 当前尚未封装类型配额召回，因此本节只展示 HTTP Tab，并补充实际存在的 MCP 调用。

## 相关文档

- [会话](05-sessions.md) - commit 与 extract
- [检索](06-retrieval.md) - 搜索记忆
- [内容](12-content.md) - 读取记忆内容
