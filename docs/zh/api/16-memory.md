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

> **已弃用**：`/api/v1/search/recall` 现在只是 [`/api/v1/search/search` 的 `mode="context"`](06-retrieval.md#searchmodecontext) 之上的轻量预设，自身不再包含独立的组装逻辑。新接入请直接使用 context 面；v1 字段别名仅在本端点保留，将在下一个 minor 版本移除。响应会带上 `Deprecation: true` 头。

按记忆类型分别检索，并在预算内组合成可直接注入 Agent 上下文的记忆块。相对 context 面，`/recall` 会叠加 `purpose="coding"`、兼容 v1 的 `score_threshold=0.1`、带 `session_id` 时 `dedup_turns=5`、`query_expansion="auto"`。Coding Agent 插件会显式发送 `score_threshold=0.35`；公共 `/recall` 默认值仍为 `0.1`，避免相同请求在升级后静默减少结果。省略 `quotas` 时沿用 v1 的分桶默认值（`events=10, entities=10, preferences=3, experiences=0`）；显式传 `"quotas": null` 才改用 `purpose` 预设配比。

**v1 字段折叠**

| v1 字段 | 折叠为 | 说明 |
|---------|--------|------|
| `max_chars` | `max_tokens = max_chars / 4` | `6500` → `1625`；显式传 `max_tokens` 时以后者为准 |
| `min_score` | `score_threshold` | 都未提供时取兼容 v1 的默认值 `0.1` |
| `render: true` | 不钉档位 | 默认行为：各类别取自己的默认档 |
| `render: false` | 只返回 `entries`，`rendered` 为空 | |
| `render: "compact"` | `detail="abstract"` | 原型期的紧凑模式；把所有类别钉在摘要档 |
| v1 `quotas` 键 | 叠加在 v1 分桶默认值之上 | 键名未变；只传一部分键时其余桶保留默认值 |

context 面的参数（`max_tokens`、`detail`、`dedup_turns`、`session_id`、`query_expansion`、`exclude_uris`、`purpose`、`rewrite`、`rewrite_max_bullets`）在本端点同样接受，便于插件在尚未升级的部署上平滑过渡。

**HTTP API**

```http
POST /api/v1/search/recall
Content-Type: application/json
```

```bash
curl -X POST http://localhost:1933/api/v1/search/recall \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENVIKING_API_KEY" \
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

响应形状与 context 面一致（entries 扁平化、`rendered` 为扁平 XML）：

```json
{
  "status": "ok",
  "result": {
    "entries": [
      {
        "uri": "viking://user/default/memories/preferences/api-docs.md",
        "category": "preferences",
        "score": 0.82,
        "detail": "full",
        "text": "用户偏好在 API 文档中同时提供 HTTP、SDK 和 CLI 示例。",
        "origin": "self"
      }
    ],
    "rendered": "<memory uri=\"viking://user/default/memories/preferences/api-docs.md\" type=\"preferences\" score=\"0.82\" detail=\"full\">\n用户偏好在 API 文档中同时提供 HTTP、SDK 和 CLI 示例。\n</memory>",
    "digest": "",
    "stats": {
      "quotas": {"events": 5, "entities": 5, "preferences": 3, "experiences": 2},
      "candidates": 4,
      "returned": 1,
      "dropped": 0,
      "max_tokens": 1625,
      "used_tokens": 96,
      "tier_counts": {"full": 1},
      "peer_scope": "all",
      "origins": {"actor_peer": 0, "self": 1, "other_peer": 0},
      "deprecated": {
        "endpoint": "/api/v1/search/recall",
        "successor": "/api/v1/search/search?mode=context",
        "aliases_used": ["max_chars"]
      }
    }
  }
}
```

字段含义见 [检索 - search(mode="context")](06-retrieval.md#searchmodecontext)。相对 v1 的形状变化：`type` → `category`、`mode` → `detail`、`content`/`summary` → `text`，`rendered` 由三层嵌套改为扁平 `<memory>` 标签，`rank` 不再返回。

公共 Python、TypeScript、Go SDK 和 `ov` CLI 当前尚未封装该端点，因此本节只展示 HTTP Tab，并补充实际存在的 MCP 调用。

## 相关文档

- [会话](05-sessions.md) - commit 与 extract
- [检索](06-retrieval.md) - 搜索记忆
- [内容](12-content.md) - 读取记忆内容
