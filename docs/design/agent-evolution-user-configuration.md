# OpenViking Agent 进化用户配置设计

## 1. 目标

本期为个人版提供用户级 Agent 进化开关和持久化 memory type allow-list。

Agent 进化开关只控制后续 session commit 是否生成或更新以下 Agent 记忆：

- `trajectories`
- `experiences`

功能上线后的默认状态为关闭。新增用户和没有显式配置的存量用户都按 `false` 生效，不再产生或更新 `trajectories`、`experiences`。

关闭开关不会删除、移动或修改存量文件。已有 trajectory 和 experience 继续保留在原 URI，仍可被控制台展示，也可通过 `find`、`search`、`read` 消费。

本期不实现企业版交互、管理员代管、跨用户批量配置和批量迁移。配置模型和运行链路按 user 隔离，为未来企业版复用保留能力。

## 2. 配置模型

用户配置继续写入现有路径：

```text
viking://user/<user_id>/settings/user_config.json
```

配置结构扩展为：

```json
{
  "add_targets": {
    "resource_uri": null,
    "skill_uri": null
  },
  "memory": {
    "memory_types": null
  },
  "agent_evolution": {
    "enabled": false
  }
}
```

字段语义：

- `memory.memory_types`：该用户允许抽取的持久记忆类型上限。`null` 表示使用部署默认值；部署默认值也未配置时，表示允许全部已启用类型。
- `agent_evolution.enabled`：是否允许后续 commit 生成或更新 `trajectories`、`experiences`。
- `add_targets`：保留现有含义。修改 memory 配置不得覆盖该字段。

配置文件不存在或 `agent_evolution.enabled` 缺失时，内置默认值为 `false`。因此不依赖存量迁移完成度，也能保证新老个人版用户上线后默认停止生产 Agent 记忆。

部署仍可通过现有 `server.user_config_defaults` 提供默认值。最终优先级为：

```text
用户显式配置 > server.user_config_defaults > 内置默认值
```

内置 Agent 进化默认值固定为 `false`。

## 3. 生效规则

用户级 `memory.memory_types` 是持久 allow-list，session 的 `memory_policy.memory_types` 是单次 commit 的进一步限制。最终 memory types 取交集：

```text
effective_memory_types
  = enabled_registry_types
  ∩ user_memory_types
  ∩ session_memory_policy.memory_types
```

其中用户或 session 没有显式配置时，对应集合按全部已启用类型处理。

Agent 进化关闭时，再从最终集合中移除：

```text
trajectories
experiences
```

开关不影响以下行为：

- session 原始 messages 归档。
- Working Memory 摘要。
- 用户配置允许的其他记忆类型，例如 profile、preferences、events 和 cases。
- 已有 experience 的检索与读取。

`compressor_v3` 的普通提取路径和 training-case fast path 都必须检查该开关。关闭时可以继续写入 allow-list 中的 case，但不得调用 trajectory/experience 训练阶段。

## 4. Commit 流程

每次 commit 都重新读取当前 user 的配置，已创建的 session 不缓存用户开关状态。用户在控制台开启后，无需重新创建 session，下一次 commit 即生效。

执行顺序：

1. 读取 session `memory_policy`。
2. 读取当前 user 的 `user_config.json`。
3. 按优先级解析用户有效配置。
4. 计算用户 allow-list 与 session allow-list 的交集。
5. Agent 进化关闭时移除 `trajectories`、`experiences`。
6. 先归档原始 session messages。
7. 运行仍被允许的记忆提取阶段。
8. 在 commit task result 中记录最终配置和跳过原因。

task result 增加：

- `user_memory_types`
- `effective_memory_types`
- `agent_evolution_enabled`
- `agent_memory_skip_reason`
- `user_config_error`，仅配置非法时返回

`agent_memory_skip_reason` 可能为：

- `agent_evolution_disabled`
- `memory_types_filtered`
- `invalid_user_config`

用户配置非法时，commit 仍完成 session 归档，但本次不运行持久记忆抽取，避免错误配置继续写入记忆文件。

## 5. API

### 读取当前用户配置

```http
GET /api/v1/user-settings/memory
```

返回用户显式配置和最终有效配置：

