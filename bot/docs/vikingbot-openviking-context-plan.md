# VikingBot 接入 OpenViking 会话压缩改造方案

## 结论

该方案可行，但不能直接沿用当前 VikingBot 的 OpenViking 接入方式。

当前 OpenViking 服务端已经具备以下关键能力：

- 稳定 session 的消息追加
- `pending_tokens` 累积
- `commit(keep_recent_count=...)`
- `get_session_context()` 返回 `latest_archive_overview` 与 live messages

真正需要改造的是 VikingBot 侧的写路径、读路径、配置与旧压缩链路的退场策略。

## 目标

把 VikingBot 的长对话压缩链路改为：

1. 同一个 bot session 持续写入同一个 OpenViking session。
2. 每轮仅增量同步本轮新增消息，不重复全量重传。
3. 当 OpenViking session 的 `pending_tokens` 达到阈值时触发 `commit`。
4. 下一轮模型调用前，从 OpenViking 读取已压缩上下文。
5. 本地 session 在未压缩前保留原始消息日志；OpenViking commit 成功后清空本地 JSONL，由 OpenViking 负责长上下文压缩与回放。

## 非目标

第一版不做以下事情：

- 不让 OpenViking 完全替代本地 session 存储。
- 不把全部 tool trace / reasoning 直接纳入 OpenViking 压缩主链路。
- 不依赖 memory extraction 完成后才能继续下一轮对话。
- 不同时保留两套自动压缩主链路并行工作。

## 当前实现与方案的关键差异

### 1. 当前 `ov_server.py` 不是稳定 session 模式

当前 `bot/vikingbot/openviking_mount/ov_server.py` 的 `commit(...)` 会在每次提交时重新创建 session，再把整段消息写入并立刻 commit。

这与目标方案冲突，因为它会导致：

- `pending_tokens` 无法持续累积
- 下一轮无法从同一个 session 取回压缩结果
- 无法做真正的增量同步

因此，这个文件必须从“一次性提交器”改造成“稳定 session 访问层”。

### 2. 当前 `loop.py` 仍以本地 history 为主

当前 `bot/vikingbot/agent/loop.py` 仍然使用：

- `session.get_history(...)` 作为模型 history
- `len(session.messages) > self.memory_window` 作为本地自动压缩触发条件

这意味着现有主链路仍是“本地 session 驱动”，而不是“OpenViking session 驱动”。

### 3. 当前 `context.py` 只会拼本地 history

当前 `bot/vikingbot/agent/context.py` 的 prompt 组装仍是：

1. system prompt
2. 本地 history
3. memory/context 注入
4. 当前 user message

如果要接入 OpenViking 压缩上下文，必须显式扩展 prompt assembly。

### 4. 现有旧 compact hook 会与新链路冲突

当前 `bot/vikingbot/hooks/builtins/openviking_hooks.py` 里仍有旧的 `message.compact` 逻辑：

- 有的模式下会把 session 拆成 admin session + per-user session
- 这与新方案的“一个房间对应一个稳定 OV session”冲突

因此新链路启用后，旧 compact hook 必须被禁用、绕过或显式降级为非主路径。

## 设计原则

### 1. OpenViking 负责长上下文压缩，本地 session 负责压缩前原始日志

第一版不建议让 OpenViking 直接替代本地 session 的短期落盘能力。

推荐职责划分：

- 本地 session：在压缩前保存原始消息，兼容现有 provider-specific 字段
- OpenViking session：保存用于长对话压缩和回放的核心消息链路

达到 token/window 阈值并成功 commit 后，本地 session JSONL 会被清空，下一轮通过 OpenViking context 回放已压缩历史。

### 2. OpenViking session 必须稳定

一个 `SessionKey.safe_name()` 对应一个稳定的 `ov_session_id`。

第一版建议直接使用：

- `ov_session_id = SessionKey.safe_name()`

不再在每次 commit 时重新 `create_session()`。

### 3. OpenViking 读路径优先，本地只补 unsynced delta

OpenViking 的 `get_session_context()` 返回的不是纯摘要，而是：

