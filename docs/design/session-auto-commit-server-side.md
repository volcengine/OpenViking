# Session Auto Commit 服务端自动触发详细方案

## 1. 背景

当前 session commit 的自动触发能力主要分散在客户端或插件侧，不同接入方的行为模型并不一致。

两个典型例子：

- `openclaw-plugin`
  - 事件驱动
  - turn-based
  - 更偏向在对话推进过程中基于 token threshold 触发
- `opencode-memory-plugin`
  - 时间驱动
  - scheduler-based
  - 更偏向在后台按时间窗口做自动提交

这种现状带来几个问题：

- 自动触发逻辑分散，服务端没有统一真相源
- 不同客户端的触发语义不一致
- 客户端退出、重启、断连后，自动触发状态容易丢
- 多端同时操作同一个 session 时，行为不可控
- 服务端难以统一治理 token/time 两类触发策略

因此，需要把 session auto commit 能力收回到服务端。

## 2. 目标

本方案的目标是：

- 让服务端统一支持两类自动 commit 触发
  - 基于 token threshold
  - 基于 idle timeout
- 把 `commit_policy` 持久化到 session meta
- 让重启后自动触发能力能够恢复
- 让内部调度状态与用户资源目录解耦
- v1 先用简单、清晰、可控的方式落地

## 3. 非目标

v1 明确不做下面这些事：

- 不做复杂的分布式 lease 协议
- 不持久化 runtime `in_flight task` 状态
- 不给 token-only session 建额外调度索引
- 不做多文件索引分片
- 不在 v1 引入独立的 `update commit_policy` 专用 API

## 4. 设计原则

### 4.1 配置真相源和调度状态分离

`commit_policy` 应该保存在 session meta 中，因为它是业务配置真相源。

索引文件不保存“完整配置”，只保存“当前需要被 idle scheduler 跟踪的 session”。

也就是说：

- session meta 负责表达“这个 session 想要什么行为”
- 索引文件负责表达“当前有哪些 session 需要后台调度器关注”

### 4.2 token 和 idle 走不同路径

这两类触发机制本质不同：

- token threshold 是消息写入后的即时判断
- idle timeout 是时间流逝后的后台判断

因此不应强行走同一个调度模型。

### 4.3 索引只保存活跃待跟踪对象

索引不应该保存所有历史上开启过 auto commit 的 session。

它只应保存：

- 已开启 auto commit
- 且配置了 idle timeout
- 且当前确实还存在未提交内容
- 且服务端 idle 自动触发功能处于开启状态

这样可以避免索引文件无限膨胀。

### 4.4 v1 优先简单可解释

在功能边界明确的前提下，优先做：

- 单文件
- 明确的写入/删除时机
- 明确的故障恢复语义
- 最小去重

而不是一开始就追求复杂分片或强一致机制。

## 5. 现状梳理

### 5.1 openclaw-plugin 模式

`openclaw-plugin` 当前更像：

- 事件驱动
- 每个 turn 结束时检查是否需要 commit
- 更偏向基于 token 大小或 turn 边界做触发

这个模型的特点是：

- 及时
- 与交互链路绑定紧
- 但客户端必须在线
- 服务端不掌握完整触发状态

### 5.2 opencode-memory-plugin 模式

`opencode-memory-plugin` 当前更像：

- 定时调度
- 后台周期检查
- 更偏向基于时间窗口决定是否 commit

这个模型的特点是：

- 适合 idle 场景
- 客户端在线与否影响较小
- 但逻辑仍不在服务端统一收口

### 5.3 收敛方向

服务端应统一支持：

- token trigger：消息到达时即时判定
- idle trigger：后台调度器判定

这样既能保留两类行为的优势，又能统一治理。

## 6. Session Meta 设计

`commit_policy` 放入 session `.meta.json`。

建议字段如下：

```json
{
  "commit_policy": {
    "enabled": true,
    "token_threshold": 8000,
    "idle_timeout_seconds": 1800,
    "keep_recent_count": 10
  },
  "last_message_at": "2026-06-20T10:00:00+08:00",
  "auto_commit_last_error": "",
  "auto_commit_last_error_at": ""
}
```

说明：

- `commit_policy`
  - session 级别自动 commit 配置
  - 是配置真相源
- `last_message_at`
  - 最近一次消息进入 session 的时间
  - idle timeout 计算依赖它
- `auto_commit_last_error`
  - 最近一次自动 commit 失败原因
- `auto_commit_last_error_at`
  - 最近一次失败时间

### 6.1 为什么不持久化 runtime in-flight 状态

