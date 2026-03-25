# OpenClaw Plugin 升级为真正上下文引擎的设计方案

## 1. 背景与目标

当前 `examples/openclaw-plugin` 已经具备 OpenViking 记忆写入、自动召回和工具式记忆操作能力，但整体仍然是“记忆插件 + 一个很薄的 context-engine 外壳”，而不是由 context-engine 生命周期统一负责上下文管理的真正上下文引擎。

现状特征：

- 自动召回主路径仍挂在 `before_prompt_build`
- 自动写入主路径仍挂在 `afterTurn`
- `assemble()` 目前基本没有承担上下文组装职责
- `compact()` 仍然委托 legacy engine，OpenViking 只参与记忆提取

这会带来 4 类问题：

1. OpenViking 统一以 context engine 的方式注册，但继续保留并使用原有 hook 链路。这里的 hook 能力并不等同于“必须注册成独立 memory plugin 并占据 `kind=memory` slot 才能使用”的能力。当前问题不是要去掉 hook，而是要把 hook 与 context engine 生命周期之间的职责分工、调用优先级和数据流转规则明确收口。
2. 自动召回结果目前以 `prependContext` 文本块直接注入，模型只能看到结果内容，看不到检索动作本身。这使自动召回与显式 `memory_recall` 工具在模型视角下不一致，模型也无法基于当前 recall 的 query 和结果边界判断是否需要继续检索。
3. 当前 compact 仍由 legacy engine 主导，OpenViking 只是在 compact 过程中附带参与记忆提取，尚未成为这条链路的正式主控方。因此，原始上下文的无损落盘、compact summary 生成、长期记忆提取、后续历史回放与来源追溯还没有被收口为一条统一的数据链路。
4. 后续如果要支持 skill memory、tool memory 和 session history expand，这些能力都需要跨越“原始 turn 写入、上下文组装、compact、历史回放、来源追溯”多个阶段流转。当前插件里还缺少统一的引擎状态和稳定的数据模型来记录这些对象及其引用关系，后续扩展容易分散在多个局部逻辑中，难以形成一致的数据链路。

## 2. 设计原则

后续实现统一遵循以下原则。

### 2.1 职责分层

OpenViking 统一注册为 context engine，但继续保留原有 hook 链路。后续需要明确的是二者的职责边界：hook 继续承接兼容路径和局部增强能力；完整的上下文组装、compact、历史回放和主生命周期协同由 context engine 统一编排。

### 2.2 复用 OpenViking 原生能力

优先复用 OpenViking 已有的 `Session -> Commit -> Archive -> Memory Extract` 体系，不在插件层额外创造第二套上下文生命周期，也不新增独立数据库。实现上尽量沿用以下能力完成闭环：

- `session.add_message`
- `session.commit`
- session archive / summary
- memory extract pipeline
- VikingFS 中已有的 user/agent/session 存储分层

### 2.3 无损优先

每轮参与推理的原始上下文必须先保存，再考虑抽取和压缩。compact 压缩的是“模型接下来看到的工作上下文”，不是 OpenViking 中已经无损保存的原始 turn / session 数据；压缩结果也不能替代原始消息本体。

### 2.4 可追溯、可展开

原始数据是后续追溯、重新抽取和历史展开的事实基座。无论长期记忆、compact summary 还是后续 expand，都必须保留到原始 turn/session 的稳定引用。

### 2.5 分阶段落地

先把插件升级成真正的 context-engine 闭环，再引入 skill/tool memory 等增强能力。Phase 1 完成后，插件就应当已经具备“真正上下文引擎”的最小定义。


本文档作为后续开发执行规范，聚焦 OpenClaw 插件升级为真正上下文引擎时的目标架构、数据模型和落地步骤，并优先按各个 hook 的实施职责组织说明。

参考资料：
- [OpenViking Session 管理文档](/Users/quemingjian/Source/OpenViking/docs/en/concepts/08-session.md)