- `latest_archive_overview`
- pending archive messages
- 当前 live messages

因此，VikingBot 读路径不能再把本地 history 整段拼进去。

正确规则应为：

- 以 OpenViking 返回内容作为主 history
- 本地仅补“尚未成功写入 OpenViking 的尾部 delta”

否则会产生重复上下文。

### 4. 请求身份与消息说话人分离

群聊场景中要区分两层身份：

- OpenViking request identity：谁在发起这次 API 调用
- message speaker identity：这条消息是谁说的

第一版建议：

- 请求继续使用当前合法的 bot/account/user 身份
- 每条消息的真实说话人通过 `peer_id` 记录

不要把“当前 `sender_id`”直接等同于每次请求的 OpenViking user 身份，否则会和现有权限/命名空间语义冲突。

## 核心方案

## 1. 稳定的 OpenViking Session 绑定

在本地 session metadata 中维护：

```json
{
  "openviking": {
    "enabled": true,
    "session_id": "<SessionKey.safe_name()>",
    "last_synced_local_index": 0,
    "last_commit_at": null,
    "last_pending_tokens": 0,
    "last_context_read_at": null,
    "last_sync_status": "idle"
  }
}
```

字段说明：

- `session_id`: 稳定的 OpenViking session id
- `last_synced_local_index`: 已成功同步到 OpenViking 的本地消息下标上界
- `last_commit_at`: 最近一次 commit 时间
- `last_pending_tokens`: 最近一次观测到的 `pending_tokens`
- `last_context_read_at`: 最近一次读 context 时间
- `last_sync_status`: `idle` / `syncing` / `error`

其中最关键的是 `last_synced_local_index`，它决定增量同步与去重是否正确。

## 2. 写路径：每轮结束后增量同步到 OpenViking

写路径触发点位于 `bot/vikingbot/agent/loop.py` 当前一轮完成、本地 `session.add_message(...)` + `save(...)` 之后。

### 同步内容

第一版只同步核心对话消息：

- user message
- assistant final content

第一版不建议写入：

- 全量 tool trace
- `reasoning_content`
- 大体积工具输出

原因：

- 这些内容会显著放大压缩噪声
- 当前 provider replay 仍主要依赖本地 session
- 第一版目标是先打通稳定会话压缩闭环，而不是完整镜像全部调试信息

### 同步流程

```text
1. 读取 session.metadata.openviking
2. 若不存在 session_id，则创建/确保稳定 session
3. 根据 last_synced_local_index 找出新增消息
4. 将新增消息转换为 OV message parts
5. 调用 append_messages / batch_add_messages 写入 OV
6. 读取 session meta，获取 pending_tokens
7. 若 pending_tokens >= commit_token_threshold 或消息数达到 memory_window，则触发 commit_session(keep_recent_count=N)
8. 更新本地 metadata 中的同步游标与快照
9. 如果本次实际执行了 commit，则清空本地 session JSONL，并重置本地同步游标但保留稳定 session_id
```

### 写路径要求

- 只在“成功写入 OV”后推进 `last_synced_local_index`
- commit 失败不应回滚已成功写入的消息游标
- commit 成功代表本轮压缩完成，应清空本地 session JSONL；后续 prompt 由 OpenViking context + 新的本地未同步 tail 组成
- commit 与 extract 异步执行时，下一轮仍可继续读取 session context

## 3. 读路径：模型调用前优先从 OpenViking 组装 history

读路径触发点位于 `bot/vikingbot/agent/loop.py` 当前构建 `messages = await message_context.build_messages(...)` 之前。

### 读取流程

```text
1. 读取本地 metadata.openviking
2. 若未启用或尚未建立稳定 session，则退化为本地 history 模式
3. 调用 get_session_context(session_id, token_budget)
4. 取回 latest_archive_overview + messages
5. 根据 last_synced_local_index，仅补本地未同步 delta
6. 将组装后的 history 传给 ContextBuilder.build_messages(...)
```

### 推荐上下文顺序

1. system prompt
2. OpenViking `latest_archive_overview`
3. OpenViking `messages`
4. 本地 unsynced delta history
5. 当前 user message

