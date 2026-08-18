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

## 检索记忆

使用 [`POST /api/v1/search/search` 的 `mode="context"`](06-retrieval.md#searchmodecontext)
跨记忆、资源和技能组装可直接注入的上下文块。MCP `search` 工具的
`mode="context"` 提供相同能力。

已弃用的 `/api/v1/search/recall` 兼容端点（此前接受 POST 请求）现已移除。
仍在使用 v1 字段的客户端需要显式迁移：

| 已移除的 v1 字段 | context search 字段 | 迁移方式 |
|------------------|---------------------|----------|
| `max_chars` | `max_tokens` | 除以 4，并将结果下限设为 64 token |
| `min_score` | `score_threshold` | 如需保留旧端点默认值，请显式设为 `0.1` |
| 部分 `quotas` | `quotas` | 发送前先将部分值叠加到旧分桶默认值上 |
| `render: "compact"` | `detail: "abstract"` | 将所有返回类别固定为摘要档 |
| `render: false` | — | 忽略 `rendered`，只消费 `entries` |

如需完整保留旧预设，还应传入 `purpose="coding"`、
`query_expansion="auto"`，并在存在 `session_id` 时传
`dedup_turns=5`。此前若省略 `quotas`，请显式指定旧分桶默认值：
`events=10`、`entities=10`、`preferences=3`、`experiences=0`。

## 相关文档

- [会话](05-sessions.md) - commit 与 extract
- [检索](06-retrieval.md) - 搜索记忆
- [内容](12-content.md) - 读取记忆内容
