# OpenViking Agent 进化用户配置设计

## 1. 目标

本期提供用户级 `agent_evolution_enabled` 开关，控制后续 session commit 是否生成或更新 Agent 记忆：

- `trajectories`
- `experiences`

配置不存在时默认关闭。关闭不删除已有文件，也不影响已有 experience 的检索和读取。

本期不提供用户级持久 `memory_types`。现有 session 级 `memory_policy.memory_types` 保留，继续作为单次 commit 的记忆类型 allow-list。

## 2. 配置模型

用户配置写入现有路径：

```text
viking://user/<user_id>/settings/user_config.json
```

配置结构：

```json
{
  "add_targets": {
    "resource_uri": null,
    "skill_uri": null
  },
  "agent_evolution": {
    "enabled": false
  }
}
```

`agent_evolution.enabled` 未设置时，按以下优先级解析：

```text
用户显式配置 > server.user_config_defaults > false
```

修改 Agent 进化配置不得覆盖同一文件中的 `add_targets`。

## 3. 生效规则

每次 commit 重新读取当前用户配置，已创建的 session 不缓存开关状态。

- `agent_evolution_enabled=false`：从本次 commit 的有效类型中移除 `trajectories` 和 `experiences`。
- `agent_evolution_enabled=true`：不改写 session 的 `memory_policy.memory_types`。
- session 未设置 `memory_policy.memory_types`：使用当前已启用的默认记忆类型。
- session 显式设置 `memory_policy.memory_types`：仅本次 commit 按该 allow-list 提取。

因此用户级开关是 Agent 记忆生产的总开关，session policy 是单次请求的进一步限制。session policy 不能绕过已关闭的用户级开关。

Agent 进化关闭时，以下行为保持不变：

- session 原始 messages 归档。
- Working Memory 摘要。
- profile、preferences、events、cases 等非 Agent 记忆提取。
- 已有 trajectory 和 experience 文件的 list、find、search、read。

## 4. Commit 流程

1. 读取并校验 session `memory_policy`。
2. 读取当前用户的 `user_config.json`。
3. 解析 `agent_evolution_enabled`。
4. 开关关闭时，从 session policy 中移除 `trajectories` 和 `experiences`。
5. 归档原始 session messages。
6. 按最终 session policy 运行记忆提取。
7. 在 task result 中返回 `effective_memory_types`、`agent_evolution_enabled` 和 `agent_memory_skip_reason`。

`agent_memory_skip_reason` 可能为：

- `agent_evolution_disabled`
- `memory_types_filtered`
- `invalid_user_config`

用户配置非法时，commit 仍完成归档并保留非 Agent 记忆处理能力，但本次不生成或更新 trajectory/experience。

队列消息显式保存 commit 时解析出的开关和最终 session policy，确保异步 Phase 2 不受后续用户配置变化影响。旧队列消息没有开关字段时按历史行为处理，即默认开启 Agent 记忆生产。

## 5. API

读取当前用户设置：

```http
GET /api/v1/user-settings/memory
```

响应：

```json
{
  "status": "ok",
  "result": {
    "override": {
      "agent_evolution_enabled": null
    },
    "effective": {
      "agent_evolution_enabled": false
    }
  }
}
```

部分更新当前用户设置：

```http
PATCH /api/v1/user-settings/memory
Content-Type: application/json

{
  "agent_evolution_enabled": true
}
```

字段不传表示不修改。显式传 `null` 表示清除用户覆盖值，回退到部署默认值或内置默认值。请求中的未知字段会被拒绝。

## 6. SDK 与 CLI

Python SDK：

```python
settings = await client.get_memory_settings()
await client.patch_memory_settings(agent_evolution_enabled=True)
await client.patch_memory_settings(agent_evolution_enabled=None)
```

CLI：

```bash
ov user-settings memory
ov user-settings set-memory --agent-evolution-enabled true
ov user-settings set-memory --agent-evolution-enabled false
ov user-settings set-memory --clear-agent-evolution-enabled
```

## 7. 存量兼容

- 没有显式配置的新增用户和存量用户，有效值均为 `false`。
- 存量 trajectory/experience 文件保持原 URI 和检索可见性。
- 幂等初始化函数只在开关缺失时写入 `false`，不覆盖用户显式值。
- `user_config.json` 的更新使用文件锁执行 read-modify-write，避免并发覆盖 `add_targets` 或 Agent 进化配置。

## 8. Future TODO：企业版

本期不实现企业管理员代管、跨用户批量配置、用户列表和企业控制台交互。未来企业版复用同一 `UserConfig`、配置读写服务和 commit 生效逻辑，不在内核增加个人版/企业版分支。

## 9. 验收标准

- 配置不存在时，Agent 进化有效值为 `false`。
- 关闭时不生成或更新 trajectory/experience，归档和其他记忆正常处理。
- 开启后，同一 session 的下一次 commit 即可恢复 Agent 记忆生产。
- session 级 `memory_policy.memory_types` 仍能限制单次 commit。
- PATCH Agent 进化设置不覆盖 `add_targets`。
- API、SDK 和 CLI 不再提供用户级 `memory_types`。
