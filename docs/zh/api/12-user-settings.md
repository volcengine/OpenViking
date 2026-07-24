# Agent 进化用户设置

用户设置用于控制后续 session commit 是否生成或更新 `cases`、`trajectories` 和 `experiences`。配置按用户隔离，保存在 `viking://user/<user_id>/settings/user_config.json`。

## 生效规则

- `agent_evolution_enabled` 未显式配置时默认为 `false`。
- 关闭后，session 归档、Working Memory 和其他记忆类型仍正常处理。
- 该开关同样约束 `POST /api/v1/sessions/{session_id}/extract` 手动提取入口。
- 关闭不会删除已有 case、trajectory 或 experience，也不影响已有 experience 的检索和读取。
- 开启后，session 级 `memory_policy.memory_types` 仍可限制单次 commit 的记忆类型。

本接口不提供用户级 `memory_types`。需要限制单次 commit 时，请使用 session 的 `memory_policy.memory_types`。

## 读取设置

```http
GET /api/v1/user-settings/memory
```

`override` 是当前用户显式保存的值，`effective` 是合并部署默认值和内置默认值后的结果。GET 不会创建配置文件。

```bash
curl http://localhost:1933/api/v1/user-settings/memory \
  -H "X-API-Key: your-key"
```

响应示例：

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

Python SDK：

```python
settings = await client.get_memory_settings()
```

CLI：

```bash
ov user-settings memory
```

## 更新设置

```http
PATCH /api/v1/user-settings/memory
Content-Type: application/json
```

`agent_evolution_enabled` 接受布尔值、`null` 或不传。`null` 清除用户覆盖值；字段不传表示不修改。

```bash
curl -X PATCH http://localhost:1933/api/v1/user-settings/memory \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"agent_evolution_enabled": true}'
```

Python SDK：

```python
await client.patch_memory_settings(agent_evolution_enabled=True)
await client.patch_memory_settings(agent_evolution_enabled=None)
```

CLI：

```bash
ov user-settings set-memory --agent-evolution-enabled true
ov user-settings set-memory --agent-evolution-enabled false
ov user-settings set-memory --clear-agent-evolution-enabled
```

## 相关文档

- [会话管理](05-sessions.md) - 创建 session、配置 `memory_policy` 和执行 commit
- [检索](06-retrieval.md) - 检索已有 experience