不建议在 meta 中写：

- `auto_commit_pending`
- `auto_commit_in_flight_task_id`

原因是：

- 这些状态是 runtime 瞬时状态，不是业务配置
- 进程崩溃后，这些状态很容易与真实 task 生命周期脱节
- 恢复逻辑会显著复杂化
- 而且现有 commit 本身已经有锁和 task 跟踪能力

因此 v1 应该依赖：

- `Session.commit_async()` 自身的锁/no-op 语义
- `TaskTracker.has_running(...)`
- 进程内 claim set

做最小去重即可。

## 7. 两类触发机制设计

### 7.1 token threshold 触发

这类触发不需要进索引。

原因：

- token threshold 的判断只在消息写入时才有意义
- 没有消息进入时，token 数不会自然增长
- 因此不需要后台 scheduler 反复扫描

处理方式：

1. `add_message` / `batch_add_message`
2. 更新 session meta
3. 判断 `pending_tokens >= token_threshold`
4. 满足则触发自动 commit

### 7.2 idle timeout 触发

这类触发需要进索引。

原因：

- idle timeout 本质是“时间过去了多久”
- 即使没有新消息，也需要服务端在未来某个时间点主动判断
- 因此需要后台 scheduler

处理方式：

1. 新消息写入后更新 `last_message_at`
2. 根据 policy 计算 `next_check_at`
3. 将 session 写入 idle 索引
4. scheduler 周期扫描，到期时触发自动 commit

## 8. 服务端全局开关

给 idle 自动触发加一个服务端全局配置：

```json
{
  "server": {
    "session_auto_commit": {
      "idle_enabled": true
    }
  }
}
```

约束如下：

- 默认开启
- 只影响 idle timeout 触发
- 不影响 token threshold 即时触发

这样做的好处是：

- 出问题时可服务端快速兜底关闭 idle 调度
- 不用改每个 session 的 policy

## 9. 索引文件设计

## 9.1 为什么需要索引

如果没有索引，启动后要实现 idle 触发，理论上可以每轮扫描全部 session。

但这有几个问题：

- session 数量大时开销高
- 大部分 session 根本没开启 idle 自动触发
- 很多 session 没有未提交内容，扫描毫无意义

因此需要一个“活跃 idle 候选集”的索引层。

### 9.2 为什么 token-only session 不进索引

因为 token-only session 不需要时间驱动扫描。

只要没有新消息写入，就不会触发 token threshold 判断。

把它放进索引会带来纯粹的无效扫描，没有收益。

### 9.3 单文件 vs 多文件

v1 先选单文件。

原因：

- 简单
- 易于解释
- 易于调试
- 当前阶段比复杂分片更重要的是把触发语义跑通

后续如果规模增长，再考虑分片。

## 10. 索引存储位置

索引文件不应放在 `viking://resources/` 下。

原因：

- 它不是用户资源
- 它是内部控制面状态
- 放在 resources 下会污染资源视图
- 也容易和用户侧语义混淆

因此 v1 建议放在内部系统路径：

- `/local/_system/session_auto_commit/index.json`

配套文件：

- `/local/_system/session_auto_commit/index.json.tmp`
- `/local/_system/session_auto_commit/index.json.bak`

## 11. 索引文件结构

建议结构如下：

```json
{
  "meta": {
    "updated_at": "2026-06-20T10:30:00+08:00"
  },
  "data": {
    "acct_a": {
      "user_b": {
        "chat_123": {
          "next_check_at": "2026-06-20T10:30:00+08:00"
        }
      }
    }
  }
}
```

### 11.1 为什么不写扁平 key

比如这种：

```json
{
  "sessions": {
    "acct_a:user_b:chat_123": {
      "account_id": "acct_a",
      "user_id": "user_b",
      "session_id": "chat_123",
      "next_check_at": "2026-06-20T10:30:00+08:00"
    }
  }
}
```

问题在于：

- 信息冗余
- 解析时仍要拆回 account/user/session
- 难以按层级做清理

### 11.2 为什么不用 `_meta` 当固定 key

因为如果顶层本身还承载业务层级，`_meta` 这种保留名会引入命名冲突风险。

比如理论上有人账户名就可能叫 `_meta`。

所以更安全的做法是：

- 顶层固定两个字段
  - `meta`
  - `data`

业务数据全部放在 `data` 之下。

### 11.3 为什么 value 里不重复写 account/user/session

因为这些信息已经由层级路径表达了。

value 只需要保留调度必需字段即可，比如：

- `next_check_at`

这样能减少冗余。

## 12. `next_check_at` 的作用

