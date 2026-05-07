# OpenClaw 多租与身份透传测试报告

## 1. 测试目标

本轮测试围绕 `examples/openclaw-plugin` 的多租户与会话身份透传能力展开，重点验证以下目标：

1. 验证插件在 `api_key` 与 `trusted` 两种服务认证模式下的请求头行为是否正确。
2. 验证插件是否按照服务侧命名空间策略，将 `viking://user/memories` 与 `viking://agent/memories` 正确展开为 canonical URI。
3. 验证 `senderId` / `requesterSenderId` 是否能够稳定映射为会话消息的 `role_id`。
4. 验证 `afterTurn` 与 `memory_store` 两条写会话路径的身份语义是否一致。
5. 验证多租户场景下默认用户空间、按 agent 隔离空间、共享 agent 空间等路径的召回行为是否符合预期。

## 2. 测试方法

### 2.1 服务端直连验证

在 OpenClaw 之前，先通过 OpenViking HTTP API 直接验证服务端状态与数据面：

- 使用 `curl` 调用 `/health`、`/api/v1/system/status`、`/api/v1/search/find`
- 直接验证显式 URI，例如：
  - `viking://user/default/memories`
  - `viking://user/default/agent/main/memories`
  - `viking://agent/main/memories`
  - `viking://agent/main/user/default/memories`
- 通过结构化 seed 与分层 `reindex`，确保召回验证基线可用

### 2.2 插件端到端验证

通过真实 OpenClaw gateway 与 OpenViking 后端联调验证：

- 远端 `19950`：用于 `api_key` 模式与命名空间矩阵验证
- 远端 `19960`：用于 `trusted` 模式与 `role_id` 验证
- 使用真实 Feishu 机器人入口验证真实 sender 映射
- 结合以下日志进行判定：
  - `before_prompt_build`
  - `find POST`
  - `session message POST`
  - `session commit POST`

### 2.3 真实机器人验证

针对 `role_id` 透传行为，使用真实机器人私聊/群聊场景复核：

- 私聊验证真实 sender 到 `role_id` 的稳定映射
- 群聊验证不同成员写入不同 `role_id`
- 工具调用验证 `memory_store` 路径是否能通过 `requesterSenderId` 写出 `role_id`

## 3. 测试范围

### 3.1 认证模式

- `api_key`
- `trusted`
- `trusted + root_api_key`

### 3.2 命名空间策略

- `ff`：user 不按 agent 隔离，agent 不按 user 隔离
- `tf`：user 按 agent 隔离，agent 不按 user 隔离
- `tt`：user 按 agent 隔离，agent 按 user 隔离
- 兼容路径：
  - `agentScopeMode=agent`
  - 新 policy 覆盖旧 alias

### 3.3 身份透传

- `afterTurn.runtimeContext.senderId -> role_id`
- `memory_store.requesterSenderId -> role_id`
- assistant message 不传 `role_id`

## 4. 测试结果

### 4.1 通过项

| 维度 | case | 结果 | 说明 |
|---|---|---|---|
| `api_key` | `api_key_without_key_dev` | 通过 | 不再合成 `default/default` 租户 header |
| `api_key` | `personal_token_default` | 通过 | 命中 `DEFAULT_USER_TOKEN_19950` |
| `api_key` | `agentid_prefix_worker` | 通过 | 实际 agent 值为 `worker_main` |
| 兼容 | `deprecated_agent_alias` | 通过 | `agentScopeMode=agent` 兼容正常 |
| 覆盖优先级 | `new_policy_overrides_deprecated` | 通过 | 新 policy 覆盖旧 alias |
| namespace | `ff_user_token` | 通过 | user 共享空间命中正确 |
| namespace | `ff_agent_token` | 通过 | agent 共享空间命中正确 |
| namespace | `tf_user_token` | 通过 | user 按 agent 隔离命中正确 |
| namespace | `tf_agent_token` | 通过 | agent 共享空间命中正确 |
| namespace | `tt_user_token` | 通过 | user/agent 双隔离命中正确 |
| namespace | `tt_agent_token` | 通过 | agent/user 双隔离命中正确 |
| `trusted` | `trusted_without_key` | 通过 | 无 key trusted 路径正常 |
| `trusted` | `trusted_with_key` | 通过 | 带 key trusted 路径正常 |
| `trusted` | `trusted_root_key_required` | 通过 | 不带 key 被服务侧拒绝 |
| `trusted` | `trusted_root_key_optional_ok` | 通过 | 带 root key 正常 |
| `senderId -> role_id` | `senderid_trusted_user_msg` | 通过 | `telegram:12345 -> telegram_12345` |
| `senderId -> role_id` | `senderid_trusted_blank` | 通过 | `role_id:null` |
| `senderId -> role_id` | `senderid_sanitize_symbols` | 通过 | `wx/user-01@abc -> wx_user-01_abc` |
| 真实机器人 | 私聊 sender 映射 | 通过 | sender 稳定映射到真实 `role_id` |
| 真实机器人 | 群聊多成员 `role_id` | 通过 | 不同成员落不同 `role_id` |
| `memory_store` | `requesterSenderId -> role_id` | 通过 | `memory_store` 已使用 `requesterSenderId` 写出真实 `role_id` |

### 4.2 已知说明

| 项目 | 状态 | 说明 |
|---|---|---|
| `agent_token_main_default` | 已打通 | 曾出现一次回答少 `_19950`，属于回答精度问题，不是链路问题 |
| 真实机器人最终回复 | 非插件问题 | 群聊/私聊中出现的 `Something went wrong...` 已定位在 OpenClaw 工具结果回填阶段，与 OpenViking 插件链路无关 |

## 5. 关键结论

1. 插件侧 canonical namespace policy、显式租户 header 与 `role_id` 透传能力已完成联调验证。
2. `afterTurn` 路径可通过 `runtimeContext.senderId` 正确写出 `role_id`。
3. `memory_store` 路径在工具上下文中无法直接读取 `senderId`，但可稳定通过 `requesterSenderId` 写出 `role_id`。
4. 在真实机器人私聊与群聊场景中，已确认不同用户会被映射为不同的 `role_id`，且 assistant message 保持 `role_id:null`。
5. 当前 OpenViking 插件链路已满足多租户、命名空间与 sender 身份透传的测试目标。

## 6. 建议

1. 若后续服务端对 `api_key + USER` 的 `role_id` 语义收紧，应补充一条显式兼容说明或服务端协同约束。
2. 如需继续扩大矩阵，可追加：
   - 更多账号/用户组合
   - 不同群聊成员的稳定性回归
   - `memory_store` 与 `afterTurn` 跨会话一致性复测
3. 若要沉淀为长期回归用例，建议将当前已验证的矩阵继续脚本化，但保留真实机器人场景作为最终回归验证补充。