---

## 3. 按 Hook 的当前实现与实施清单

当前相关实现主要分布于：

- [examples/openclaw-plugin/index.ts](/Users/quemingjian/Source/OpenViking/examples/openclaw-plugin/index.ts)
- [examples/openclaw-plugin/context-engine.ts](/Users/quemingjian/Source/OpenViking/examples/openclaw-plugin/context-engine.ts)
- [examples/openclaw-plugin/client.ts](/Users/quemingjian/Source/OpenViking/examples/openclaw-plugin/client.ts)

## 3.1 `before_prompt_build`: 兼容 fallback 与迁移约束

### 当前实现

当前 `before_prompt_build` 会执行搜索，并把 recall 结果拼接为 `<relevant-memories>` 文本块注入。

### 职责

在 Phase 1 期间保留 fallback recall，保证旧环境仍可工作；一旦 `assemble()` 成为主链路，这个 hook 只保留兼容职责，不再承担默认实现。

### 实施清单

1. 保留当前 recall fallback 作为兼容与降级路径，保证旧环境继续可用；后续新增的自动召回能力、上下文编排能力和注入形态演进统一落在 `assemble()`，不再继续扩展 `before_prompt_build` 主逻辑。
2. 延续现有“宽检索、窄注入”的有界策略，不允许回退到无上限全文拼接。
3. 保持并行检索 `viking://user/memories` 与 `viking://agent/memories` 的做法，并在本地完成去重、筛选、排序和预算裁剪。
4. 保持 `level === 2` 作为最终可注入 detail memory 的判断标准，不把 L0/L1 直接当作最终注入正文。
5. 保持 `recallScoreThreshold`、`recallMaxContentChars`、`recallPreferAbstract`、`recallTokenBudget` 这些配置约束继续生效。
6. 后续迁移到 `assemble()` 时，迁移的是并行检索、去重、`level === 2` 过滤、score threshold、query-aware ranking、摘要优先、单条截断、总 token budget 这些规则本身；迁移完成后，`before_prompt_build` 只保留一个最小 fallback，而不再作为默认主入口。

### 不在此 hook 做的事情

- 不负责主路径上下文组装
- 不承接 compact 结果回放
- 不新增新的状态写入逻辑

## 3.2 `afterTurn`: 无损写入主链路

### 当前实现

当前 `afterTurn` 会提取本轮增量 user/assistant 文本，创建临时 OpenViking session，并调用 `/extract`。

### 职责
afterTurn 在新架构中承担两件事：
1. **无损写入**：将本轮消息写入 OpenViking session。
2. **Compact 评估与触发**：判断是否需要调用 `session.commit()` 归档消息和提取记忆。
两个职责顺序执行：先写入，再评估。评估结果可能导致同步或异步的 compact 调用，但不影响写入的完成。

重构逻辑：
管理一个OV的Session状态数组，每个Session对应一个OV的Session状态和信息：(下面涉及到的session都是指OV内部定义的session)
0. Compact状态检查，检查Session数组的状态:
   - 如果数组中没有本次会话期望的sessionId 则新建OV session;
   - 如果数组中有本次会话期望的session, 检查session状态:
     - 如果在Compact状态，则新建会话;
     - 如果在非Compact状态，则复用这个session;
1. 入口检查：autoCapture 未启用 → 直接返回(复用原逻辑)
2. 解析身份：调用 resolveAgentId(sessionId) 获取当前 agentId，生成复合 bufferKey = sessionId:agentId
3. 切片新消息：用 prePromptMessageCount 从完整 messages 中切出本轮新增部分(复用原逻辑)
4. 提取文本：extractNewTurnTexts 提取 user/assistant 角色的纯文本，无新文本 → 返回(复用原逻辑)
5. 捕获判断：getCaptureDecision 判断文本是否值得捕获（长度、模式匹配等），不值得 → 返回(复用原逻辑)
6. 累积到 buffer：按 bufferKey 查找或创建 SessionBuffer，更新 totalChars 和 messageCount