### 去重规则

这是第一版最关键的边界条件：

- 不能再把本地 `session.get_history(...)` 全量拼到 OpenViking context 后面
- 本地只允许补尚未成功同步到 OpenViking 的消息
- 一旦消息已确认 append 成功，就不应再出现在 local delta 中

否则很容易出现重复轮次，导致模型重复理解、工具误触发或 token 浪费。

## 4. token budget 的使用方式

`session_context_token_budget` 不能被视为最终 prompt 的硬上限。

第一版应采用两层预算：

### 第一层：OpenViking context budget

用于控制调用 `get_session_context(session_id, token_budget=...)` 的预算目标。

### 第二层：VikingBot 最终 prompt trim

在组装出：

- OV overview
- OV messages
- local unsynced delta
- current user message

之后，仍需在 VikingBot 侧做一次最终裁剪，确保不会超过 provider 的上下文限制。

否则在大群聊或消息内容较长时，仍可能超出模型窗口。

## 5. 群聊语义

群聊推荐语义如下：

- 一个聊天房间对应一个稳定 `ov_session_id`
- OpenViking request identity 继续按当前 bot/account 配置走
- 每条 user/assistant message 的实际说话者写入 `peer_id`

建议映射：

- user message: `role="user"`, `peer_id=<真实 sender_id>`
- assistant message: `role="assistant"`, `peer_id=<bot/agent id 或默认 assistant 标识>`

这样可以同时满足：

- 会话不被拆碎
- 群聊参与者身份可保留
- 不破坏当前 OpenViking 的身份回退逻辑

## 需要改动的文件

### 1. `bot/vikingbot/openviking_mount/ov_server.py`

把现有一次性 `commit(...)` 改造成稳定 session 访问层。

建议新增或重构为以下接口：

- `ensure_session(session_id: str) -> dict`
- `append_messages(session_id: str, messages: list[dict], peer_id_resolver=...) -> dict`
- `get_session(session_id: str) -> dict`
- `get_session_context(session_id: str, token_budget: int) -> dict`
- `commit_session(session_id: str, keep_recent_count: int = 0) -> dict`

要求：

- 不再每次重新创建 session
- 允许批量追加消息
- 能返回最新 `pending_tokens`
- commit 时能传 `keep_recent_count`

### 2. `bot/vikingbot/agent/loop.py`

需要新增两段逻辑：

- 模型调用前：读取 OV context 并构造 history
- 一轮结束后：把本轮新增消息增量同步到 OV，并按阈值决定是否 commit

同时需要关闭或门控当前本地自动 compact 主链路：

- 当 `session_context_enabled=true` 时，不再使用 `len(session.messages) > self.memory_window` 触发旧压缩主链路
- `/compact` 可保留为显式命令，但行为要重新定义，避免与新链路重复

### 3. `bot/vikingbot/agent/context.py`

需要让 `build_messages(...)` 支持接收“外部已组装好的 history”。

推荐方式：

- `loop.py` 先准备好 `history`
- `context.py` 只负责拼：system prompt、memory、当前 user message

这样可以避免把 OpenViking 逻辑硬塞进 `ContextBuilder` 内部。

### 4. `bot/vikingbot/session/manager.py`

当前 metadata merge 已支持嵌套字典，可直接用于持久化：

- `metadata["openviking"][...]`

这里只需要补充新字段的读写约定，不需要重新设计存储格式。

### 5. `bot/vikingbot/config/schema.py`

在 `AgentsConfig` 中增加配置项：

- `session_context_enabled: bool = False`
- `session_context_token_budget: int = 12000`
- `commit_token_threshold: int = 6000`
- `commit_keep_recent_count: int = 10`

其中：

- `session_context_enabled`：总开关
- `session_context_token_budget`：OV context 读取预算
- `commit_token_threshold`：触发 commit 的阈值
- `commit_keep_recent_count`：commit 后保留的 recent live messages 数量

## OpenViking Python Client 需要补的能力

