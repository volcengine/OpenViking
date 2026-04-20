# OpenClaw 插件 `agent_scope_mode` 测试报告

## 测试目的

本轮测试的目标是验证 OpenClaw 的 OpenViking 插件在以下两种模式下是否符合预期：

- `agent_scope_mode=user+agent`
- `agent_scope_mode=agent`

重点关注以下问题：

- 插件是否能够正确把逻辑 alias 展开为 OpenViking 可接受的显式 URI
- OpenViking 服务端的路由和权限行为是否与插件展开逻辑一致
- 端到端场景下，用户记忆与 agent 记忆是否能够正确召回
- 在 `agent` 模式下，是否实现了“同账号共享、跨账号隔离”的 agent memory 语义
- API key 模式下，插件是否不再发送错误的租户头

## 测试方案设计

本轮测试分为两层进行验证。

### 1. 服务侧验证

直接使用 `curl` 访问 OpenViking 服务 `127.0.0.1:19880`，绕开 OpenClaw，优先验证以下内容：

- seed 是否被写入正确目录
- 语义摘要与向量索引是否成功生成
- 显式 URI 的检索行为是否正确
- 错误用户、错误 agent、错误账号等负向场景是否被正确拒绝

这一层的目的是先确认服务端行为本身正确，再进入 OpenClaw 端到端链路。

### 2. 端到端验证

通过 `openclaw agent` 触发 OpenClaw 插件的真实 recall 流程，验证：

- 插件是否把 `viking://user/memories` 展开为 `viking://user/<user_id>/memories`
- 插件是否把 `viking://agent/memories` 展开为正确的显式 agent URI
- API key 模式下插件请求是否不再带 `X-OpenViking-Account/User`
- 最终回答是否命中预期 token

端到端验证分别覆盖：

- `user+agent` 模式
- `agent` 模式

在 `agent` 模式中，测试按用户分组执行：

- 同一用户组内不重启 OpenClaw gateway
- 仅在切换 API key（即切换用户）时重启一次 gateway

这样可以减少无关变量，避免因频繁重启造成干扰。

## Seed 设计与调整

### 初始 seed 的问题

最早使用的是较薄的 seed，通常只有一行 token。  
这种写法存在几个问题：

- 叶子文件容易被 `.overview` 或 `.abstract` 压过
- 摘要内容过于泛化
- 模型最终回答时不容易稳定复制精确 token

### 最终采用的结构化 seed

最终稳定通过的测试使用了结构化 seed，内容包括：

- 标题
- 类型
- `Canonical token`
- 当被问到唯一代号时应如何精确回答的明确说明

这种写法显著提升了：

- 叶子内容的排序稳定性
- 摘要质量
- 模型端到端回答的稳定性

## 测试注意点

### 1. 服务端 direct API 不会自动展开 alias

以下逻辑 URI 直接打服务端时不会自动展开：

- `viking://user/memories`
- `viking://agent/memories`

所以服务侧验证必须使用显式 URI，例如：

- `viking://user/<user_id>/memories`
- `viking://agent/<space_hash>/memories`

逻辑 alias 的展开属于插件侧行为，不是 OpenViking 服务端的通用能力。

### 2. API key 模式下不应发送租户头

在 API key 模式下，请求身份应由服务端根据 API key 自行解析。  
因此插件不应主动发送：

- `X-OpenViking-Account`
- `X-OpenViking-User`

本轮已验证该修复在运行时生效。

### 3. `agent_scope_mode=agent` 必须重注 agent seed

从 `user+agent` 切换到 `agent` 后，agent space 的 hash 规则由：

- `md5(user_id:agent_id)`

变为：

- `md5(agent_id)`

因此原先按“用户 + agent”写入的 agent seed 不能复用。  
user seed 可以保留，但 agent seed 必须重新按共享空间写入。

### 4. 使用 root key 重写共享 agent seed 时必须带正确的 agent header

在重写共享 agent seed 时，如果使用 root key 且未带正确的 `X-OpenViking-Agent`，则语义与向量 owner space 可能会被错误记录为 `agent_id=default`，造成路径与索引空间不一致。

因此在 `agent` 模式下写入共享 seed 时，必须显式带上对应 agent：

- `work2`
- `work3`
- `fresh3`

### 5. `openclaw agent` 的结果解析方式