Compact 评估与触发：
7. 按优先级从高到低逐条判断（命中第一个即触发，不继续往下）：
   - 预算阈值：estimatedTokens >= budget × 0.75？ → 异步后台 compact
   - 新增 token 积累：tokensAdded >= 30,000？ → 异步后台 compact
   - 轮次积累：turns >= 20？ → 异步后台 compact
   - 最长间隔：interval >= 30min 且 turns >= 1？ → 异步后台 compact
   - 无条件满足 → 不触发
8. 触发后执行 compact 提交：
   - 调用 `session.commit(wait=false)` 提交 OpenViking session
   - 异步：commit 在后台执行，afterTurn 立即返回，更新 session中 CompactState， 下一轮 Compact状态检查 中确认完成

兜底 flush：进程退出时（service.stop() / SIGTERM / SIGINT），遍历所有 buffer 执行 commitAndReset，确保未提交的累积内容不丢失

#### Compact触发条件与保护机制
**触发条件**

| 条件 | 场景 | 阈值 | 执行方式 |
|---|---|---|---|
| 预算阈值 | 上下文达到预警线 | `estimatedTokens >= budget × 0.75` | 异步后台 |
| 新增 token 积累 | 大量新内容（大文件、长工具输出等） | `tokensAdded >= 30,000` | 异步后台 |
| 轮次积累 | 短对话多轮，token 阈值不触发 | `turns >= 20` | 异步后台 |
| 最长间隔 | 长时间未 compact | `interval >= 30min` 且 turns >= 1 | 异步后台 |

典型场景：
- **预算阈值**：上下文达到 96k（128k × 0.75），还有余量但该开始压缩了。后台异步执行 commit，不阻塞当前轮返回。这是最常见的触发路径。
- **新增 token 积累**：总上下文才 60k，远没到阈值，但本轮用户粘贴了一个大文件（35k tokens）。虽然总量不紧张，但大量原始内容堆积不压缩会持续占用空间。对应 lossless-claw 的"叶子触发"，但阈值更高（30k vs 20k），因为 `session.commit()` 是全量归档的重操作。
- **轮次积累**：用户在做代码审查，每轮只问一个短问题（300-500 tokens），20 轮下来才 8k tokens，token 阈值不会触发。但 20 轮的对话已经有足够的信息量值得做一次摘要和记忆提取。
- **最长间隔**：用户上午聊了 5 轮后去开会，下午回来继续。距离上次 compact 已超过 30 分钟，触发一次整理。要求至少有 1 轮新内容，避免空 commit。

**保护机制**
| 机制 | 场景 | 说明 |
|---|---|---|
| 并发互斥 | 上一轮的异步 compact 还在跑，本轮又触发了条件 | 同一 session 同时只允许一个 compact，避免重复提交 |
| 异常退出 | 进程退出时（service.stop() / SIGTERM / SIGINT），遍历所有 session 执行 commit，确保未提交的累积内容不丢失 |

## 3.3 `assemble`: 自动召回与上下文组装主链路

### 当前实现

当前 `assemble()` 基本直接回传原始 messages，不承担上下文编排职责。

### 职责

`assemble()` 是真正的上下文组装入口，负责把 profile、长期记忆、recent raw turns、compact summary 统一编排成最终返回给 OpenClaw 的 messages。

### 输入

- 当前 OpenClaw sessionId
- 当前 messages
- token budget

### 输出

- 注入后的 `messages`
- `estimatedTokens`
- 必要时的 `systemPromptAddition`

### 实施清单