`next_check_at` 是 idle 索引中的核心字段。

### 12.1 为什么它能减少无效扫描

如果索引只记录“有哪些 session 开启了 idle auto commit”，每一轮 scheduler 都得：

- 取出所有 session
- 再读取/计算它们是否到期

这会导致大量无效工作。

引入 `next_check_at` 后，scheduler 可以先做非常轻量的时间判断：

- `next_check_at <= now`

没到时间的 session 可以直接跳过。

因此它能显著减少无效扫描和无意义的进一步检查。

### 12.2 `next_check_at` 何时更新

它在以下场景更新：

1. 新消息写入后
   - `last_message_at` 变化
   - 因此 `next_check_at` 需要重算
2. policy 更新后
   - 如果 `idle_timeout_seconds` 变化
   - `next_check_at` 需要重算
3. 自动 commit 完成后
   - 如果仍需继续跟踪，则按新状态重算
   - 如果已不需要跟踪，则直接删索引

## 13. 索引项写入条件

一个 session 只有同时满足以下条件，才进入索引：

- `commit_policy.enabled == true`
- `idle_timeout_seconds` 有效
- 服务端 `idle_enabled == true`
- 当前存在未提交内容

写入时计算：

`next_check_at = last_message_at + idle_timeout_seconds`

## 14. 索引项删除条件

索引文件里只保留当前需要后台调度器关注的 session。

因此以下情况应删除索引项：

- policy 被关闭
- `idle_timeout_seconds` 被移除
- 服务端全局 `idle_enabled` 被关闭
- session 被删除
- commit 后已经没有未提交内容

### 14.1 为什么自动 commit 成功后可以直接删

当自动 commit 成功后，如果当前 session 已无未提交内容，就没必要继续保留索引。

虽然 “policy 还开着” 这件事仍然存在，但这个信息保留在 session meta 里。

当未来再次 `add_message` 时：

- 新消息进入
- 服务端重新读取 meta 中的 policy
- 重新计算并写回索引

这样做的好处是：

- 索引不会随着历史 session 无限膨胀
- 索引始终只表示“活跃待跟踪对象”

## 15. 持久化策略

### 15.1 何时持久化

v1 采用“每次变更立即持久化”。

也就是说：

- 任何 `upsert`
- 任何 `remove`

都立即刷盘。

### 15.2 为什么不用定时批量 flush

因为索引变更频率相对可控，而且我们更关心：

- 崩溃窗口尽可能小
- 恢复语义清晰

批量 flush 虽然能减少写次数，但会增加“内存状态已更新、磁盘状态未更新”的窗口，不适合 v1。

### 15.3 持久化写入方式

参考 watch resources 的方式，使用：

1. 写 `tmp`
2. 主文件轮转成 `bak`
3. `tmp` `mv` 成主文件

对应到本方案即：

- 写 `/local/_system/session_auto_commit/index.json.tmp`
- 主文件轮转到 `/local/_system/session_auto_commit/index.json.bak`
- `tmp` `mv` 成 `/local/_system/session_auto_commit/index.json`

### 15.4 为什么要这么做

这是为了降低主文件损坏的风险。

如果直接覆盖主文件：

- 写到一半崩溃
- 主文件可能处于半写状态
- 启动恢复时可能完全不可用

而 `tmp -> bak -> 主文件` 的流程可以让系统在大多数情况下至少保留：

- 一份最新成功写入的主文件
- 或一份上一次成功写入的 `bak`

## 16. 启动与重启恢复

### 16.1 启动正常流程

服务启动时：

1. 初始化 idle 索引对象
2. 加载主索引文件
3. 主索引不可用时回退到 `bak`
4. 启动 scheduler

### 16.2 为什么不能只依赖索引文件

因为存在这样的故障窗口：

1. 新消息写入
2. session meta 已更新
3. 但索引尚未写入
4. 进程崩溃

这时：

- meta 里有 policy
- 但索引里没有这个 session

如果重启后只读索引、不做补偿，那么这个 session 的 idle 自动触发就会丢。

### 16.3 补偿恢复原则

因此，重启恢复不能只依赖索引文件。

正确原则应是：

- session meta 是配置真相源
- 索引是调度加速层

所以启动时需要具备“一次性发现/重建”的能力，用 meta 补齐索引的缺口。

### 16.4 v1 恢复建议

v1 推荐做法：

1. 启动时先加载索引文件
2. 再做一次全量发现或受控发现
3. 对开启 idle 自动触发且有未提交内容的 session 重建索引

这样才能覆盖 crash window。

## 17. scheduler 设计