当前环境下，`openclaw agent` 的输出不能简单假设为稳定 JSON 结构。  
更稳妥的做法是：

- 直接运行 `openclaw agent`
- 过滤插件日志行
- 将最后一条纯回答文本作为最终答案

这样可以避免把插件调试日志错误解析为模型最终输出。

### 6. `serverAuthMode` 的最终规则

插件最终支持两种服务端鉴权模式：

- `serverAuthMode=api_key`
- `serverAuthMode=trusted`

对应行为如下：

#### `serverAuthMode=api_key`

1. 当 `apiKey` 非空时
- 发送 `X-API-Key`
- 不发送 `X-OpenViking-Account`
- 不发送 `X-OpenViking-User`
- 身份由服务端根据 API key 解析

2. 当 `apiKey` 为空时
- 不发送 `X-API-Key`
- 发送：
  - `X-OpenViking-Account: default`
  - `X-OpenViking-User: default`
- 进入开发态默认身份 `default/default/default`

#### `serverAuthMode=trusted`

1. 始终发送：
- `X-OpenViking-Account`
- `X-OpenViking-User`

2. 当配置了 `apiKey` 时
- 额外发送 `X-API-Key`

3. 当未配置 `accountId/userId` 时
- 回退到：
  - `default/default`

#### 与服务端校验的一致性说明

插件当前规则与服务端 `openviking/server/auth.py` 的主要分支保持一致，但有两个前提需要明确：

1. `api_key` 模式下，如果服务端启用了 key manager，则缺少 API key 的请求仍可能被服务端拒绝；只有在开发态（无 manager）时，`default/default/default` 回退才会真正生效。
2. `trusted` 模式下，如果服务端配置了 `root_api_key`，则服务端仍可能要求同时提供 API key。插件已支持在 `trusted` 模式下可选发送 `apiKey`，以适配这一场景。

## 测试结果

## 一、`agent_scope_mode=user+agent`

### 服务侧结论

服务侧显式 URI 验证通过：

- user memory 显式 URI 检索正常
- agent memory 显式 URI 检索正常
- 跨用户和跨账号的错误访问均被拒绝

### 端到端结论

在改用结构化 seed 后，端到端矩阵通过。  
运行时已确认：

- `viking://user/memories` 能正确展开为显式 user URI
- `viking://agent/memories` 能正确展开为按 `user_id + agent_id` 计算出的 hash space
- API key 模式下不再发送租户头

### `agent_scope_mode=user+agent` 测试矩阵

| 用户 | Agent | 测试项 | 预期结果 | 实际结果 | 是否通过 |
|---|---|---|---|---|---|
| Alice | `work2` | personal | `ALICEUA19880` | `ALICEUA19880` | ✅ |
| Alice | `work2` | agent token | `ALICE_WORK2_19880` | `ALICE_WORK2_19880` | ✅ |
| Alice | `work3` | agent token | `ALICE_WORK3_19880` | `ALICE_WORK3_19880` | ✅ |
| Alice | `fresh3` | agent token | `ALICE_FRESH3_19880` | `ALICE_FRESH3_19880` | ✅ |
| Bob | `work2` | personal | `BOBUA19880` | `BOBUA19880` | ✅ |
| Bob | `work2` | agent token | `BOB_WORK2_19880` | `BOB_WORK2_19880` | ✅ |
| Bob | `work3` | agent token | `BOB_WORK3_19880` | `BOB_WORK3_19880` | ✅ |
| Bob | `fresh3` | agent token | `BOB_FRESH3_19880` | `BOB_FRESH3_19880` | ✅ |
| Carol | `work2` | personal | `CAROLUA19880` | `CAROLUA19880` | ✅ |
| Carol | `work2` | agent token | `CAROL_WORK2_19880` | `CAROL_WORK2_19880` | ✅ |
| Carol | `work3` | agent token | `CAROL_WORK3_19880` | `CAROL_WORK3_19880` | ✅ |
| Carol | `fresh3` | agent token | `CAROL_FRESH3_19880` | `CAROL_FRESH3_19880` | ✅ |

## 二、`agent_scope_mode=agent`

### 服务侧结论

在以下动作完成后，服务侧验证通过：