1. 读取 stable profile，包括 `profile.md` 和稳定偏好类高质量记忆。
2. 从最近 `assembleRecallWindow` 条 user turns 构造 recall query，并做轻量 skip 判断，跳过问候、无内容、纯短句。
3. 并行检索 `viking://user/memories` 与 `viking://agent/memories`。
4. 复用当前 fallback recall 的本地约束：去重、`level === 2` 过滤、score threshold、query-aware ranking、摘要优先、单条截断、总 token budget。
5. 读取 session context，包括最近 raw turns 和最近 compact summary / archive overview。
6. 把 recalled memories、raw turns、compact summary 混合编排为最终注入消息。
7. 统一计算 token，并在超预算时优先裁剪 recalled memories 和旧 summary，而不是裁剪最新 raw turns。

### 自动召回的注入形态

自动召回结果不再用：

```text
<relevant-memories> ... </relevant-memories>
```

而改为注入为合成消息，形态类似：

```text
assistant: [tool_call] memory_recall_auto({"query":"..."})
tool: [tool_result] {"memories":[...], "source":"openviking-auto-recall"}
```

### 补充职责

- 读取 compact summary，而不是只靠长期记忆支撑历史连续性
- 将最近未 compact 的 raw turns 与历史 summary 混合组装
- 为后续 `memory_expand` 提供 summary 到 raw session 的桥梁

## 3.4 `compact`: 正式 commit 边界

### 当前实现

当前 `compact()` 仍调用 legacy context engine 的 compact，OpenViking 只是在其过程中附带参与记忆提取。

### 职责

`compact()` 负责触发 OpenViking `session.commit()`，把 session 写入、归档、summary 生成和长期记忆提取统一收口到一个正式同步点。

### 实施清单

1. 调用 OpenViking `session.commit()`。
2. 触发当前 session 消息归档。
3. 读取并记录最新 session summary / archive summary。
4. 触发长期记忆提取，并记录本次抽取出的 memory URI。
5. 落 `CompactCheckpoint`，保存本次 compact 的时间、来源 turn 范围和 summary 引用。
6. 返回新的工作上下文，至少包含最近 `recentRawTurnCount` 条 raw turns、最新 compact summary、必要的 pending tasks / active instructions。
7. 保证 compact 失败时可回退，不破坏原始 session 数据。

### 与当前实现的差异

- 不再依赖 legacy compact
- 自动记忆提取从 `afterTurn` 主路径迁移到 `compact` 主路径
- `compact()` 成为 OpenViking 与 OpenClaw session 边界的正式同步点

---

## 4. 终态架构

### 4.1 总体数据流

```text
OpenClaw turn
  │
  ├─ afterTurn
  │   └─ 将本轮真实上下文写入 OpenViking session（无损保存）
  │
  ├─ assemble
  │   ├─ 读取 profile / stable memories
  │   ├─ 按 query 检索 user/agent memories
  │   ├─ 读取最近 raw turns + compact summaries
  │   └─ 组装成新的 messages 返回给 OpenClaw
  │
  └─ compact
      ├─ 调用 OpenViking session.commit()
      ├─ 归档旧消息并生成 session summary
      ├─ 提取长期记忆
      └─ 返回压缩后的工作上下文
```

### 4.2 存储分层

本方案不引入新的顶层存储系统，直接复用 OpenViking：

- `viking://session/{user_space}/{session_id}`：保存完整对话 turn、工具调用、上下文使用记录、compact history
- `viking://user/.../memories`：用户画像、偏好、实体、事件等长期记忆
- `viking://agent/.../memories`：cases、patterns 等 agent 记忆
- `viking://agent/.../skills`：后续 skill memory Phase 3 的锚点

### 4.3 关键设计决策

1. 不引入插件侧独立 SQLite / DAG。
2. 不再把自动召回主结果作为纯文本 prompt prepend。
3. 自动 recall 的默认注入形态改为“模拟工具调用结果消息”。
4. compact 的正式语义改为 OpenViking `session.commit()`，而不是 legacy compact + 附带提取。
5. `memory_store` 仍保留，但只是“显式强制写入”的辅助手段，不再承担主数据链路。

---

## 5. 核心数据结构