### 17.1 扫描范围

scheduler 不扫描全部 session。

它只扫描索引文件里登记的 session。

也就是说：

- token-only session 不在调度范围内
- 未开启 idle policy 的 session 不在调度范围内
- 已没有未提交内容、已从索引删除的 session 不在调度范围内

### 17.2 单轮逻辑

每轮 scheduler：

1. 读取索引中的候选项
2. 找出 `next_check_at <= now` 的项
3. 对这些项逐个加载 session meta 再次校验
   - policy 是否还开启
   - idle timeout 是否仍有效
   - 是否还有未提交内容
4. 满足则触发自动 commit
5. commit 后更新或删除索引

### 17.3 为什么需要二次校验

因为索引不是配置真相源，可能存在滞后。

例如：

- policy 已经关闭
- 但索引尚未来得及删

所以 scheduler 到期后不能直接 commit，必须再读 meta 做最终判断。

## 18. 去重与并发控制

自动 commit 存在重复触发风险，例如：

- token trigger 和 idle trigger 同时命中
- scheduler 多轮扫到同一个 session
- 进程内多个协程同时试图触发
- 重试与手动 commit 叠加

v1 使用三层最小去重：

1. `Session.commit_async()` 自身锁与 no-op 语义
2. `TaskTracker.has_running(...)`
3. 进程内 claim set

### 18.1 为什么不再额外持久化 task 状态

因为现有 commit 链路本身已经具备：

- 任务跟踪
- 锁保护
- no-op 防重

再单独持久化 `auto_commit_pending` 或 `in_flight_task_id`：

- 收益有限
- 恢复逻辑复杂
- 还会引入 stale 状态问题

v1 没必要。

## 19. 关键流程

### 19.1 add message

1. 写入 message
2. 更新 `last_message_at`
3. 如果请求带 `commit_policy`，则持久化到 session meta
4. 如果存在 idle policy，则重算 `next_check_at` 并更新索引
5. 如果存在 token threshold，则即时判断是否要自动 commit

### 19.2 token 自动触发

1. 在消息写入后检查 `pending_tokens`
2. 若达到阈值，则发起自动 commit
3. commit 完成后根据最新状态处理索引

### 19.3 idle 自动触发

1. scheduler 扫到到期项
2. 加载 meta 做二次校验
3. 满足条件则发起自动 commit
4. commit 完成后更新或删除索引

### 19.4 自动 commit 成功

1. 清理最近错误状态
2. 如果已无未提交内容，则删除索引项
3. 如果仍需继续跟踪，则重算并更新 `next_check_at`

### 19.5 自动 commit 失败

1. 记录 `auto_commit_last_error`
2. 记录 `auto_commit_last_error_at`
3. 保留未来再次重试的机会

## 20. API 语义

在消息写入接口上支持：

```json
{
  "commit_policy": {
    "enabled": true,
    "token_threshold": 8000,
    "idle_timeout_seconds": 1800,
    "keep_recent_count": 10
  }
}
```

这里的语义应明确：

- 这是 session 级别 policy
- 一旦设置，会持久化到 session meta
- 后续触发完全以服务端持久化配置为准

如果未来需要单独更新 policy，也可以再补一个专用 API，但 v1 不强依赖。

## 21. 风险点与取舍

### 21.1 全量发现成本

启动时做全量发现，在 session 特别多时会有成本。

这是单文件、meta 真相源方案天然要面对的取舍之一。

v1 可以接受这个成本，原因是：

- 先保证恢复正确性
- 再优化规模问题

未来如果规模成为问题，再考虑：

- 分片索引
- 分 account 扫描
- 增量恢复

### 21.2 单文件热点

单文件索引在高并发下会有写热点。

但 v1 的目标不是承载极端规模，而是先把语义跑通、恢复语义跑通，因此可以接受。

### 21.3 故障窗口仍然存在

即便用了 `tmp/bak`，仍然不能消灭所有故障窗口。

本方案的关键不是“绝对无窗口”，而是：

- 配置真相源在 meta
- 索引可重建

只要这两点成立，故障窗口最终是可补偿的。

## 22. v1 明确边界

v1 明确采用以下约束：

- 单文件索引
- 只让 idle session 入索引
- token-only session 不入索引
- 服务端 `idle_enabled` 默认开启
- 每次变更立即持久化
- 索引文件不放在 `resources`
- 不持久化 in-flight task 状态
- 配置真相源始终是 session meta
- commit 后如果已无继续跟踪价值，可以直接删索引项

## 23. 后续演进方向

后续可继续演进为：