- 将 OpenViking 服务配置切到 `memory.agent_scope_mode = agent`
- 按共享 hash space 重写 agent seed
- 使用正确的 agent 身份重新生成语义和向量索引

本轮使用的共享 hash space 为：

- `work2 -> 1e62745e8d86`
- `work3 -> 47b019431c60`
- `fresh3 -> 90c732fde37a`

### 端到端结论

端到端验证通过。

对于账号 `acctx`：

- Alice 与 Bob 都能命中同一份共享 agent token

对于账号 `accty`：

- Carol 命中的是 `accty` 自己的共享 agent token

这说明：

- 同账号共享行为符合预期
- 跨账号隔离行为符合预期

## `agent_scope_mode=agent` 端到端结果

### Alice（`acctx`）

- `alice_personal -> ALICEUA19880`
- `alice_work2 -> ACCTX_WORK2_SHARED_19880`
- `alice_work3 -> ACCTX_WORK3_SHARED_19880`
- `alice_fresh3 -> ACCTX_FRESH3_SHARED_19880`

### Bob（`acctx`）

- `bob_personal -> BOBUA19880`
- `bob_work2 -> ACCTX_WORK2_SHARED_19880`
- `bob_work3 -> ACCTX_WORK3_SHARED_19880`
- `bob_fresh3 -> ACCTX_FRESH3_SHARED_19880`

### Carol（`accty`）

- `carol_personal -> CAROLUA19880`
- `carol_work2 -> ACCTY_WORK2_SHARED_19880`
- `carol_work3 -> ACCTY_WORK3_SHARED_19880`
- `carol_fresh3 -> ACCTY_FRESH3_SHARED_19880`

### `agent_scope_mode=agent` 测试矩阵

| 用户 | 账号 | Agent | 测试项 | 预期结果 | 实际结果 | 是否通过 |
|---|---|---|---|---|---|---|
| Alice | `acctx` | `work2` | personal | `ALICEUA19880` | `ALICEUA19880` | ✅ |
| Alice | `acctx` | `work2` | shared agent token | `ACCTX_WORK2_SHARED_19880` | `ACCTX_WORK2_SHARED_19880` | ✅ |
| Alice | `acctx` | `work3` | shared agent token | `ACCTX_WORK3_SHARED_19880` | `ACCTX_WORK3_SHARED_19880` | ✅ |
| Alice | `acctx` | `fresh3` | shared agent token | `ACCTX_FRESH3_SHARED_19880` | `ACCTX_FRESH3_SHARED_19880` | ✅ |
| Bob | `acctx` | `work2` | personal | `BOBUA19880` | `BOBUA19880` | ✅ |
| Bob | `acctx` | `work2` | shared agent token | `ACCTX_WORK2_SHARED_19880` | `ACCTX_WORK2_SHARED_19880` | ✅ |
| Bob | `acctx` | `work3` | shared agent token | `ACCTX_WORK3_SHARED_19880` | `ACCTX_WORK3_SHARED_19880` | ✅ |
| Bob | `acctx` | `fresh3` | shared agent token | `ACCTX_FRESH3_SHARED_19880` | `ACCTX_FRESH3_SHARED_19880` | ✅ |
| Carol | `accty` | `work2` | personal | `CAROLUA19880` | `CAROLUA19880` | ✅ |
| Carol | `accty` | `work2` | shared agent token | `ACCTY_WORK2_SHARED_19880` | `ACCTY_WORK2_SHARED_19880` | ✅ |
| Carol | `accty` | `work3` | shared agent token | `ACCTY_WORK3_SHARED_19880` | `ACCTY_WORK3_SHARED_19880` | ✅ |
| Carol | `accty` | `fresh3` | shared agent token | `ACCTY_FRESH3_SHARED_19880` | `ACCTY_FRESH3_SHARED_19880` | ✅ |

## 总体结论

本轮测试确认了以下几点：

1. 插件的逻辑 alias 展开逻辑正确。
2. API key 模式下，插件不再发送错误的租户头。
3. OpenViking 服务端在以下两种模式下都符合预期：
   - `agent_scope_mode=user+agent`
   - `agent_scope_mode=agent`
4. 通过 OpenClaw 的端到端 recall 链路在两种模式下均已跑通。
5. 在 `agent_scope_mode=agent` 下，已验证：
   - 同账号共享 agent memory
   - 跨账号隔离 agent memory