为了让实现可执行，插件内部需要统一最小数据结构。

### 5.1 TurnEnvelope

每个进入 OpenViking session 的 turn 都按统一结构落盘。

```ts
type TurnEnvelope = {
  turn_id: string;
  session_id: string;
  sequence: number;
  timestamp: string;
  role: "user" | "assistant" | "system" | "tool";
  parts: Array<
    | { type: "text"; text: string }
    | { type: "tool_call"; tool_name: string; arguments: unknown }
    | { type: "tool_result"; tool_name: string; result: unknown }
    | { type: "context_ref"; uri: string; abstract?: string }
    | { type: "meta"; key: string; value: unknown }
  >;
  used_context_uris: string[];
  used_skills: Array<{
    uri: string;
    input?: string;
    output?: string;
    success?: boolean;
  }>;
  tool_calls: Array<{
    tool_name: string;
    arguments: unknown;
    status?: "success" | "error";
  }>;
  tool_results: Array<{
    tool_name: string;
    result: unknown;
    status?: "success" | "error";
  }>;
  compaction_marker?: {
    source: "raw" | "post_compact";
    compact_checkpoint_id?: string;
  };
};
```

约束：

- `parts` 必须能完整表示本轮输入给模型和模型产出的关键上下文
- `used_context_uris` 记录本轮真正注入的外部上下文 URI
- `used_skills` 和 `tool_calls/tool_results` 用于后续 patterns/cases 抽取与可追溯分析
- 当前无法拿到的字段可以先留空，但字段定义必须稳定

### 5.2 AssembledContextPacket

`assemble()` 内部统一生成如下结构，再转换成 OpenClaw `messages`：

```ts
type AssembledContextPacket = {
  profile_blocks: string[];
  recalled_memories: Array<{
    query: string;
    uri: string;
    abstract?: string;
    score?: number;
  }>;
  compact_summaries: Array<{
    session_uri: string;
    abstract?: string;
    overview?: string;
  }>;
  recent_raw_turns: TurnEnvelope[];
  injected_messages: Array<Record<string, unknown>>;
  estimated_tokens: number;
};
```

### 5.3 CompactCheckpoint

每次 compact 后记录一个 checkpoint，用于之后的可展开与调试：

```ts
type CompactCheckpoint = {
  checkpoint_id: string;
  session_id: string;
  committed_at: string;
  archive_index: number;
  source_turn_range: {
    start_sequence: number;
    end_sequence: number;
  };
  summary_uri?: string;
  extracted_memory_uris: string[];
};
```

---

## 6. 工具设计

## 6.1 保留工具

保留以下工具，并继续由插件注册：

- `memory_recall`
- `memory_store`
- `memory_forget`

其中：

- `memory_recall`：显式检索长期记忆
- `memory_store`：用户明确要求“记住这件事”时强制写入
- `memory_forget`：删除或撤销错误记忆

## 6.2 新增工具 `memory_expand`

Phase 2 新增：

```ts
memory_expand({
  summaryOrMemoryUri: string,
  query?: string,
  limit?: number
})
```

返回：

- 关联的 session/archive URI
- 命中的原始 turn 摘要
- 可进一步 read 的原始上下文 URI 列表

作用：

- 从 compact summary 回到原始对话片段
- 从 memory 抽象回到创建它的原始 turn
- 补足 summary 到原始上下文之间的回钻能力

## 6.3 自动与显式工具的关系

- 自动 recall 由 `assemble()` 触发
- 显式 recall 由模型主动调用 `memory_recall`
- 自动 recall 的注入形态与显式 recall 保持一致，降低模型学习成本

---

## 7. 配置与兼容策略

### 7.1 保持兼容的配置项

以下现有配置保持保留：

- `mode`
- `configPath`
- `port`
- `baseUrl`
- `apiKey`
- `agentId`
- `timeoutMs`
- `autoRecall`
- `autoCapture`
- `recallLimit`
- `recallScoreThreshold`
- `recallMaxContentChars`
- `recallPreferAbstract`
- `recallTokenBudget`

