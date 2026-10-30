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

## 规则

规则是编译生成的行为约束，以 Markdown 存储在 `viking://user/{user_id}/memories/rules` 下，按当前认证的账号和用户隔离。

### 编译规则

```http
POST /api/v1/rules/compile
Content-Type: application/json
```

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| instruction | string | 否 | null | 规则指令，最多 64,000 个字符；自动去除首尾空白 |
| sources | object[] | 否 | [] | 最多 16 个来源，每项为已导入资源的 `{"resource_uri":"viking://resources/policy"}`，或[资源导入请求](02-resources.md) |

必须提供非空 `instruction` 或至少一个来源。来源必须能解析为可读文本，不支持多模态来源。不接受未知的顶层字段。

```json
{
  "instruction": "删除文件前必须征求确认。",
  "sources": []
}
```

返回 HTTP `202 Accepted`，响应格式为 `{"status":"ok","result":...}`，其中 `result` 是任务记录，`task_type="rule_compile"`，任务 ID 以 `rul_` 开头。通过[任务 API](17-tasks.md) 的 `GET /api/v1/tasks/{task_id}` 轮询状态。完成后的任务结果包含 `rules_root`、`source_uris`、`plan_reason`、`batch_count`、`operations`、`written_uris` 和 `write_result`。内容索引作为独立后台工作运行，可通过 `write_result` 定位。

### 检索适用规则

```http
POST /api/v1/rules/search
Content-Type: application/json
```

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| task_context | string | 是 | - | 当前任务事实，1–64,000 个字符；去除首尾空白后必须非空 |
| rule_probability_threshold | number | 否 | 0.65 | 最低规则适用概率，取值范围为 0 到 1，包含端点 |

```json
{
  "task_context": "删除工作区中自动生成的文件。",
  "rule_probability_threshold": 0.65
}
```

返回 HTTP `200 OK`，响应格式为 `{"status":"ok","result":...}`。仅检索 active 状态的规则。不接受未知请求字段。

| 结果字段 | 说明 |
|----------|------|
| rules | 匹配规则，每项包含 `atomic_id`、`revision`、`scenario_id`、`probability`、`uri` 和完整 Markdown `content` |
| scenarios | 召回的场景 ID |
| rendered | 可供 Agent 上下文使用的规则正文和应用指引 |
| delivery | `complete`、`rule_count` 和 `rendered_chars` 描述交付内容 |
| scenario_selection | 场景筛选诊断信息，可为 null |
| judge、warning | 可选的判定器标识和降级警告 |

## 相关文档

- [会话](05-sessions.md) - commit 与 extract
- [检索](06-retrieval.md) - 搜索记忆
- [内容](12-content.md) - 读取记忆内容