- 索引分片
- 更强的恢复扫描策略
- 更强的跨进程 claim / lease
- 显式 policy 管理 API
- 更细粒度的 observability
- 指标上报
- 后台调度效率优化

## 24. 结论

这个方案的核心思路可以概括为：

- `commit_policy` 放在 session meta，作为真相源
- token threshold 走消息写入后的即时触发
- idle timeout 走后台 scheduler
- scheduler 依赖一个“只记录活跃 idle session”的单文件索引
- 索引放在 `_system`，不放在 `resources`
- 索引每次变更立即持久化，采用 `tmp -> bak -> 主文件`
- 启动恢复不能只依赖索引，必须允许用 meta 做补偿重建

这样可以在实现复杂度可控的前提下，把自动 commit 的核心能力从插件侧收回到服务端，并保证后续有继续增强的空间。

## 25. 设计与当前实现差异

本节用于对照当前代码实现，说明哪些部分已经落地，哪些部分仍与设计存在偏差。

### 25.1 已实现部分

当前代码里已经落地的内容包括：

- session meta 中已增加自动 commit 相关字段
  - `auto_commit_policy`
  - `last_message_at`
  - `auto_commit_last_error`
  - `auto_commit_last_error_at`
- 服务端配置中已增加 idle 自动触发全局开关
  - `server.session_auto_commit.idle_enabled`
- `add_message` / `batch_add_message` 已支持接收并持久化 auto commit policy
- `auto_commit_policy.keep_recent_count` 已同步写入 `session.meta.keep_recent_count`
- 当 `keep_recent_count` 变化时，已触发 `pending_tokens` 重算，保证 token threshold 判断语义一致
- token threshold 已在消息写入后尝试即时触发
- idle session 已进入单文件索引
- 索引文件已放到内部控制路径，而不是 `resources`
  - `/local/_system/session_auto_commit/index.json`
- 索引持久化已采用 `tmp -> bak -> 主文件` 轮转方式
- scheduler 已能周期扫描索引并尝试触发 idle commit
- 删除 session 时已同步清理索引项
- 自动 commit 路径中已接入最小去重能力
  - `TaskTracker.has_running(...)`
  - 进程内 claim set
  - `Session.commit_async()` 自身锁/no-op 语义
- batch message 接口现在只允许顶层传一次 `auto_commit_policy`
- `messages[*].auto_commit_policy` 已从 batch schema 中移除

### 25.2 当前实现与设计的主要偏差

#### 偏差 1：重启恢复还没有补上“从 meta 补偿重建索引”的逻辑

设计上已经明确：

- session meta 是配置真相源
- 索引只是调度加速层

因此在下面这个窗口里：

1. meta 已写入
2. 索引尚未写入
3. 进程崩溃

重启后应具备补偿恢复能力。

但当前实现中：

- 启动时 scheduler 只是加载索引文件
- 还没有做一次从 session meta 重建索引的恢复过程

这意味着：

- 某些 idle session 可能在重启后永久漏触发

这个问题属于高风险恢复语义缺失。

### 25.3 当前实现中尚未完全落实的设计点

下面这些设计点当前还没有完全闭环：

- 启动时的一次性发现/索引重建
- 更完整的测试覆盖
  - 重启恢复
  - commit 后索引删除
  - policy 关闭后的索引清理
  - token/idle 双触发并发去重

### 25.4 当前测试状态说明

当前相关测试已经补了一部分，主要覆盖：

- policy 持久化
- `last_message_at` 持久化
- token-only session 不进入 idle 索引
- idle policy session 进入索引
- 服务端全局 idle 开关关闭时不入索引
- 索引路径不在 `resources`

但测试仍存在两个现实限制：

- 当前 worktree 的测试环境写权限受限
- server fixture 会尝试在仓库内 `test_data/` 下创建临时目录

因此完整 pytest 结果尚未完全跑绿，现阶段不能把测试通过视为已完全验证。

### 25.5 建议的后续修正顺序

建议后续按下面顺序补齐：

1. 补启动恢复时的索引补偿重建逻辑
2. 补齐针对恢复、删除、并发去重的测试

### 25.6 当前实现的总体评价

当前实现已经把主框架搭起来了：

- 配置持久化路径是对的
- token / idle 分路是对的
- 单文件索引方向是对的
- `_system` 控制面存储位置也是对的

但从“方案落地完整度”来看，还不能算完全收口。

更准确地说，当前状态是：

- 主设计骨架已落地
- `keep_recent_count` 与 batch policy 语义问题已修正
- 但重启恢复语义仍需要继续补齐