### 7.2 新增配置项

新增最小集合：

```ts
type ContextEngineRefactorConfig = {
  recentRawTurnCount?: number;
  assembleRecallWindow?: number;
  compactCommitThreshold?: number;
  memoryExpandMaxResults?: number;
};
```

建议默认值：

- `recentRawTurnCount = 8`
- `assembleRecallWindow = 5`
- `compactCommitThreshold = 0.75`
- `memoryExpandMaxResults = 6`

### 7.3 协同与兼容策略

Phase 1 期间采用双轨兼容：

1. 如果 OpenClaw 支持完整 context-engine 生命周期：
   - 由 `assemble()` / `compact()` 承担完整上下文生命周期
   - 原有 hook 继续保留，用于兼容与局部协同
2. 如果环境缺少完整能力：
   - 退化到现有 `before_prompt_build` recall 方案

默认目标不是移除 hook，而是让 hook 与 context engine 的职责边界和调用优先级稳定下来，避免重复实现和相互覆盖。

### 7.4 弃用项

以下实现被标记为 deprecated：

- `afterTurn` 中临时 session + `/extract` 的主逻辑
- `tryLegacyCompact()` 依赖 legacy context engine

说明：

- `before_prompt_build` hook 本身不废弃；被弃用的是把完整自动 recall 主链路长期放在该 hook 中的做法。
- 原有 hook 体系继续保留，但其职责应收敛到兼容路径和局部增强，而不是继续承接完整上下文生命周期。

---

## 8. 分阶段实施方案

## Phase 1: 让插件成为真正 context-engine

### 目标

完成“写入、组装、compact”三条主链路收口。

### 必做项

1. `afterTurn` 改为持久化 `TurnEnvelope`
2. `assemble()` 接管自动 recall 和上下文组装
3. 自动 recall 注入形态改为合成工具消息
4. `compact()` 改为调用 OpenViking `session.commit()`
5. 保留原有 hook 链路，并明确它与 context engine 生命周期之间的边界、优先级和协同方式

### Phase 1 验收标准

- 在具备完整 context-engine 生命周期的环境中，不依赖 `before_prompt_build` 也能完成自动 recall
- 不依赖临时 extract 也能正常积累上下文
- 不依赖 legacy compact 也能完成 compact 与后续连续对话

## Phase 2: 可追溯与可展开

### 目标

让压缩历史真正可回钻。

### 必做项

1. 为 compact 结果落 `CompactCheckpoint`
2. 读取 session summary / archive overview 参与 `assemble`
3. 新增 `memory_expand`
4. 建立 memory -> source turn / session 的追溯关系

### Phase 2 验收标准

- 新 session 中可以回答依赖 compact 前历史的问题
- 模型在需要时可以通过 `memory_expand` 找回原始细节

## Phase 3: 高级增强能力

这一阶段不阻塞 context-engine 升级成功。

范围包括：

- skill memory 注入
- tool memory 注入
- `ov ls viking://` 目录预注入
- 更细粒度的 recall intent analyzer
- summary 质量治理与更复杂的层级压缩策略

---

## 9. 需要修改的接口与模块

### 9.1 插件侧

- [examples/openclaw-plugin/context-engine.ts](/Users/quemingjian/Source/OpenViking/examples/openclaw-plugin/context-engine.ts)
  - 重写 `assemble()`
  - 重写 `afterTurn()`
  - 重写 `compact()`
- [examples/openclaw-plugin/index.ts](/Users/quemingjian/Source/OpenViking/examples/openclaw-plugin/index.ts)
  - 将 `before_prompt_build` recall 降级为 fallback
  - 新增 `memory_expand`
  - 迁移自动 recall 辅助逻辑到 context-engine 内部