```json
{
  "status": "ok",
  "result": {
    "override": {
      "memory_types": null,
      "agent_evolution_enabled": null
    },
    "effective": {
      "memory_types": ["cases", "events", "preferences", "profile"],
      "agent_evolution_enabled": false
    }
  }
}
```

GET 是只读操作，不自动创建 `user_config.json`。

### 部分更新当前用户配置

```http
PATCH /api/v1/user-settings/memory
Content-Type: application/json
```

开启 Agent 进化：

```json
{
  "agent_evolution_enabled": true
}
```

同时设置用户 memory type allow-list：

```json
{
  "memory_types": ["profile", "preferences", "events", "cases", "trajectories", "experiences"],
  "agent_evolution_enabled": true
}
```

字段未传表示不修改；显式传 `null` 表示清除用户覆盖值并回退到部署或内置默认值。空数组是合法 allow-list，表示不抽取任何持久记忆。

未知或未启用的 memory type 返回 `INVALID_ARGUMENT`。

## 6. SDK 与 CLI

Python SDK 提供：

```python
settings = await client.get_memory_settings()

await client.patch_memory_settings(agent_evolution_enabled=True)
await client.patch_memory_settings(memory_types=["profile", "trajectories", "experiences"])
await client.patch_memory_settings(agent_evolution_enabled=None)
```

同步客户端提供同名方法。

CLI 提供：

```bash
ov user-settings memory

ov user-settings set-memory \
  --agent-evolution-enabled true

ov user-settings set-memory \
  --memory-types profile,preferences,events,cases,trajectories,experiences

ov user-settings set-memory \
  --clear-agent-evolution-enabled

ov user-settings set-memory \
  --clear-memory-types
```

## 7. 存量用户与存量文件

本期不扫描 trajectory/experience 目录决定开关状态，也不因为存量文件存在而自动开启。

存量用户配置文件没有新字段时：

- 运行期直接解析为 `agent_evolution_enabled=false`。
- 原有 `add_targets` 保持不变。
- `memory.memory_types` 保持 `null`，继续沿用原有全部已启用类型语义，但 Agent 记忆类型会被总开关移除。
- 存量 trajectory/experience 文件保持原样。

内核提供单 user 幂等初始化函数，可由个人版 onboarding 或部署迁移流程在需要落盘时调用。该函数只在字段缺失时写入 `false`，不会覆盖用户已经显式设置的 `true` 或 `false`。运行正确性不依赖该初始化函数被调用。

## 8. 并发与兼容

`user_config.json` 同时保存 add targets 和 memory 配置。所有更新使用同一文件锁执行 read-modify-write：

1. 锁定当前 user 的配置文件路径。
2. 读取完整配置。
3. 只修改目标字段。
4. 校验完整配置。
5. 使用同一锁句柄写回。

删除 add locations 只清空 `add_targets`，不得删除整个 `user_config.json`，否则会误删 Agent 进化配置。

旧配置只包含 `add_targets` 时可以直接解析。新字段使用 optional 模型，避免要求离线升级已有文件。

## 9. Future TODO：企业版

以下内容不在本期实现：

- 企业管理员按 user 查看和修改配置。
- account 下用户列表和跨用户批量操作。
- 企业版用户创建时注入初始 memory 配置。
- 跨用户存量初始化、失败重试和迁移审计。
- 企业控制台交互和权限模型。

未来实现必须复用本期 `UserConfig`、配置读写服务和 commit 生效逻辑，不在 OpenViking 内核增加个人版/企业版分支。

## 10. 验收标准

- 配置文件不存在时，Agent 进化有效值为 `false`。
- 存量用户下一次 commit 不生成或更新 trajectory/experience。
- 关闭时 session archive 和其他允许的记忆类型仍正常处理。
- 存量 trajectory/experience 文件仍能 list、find、search 和 read。
- 用户开启后，同一个 session 的下一次 commit 恢复 Agent 记忆生产。
- 用户 allow-list 与 session allow-list 按交集生效。
- 普通提取路径和 training-case fast path 都不能绕过开关。
- PATCH 不覆盖 `add_targets`；删除 add locations 不删除 memory 配置。
- 未知 memory type 被拒绝。
- Python SDK 和 CLI 可以读取、更新及清除用户覆盖值。