虽然 OpenViking 服务端已支持 `keep_recent_count`，但当前 Python client wrapper 还没有把这个参数完整透出给 VikingBot。

需要修改：

- `openviking/async_client.py`
- `openviking/client/session.py`

建议补齐以下调用能力：

- `commit_session(session_id, keep_recent_count=0, telemetry=False)`
- `Session.commit(keep_recent_count=0, telemetry=False)`
- `Session.commit_async(keep_recent_count=0, telemetry=False)`

否则 VikingBot 在 `AgentsConfig` 配置了 `commit_keep_recent_count` 也无法真正生效。

## 与旧链路的共存策略

### 必须处理的冲突点

以下旧链路会与新方案冲突：

- `bot/vikingbot/hooks/builtins/openviking_hooks.py`
- `bot/vikingbot/agent/loop.py` 中基于 `memory_window` 的自动 compact
- 任何仍按“全量 session -> 一次性 commit”的旧调用点

### 推荐策略

当 `session_context_enabled=true` 时：

1. 禁用旧的 `message.compact` OpenViking hook 主路径
2. 禁用 `len(session.messages) > self.memory_window` 的旧自动压缩
3. 保留 `/compact` 作为显式运维命令，但它应调用新的 stable-session commit 逻辑，而不是旧 fanout 逻辑

## 分阶段实施

## 第一阶段：打通最小闭环

目标：不动 provider 行为的前提下，让 OpenViking 真正接管长对话压缩。

实施项：

1. 重构 `ov_server.py` 为稳定 session 访问层
2. 在 `loop.py` 中接入 after-turn 增量同步
3. 在 `loop.py` 中接入 before-call OV context 读取
4. 在本地 metadata 中保存 `openviking` 同步状态
5. 启用去重规则：本地仅补 unsynced delta

验收标准：

- 同一 session 多轮对话使用同一个 `ov_session_id`
- `pending_tokens` 可持续增长
- 达到阈值后能成功 commit
- 下一轮能读到 `latest_archive_overview` 和 live messages
- prompt 中无重复历史片段

## 第二阶段：补齐 keep_recent_count 与预算控制

实施项：

1. 修改 OpenViking Python client wrapper
2. 让 `commit_keep_recent_count` 配置生效
3. 在 VikingBot 侧增加最终 prompt trim

验收标准：

- commit 后最近 N 条消息仍保留在 live session 中
- 长会话下 prompt 大小可控
- 不因 context 过长导致 provider 调用失败

## 第三阶段：清理旧链路

实施项：

1. 门控旧 `message.compact` hook
2. 门控旧 `memory_window` 自动 compact
3. 明确 `/compact` 与新方案的关系
4. 检查其他工具或工厂函数是否仍依赖旧一次性 commit 逻辑

验收标准：

- 新旧链路不会同时对同一 session 生效
- 群聊不会被拆成多个 OV session
- 回归测试中 history 组装路径唯一且可解释

## 风险与注意事项

### 1. 重复上下文风险

如果本地 delta 计算不准确，最容易出现消息重复拼接。

这是第一版必须优先规避的问题。

### 2. provider-specific 字段丢失风险

当前本地 session 会保留部分 provider 特有字段，例如 `reasoning_content`。

因此第一版推荐保留“本地原始日志 + OV 压缩层”的双层职责，而不是直接完全切换到 OV history。

### 3. 群聊身份映射风险

如果直接把 `sender_id` 当作每次 OV 请求 user 身份，容易与当前 account/user/peer 权限语义冲突。

应优先通过 `peer_id` 保留真实说话人。

### 4. 提取异步性的认知风险

commit 之后的 memory extraction 是后台过程。

下一轮对话可依赖 session context，但不要把“新 memory 必然已可检索”当成同步保证。

## 一句话总结

把 VikingBot 改成“本地原始日志 + OpenViking 长上下文压缩层”的双层结构：每轮增量写入稳定 OV session，达到 `pending_tokens` 阈值就 commit，下一轮优先读取 `latest_archive_overview + live messages`，本地只补尚未同步的 delta，并在启用新链路后退场旧的 compact/fanout 逻辑。
