# 记忆

记忆由会话提交或显式提取生成，存储在用户记忆命名空间中，并可通过内容、文件系统和检索 API 使用。

## 内置记忆类型

| 分类 | 位置 | 说明 |
|------|------|------|
| profile | `viking://user/{user_id}/memories/profile.md` | 用户个人信息 |
| preferences | `viking://user/{user_id}/memories/preferences/` | 按主题分类的用户偏好 |
| entities | `viking://user/{user_id}/memories/entities/` | 重要实体（人物、项目等） |
| events | `viking://user/{user_id}/memories/events/` | 重要事件 |
| identity | `viking://user/{user_id}/memories/identity.md` | 助手身份与自我介绍 |
| soul | `viking://user/{user_id}/memories/soul.md` | 助手原则、边界、风格和连续性 |
| cases | `viking://user/{user_id}/memories/cases/` | 可训练、可评估的任务案例 |
| trajectories | `viking://user/{user_id}/memories/trajectories/` | 从 Agent 任务轨迹提炼的可复用操作契约 |
| experiences | `viking://user/{user_id}/memories/experiences/` | 可复用的执行经验 |
| tools | `viking://user/{user_id}/memories/tools/` | 工具使用经验与最佳实践 |
| skills | `viking://user/{user_id}/memories/skills/` | 技能执行经验与工作流策略 |

以上是随部署提供的内置类型，`tools` 和 `skills` 默认关闭；实际抽取还取决于生效的记忆策略和 Agent 进化设置。表中为 Self 路径，支持 Peer 的类型也可存放在 `viking://user/{user_id}/peers/{peer_id}/memories/` 下。部署模板可以扩展或覆盖 Registry；Account 模板 API 只允许修改其列出的字段和类型。

---

## 记忆召回

用 [`search(mode="context")`](06-retrieval.md#search-mode-context) 检索记忆并组装可直接注入的记忆块。服务端 MCP 以 `search(mode="context")` 提供同一能力，没有单独的 `recall` 工具。

```http
POST /api/v1/search/recall
Content-Type: application/json
```

`/api/v1/search/recall` 已弃用。它只是 `search(mode="context")` 叠加 `purpose="coding"` 和 v1 默认值的预设，为现有调用方保留；响应带 `Deprecation: true` 头。新接入不要使用。

## 相关文档

- [会话](05-sessions.md) - commit 与 extract
- [检索](06-retrieval.md) - 搜索记忆
- [内容](12-content.md) - 读取记忆内容