- [examples/openclaw-plugin/client.ts](/Users/quemingjian/Source/OpenViking/examples/openclaw-plugin/client.ts)
  - 补充 session commit / summary / archive 相关读写接口

### 9.2 OpenViking 服务端

插件实现默认复用现有 session API，但以下能力若现有返回不够用，需要补充：

1. 读取 session latest summary / archive overview 的便捷接口
2. 从 memory 反查 source session / source turn 的元数据
3. compact checkpoint 的持久化查询接口

原则：

- 能复用已有 `session` / `content` / `fs` API 就不新增新协议
- 若新增 API，必须围绕 “summary read / source trace / expand support” 三类能力展开

---

## 10. 测试方案

## 10.1 单元测试

新增或补齐以下测试：

- `TurnEnvelope` 构造与字段完整性
- recall query window 拼接与 skip 策略
- 自动 recall 注入消息格式
- token budget 裁剪
- 新增配置项解析
- `memory_expand` 参数校验与结果格式

## 10.2 集成测试

覆盖以下场景：

1. 多轮对话后 `afterTurn` 成功无损写入 session
2. 触发 compact 后 session 完成 commit、archive、memory extract
3. 新会话中 `assemble()` 能召回 compact 前历史
4. `memory_store` 显式提交与自动管线不冲突
5. 本地模式和远程模式均可工作
6. 多 agent 场景下 `agentId` 隔离不破坏

## 10.3 回归测试

必须验证：

- 自动注入内容不会再次被 capture
- OpenViking 服务不可用时，context-engine 优雅降级
- recall 注入不会导致明显上下文膨胀回归
- 旧配置文件仍能启动插件

---

## 11. 验收场景

以下场景全部成立，才算升级成功。

### 场景 1：长期对话连续性

一个长对话触发 compact 后，后续会话仍能回答：

- “我们上周关于项目 X 做过什么决定？”
- “之前你给我的数据库优化建议里，哪条是针对慢查询的？”

### 场景 2：显式记忆与自动记忆共存

用户说“记住我偏好深色主题”，插件通过 `memory_store` 立即写入；之后普通对话中的重要决策，在 compact 时通过自动管线进入长期记忆。

### 场景 3：压缩历史可回钻

模型先依据 compact summary 回答，再在需要时通过 `memory_expand` 找回原始 turn 细节，而不是只能依赖抽象 memory。

### 场景 4：引擎而非 hook 主导

禁用 `before_prompt_build` 主 recall 路径后，context-engine 仍能正常工作。

---

## 12. 明确不做的事情

本设计不包含以下内容：

- 在插件内自建新的独立持久化数据库
- 在 Phase 1 中同时上线 skill memory / tool memory 全能力
- 修改 OpenClaw 内核的 session reset 语义

---

## 13. 开发执行建议

建议按以下顺序开发：

1. 先实现 `afterTurn` 的 `TurnEnvelope` 持久化
2. 再实现 `assemble()` 的 recall 注入与 session summary 读取
3. 然后切换 `compact()` 到 OpenViking `session.commit()`
4. 最后补 `memory_expand` 与追溯元数据

原因：

- 先有稳定写入，后续 recall 和 expand 才有可靠数据源
- 先让 `assemble()` 成为主链路，再移除 hook 才安全
- `compact()` 最后切换，便于逐步回归验证

---

## 14. 默认决策

为避免后续开发再次出现未收敛讨论，本文固定以下默认决策：

1. 文本自动 recall 的默认注入方式为“模拟工具调用结果”，不是 prompt prepend。
2. 自动长期记忆提取的正式触发边界为 `compact()`，不是每轮 `afterTurn`。
3. `afterTurn` 的第一职责是无损保存，不是立即抽取。
4. OpenViking Session 是主存储基座，不新增插件侧独立数据库。
5. skill memory / tool memory 不进入 Phase 1 验收范围。

以上决策除非后续出现明确阻塞，否则实现阶段不再重新讨论。
