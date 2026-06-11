# OpenClaw 接入 OpenViking Agent Experience Recall 设计

日期：2026-05-28

## 当前结论

OpenViking 已经负责从 session commit 后的轨迹中抽取 agent experience。本 PR 不重做经验抽取、不改 commit policy，也不新增“长期记忆/经验记忆是否抽取”的控制面。

本 PR 只做运行期使用面：

1. 经验记忆功能默认关闭，必须显式配置 `agentExperience.enabled: true` 才启用。
2. 启用后，OpenClaw 在 `transformContext assemble` 阶段判断当前 turn 是否像执行任务。
3. 符合条件时，从 `viking://user/memories/experiences` 检索 agent experience。
4. 同一轮也保留原有长期记忆 auto recall，并把 experience 和长期记忆分成不同 section。
5. 使用统一的 `<openviking-context>` 外壳注入到 latest user message 前；即使只有长期记忆，也使用这个外壳。
6. `afterTurn` 写 session 前剥离 `<openviking-context>`，避免把召回内容再次写回 OV。

这是一版“OpenClaw 能用 OV 经验记忆”的基准实现，不是完整的多 hook experience system。

## 不在本 PR 中处理的事

以下内容明确不属于本 PR：

- 不新增或修改 OV 服务端的 trajectory -> experience 抽取逻辑。
- 不新增 commit 时的 extraction policy。一次 commit 是否抽长期记忆、是否抽 experience，由 OV core 后续控制面解决。
- 不改变现有 `afterTurn()` session capture 主链路。
- 不改变现有 `compact()` / `commitSession()` 触发策略。
- 不新增 OpenClaw host hook。
- 不实现 skill load / subagent start / write preflight 的自动注入。
- 不新增 `experience_recall` 工具。
- 不把 experience 和普通长期记忆混成同一种记忆格式。

## 为什么接在 transformContext assemble

OpenClaw 插件当前有两类 assemble：

- preflight assemble：用于回读 OV session context，把已有 session 上下文给 OpenClaw。
- transformContext assemble：LLM 看到消息前最后一次改写消息上下文。

经验记忆是“执行前提醒”，应该发生在 LLM 决策前。当前 OpenClaw 插件没有 MemOS 那种 `before_prompt_build` hook，也没有 `before_subagent_start` / 可中断 `before_tool_call`。因此当前可落地的正确位置是 transformContext assemble。

当前代码路径：

```text
examples/openclaw-plugin/context-engine.ts
  assemble()
    -> isTransformContextAssemble
    -> latestMessage.role === "user"
    -> prepareRecallQuery()
    -> shouldRecallAgentExperience()
    -> buildAgentExperienceRecallContext()
    -> buildLongTermMemoryRecallContext()
    -> buildOpenVikingContextBlock()
    -> prependRecallToLatestUserMessage()
```

## 当前 PR 的文件改动

### `examples/openclaw-plugin/config.ts`

新增 `agentExperience` 配置，并在 parse 阶段补齐默认值：

```ts
agentExperience?: {
  enabled?: boolean;
  recallLimit?: number;
  scoreThreshold?: number;
  maxInjectedChars?: number;
  minQueryChars?: number;
};
```

默认值：

```ts
{
  enabled: false,
  recallLimit: 3,
  scoreThreshold: 0.35,
  maxInjectedChars: 6000,
  minQueryChars: 12,
}
```

`enabled` 默认是 `false`，这是保守发布开关：默认不触发 experience recall，也不增加额外检索请求。用户确认服务端 experience 数据质量和注入效果后，再显式打开。

`ParsedMemoryOpenVikingConfig` 是 schema parse 后的运行期类型，重点是让 `agentExperience` 的子字段也变成 required，避免 TypeScript 仍然认为 `recallLimit` / `maxInjectedChars` 可能是 undefined。

### `examples/openclaw-plugin/auto-recall.ts`

新增经验召回相关逻辑，但仍放在 `auto-recall.ts` 中。原因是当前插件还没有统一 recall source 目录体系，经验召回和长期记忆召回共享 query preparation、timeout、token estimate、context block 构造等基础设施。

核心新增/调整：

- `OPENVIKING_CONTEXT_TAG = "openviking-context"`
- `ExperienceRecallTrigger`
- `shouldRecallAgentExperience()`
- `isCronSession()`
- `buildAgentExperienceRecallContext()`
- `buildOpenVikingContextBlock()`
- `buildLongTermMemorySection()`
- `buildLongTermMemoryRecallContext()` 过滤掉 experience memory，避免 experience 出现在长期记忆区；同时生成 `Long-term Memories` section，交给统一外壳注入。

经验召回复用配置项 `autoRecallTimeoutMs`，不单独开 experience timeout；默认值仍是 `5000ms`。

### `examples/openclaw-plugin/context-engine.ts`

在 transformContext assemble 中接入两路 recall：

```text
experience recall
  -> 只查 viking://user/memories/experiences
  -> 受 agentExperience 配置和 shouldRecallAgentExperience 控制

long-term recall
  -> 查 viking://user/memories
  -> recallResources=true 时查 viking://resources
  -> 过滤掉 experiences
```

然后统一生成 `<openviking-context>` 外壳：

```ts
const combinedBlock = buildOpenVikingContextBlock({
  sections: [experienceRecall.block, recall.section],
});
```

如果没有 experience 命中，普通长期记忆仍然作为 `Long-term Memories` section 放入 `<openviking-context>`。如果两个结果都为空，直接 passthrough，不改写 messages。

### `examples/openclaw-plugin/text-utils.ts`

新增剥离：

```text
<openviking-context ...>...</openviking-context>
```

并保留历史格式清理：

```text
<relevant-memories>...</relevant-memories>
```

这样 `afterTurn()` 写 session 前会清理当前格式和历史格式注入块，避免本轮召回内容进入下一轮抽取。运行时新注入统一使用 `<openviking-context>`。

### tests

PR 中保留单元测试：

- `tests/ut/agent-experience-recall.test.ts`
- `tests/ut/context-engine-assemble.test.ts`
- `tests/ut/context-engine-afterTurn.test.ts`
- `tests/ut/text-utils.test.ts`

`tests/integration/test_openclaw_openviking_strict_e2e.py` 只作为本地严格联调脚本保留，不进入远端 PR。

## 运行时流程

### 1. 进入 assemble

当前只在 transformContext assemble 阶段自动注入。前置条件：

- session 没有被 `bypassSessionPatterns` 命中。
- latest message 是 user。
- `cfg.autoRecall` 或 `cfg.agentExperience.enabled` 至少一个开启。
- latest user message 没有已经包含 `<openviking-context>`。
- `prepareRecallQuery()` 清理后的 query 非空，且长度至少 5。

如果以上任一条件不满足，直接 passthrough。

### 2. 生成 query

query 来自 latest user message：

```ts
const recallQuery = prepareRecallQuery(extractAgentMessageText(latestMessage));
```

`prepareRecallQuery()` 会先调用 `sanitizeUserTextForCapture()`，因此旧的注入块、sender metadata、conversation metadata 等噪音不会进入 recall query。query 最长 4000 字符，超出会截断并写日志。

### 3. 判断是否要查 experience

经验召回首先受总开关控制：`agentExperience.enabled` 默认关闭。只有显式开启后，才会进入 `shouldRecallAgentExperience()`。这个 task gate 是内置逻辑，不再提供单独配置开关。

硬跳过：

- session bypass。
- query 为空或短于 `agentExperience.minQueryChars`。
- latest user text 已包含 `<openviking-context>`。

强制召回：

- `triggerHint` 不是 `task_start`，例如 `cron_start`。
- `sessionKey` 包含 `:cron:`，或 `runtimeContext.isCron === true`，或 `runtimeContext.automationKind === "cron"`。

普通 task gate 使用确定性打分：

```text
+3 write/edit/modify/delete/migrate/deploy/release/configure/patch 等副作用动作
+2 fix/debug/test/build/run/implement/refactor/integrate/troubleshoot 等执行动作
+2 error/exception/failed/retry/traceback/test failed 等失败信号
+2 文件路径、代码对象、hook/API/tool/package/module 等工程对象
+1 经验/踩坑/最佳实践/avoid/best practice/lesson/pitfall 等经验意图
-3 闲聊、翻译、总结当前对话等非执行场景
-2 纯知识问答，并且没有工程对象和执行动词
```

`score >= 3` 才自动查 experience。

### 4. 检索 experience

经验召回只查 agent experience 目录：

```ts
client.find(queryText, {
  targetUri: "viking://user/memories/experiences",
  limit: Math.max(expCfg.recallLimit * 4, 12),
  scoreThreshold: expCfg.scoreThreshold,
}, agentId)
```

后处理：

- 只保留 URI/category 看起来是 experience 的结果。
- 按 URI 去重。
- 截到 `agentExperience.recallLimit` 条，默认 3。
- `level === 2` 时用 `client.read()` 读取完整内容；否则使用 abstract / overview / uri。
- 只渲染结构化 experience，或者 metadata/URI 明确标记为 experience 的内容。
- 总注入字符不超过 `agentExperience.maxInjectedChars`。
- 受 `autoRecallTimeoutMs` 控制；超时或失败只 warn，不阻塞 OpenClaw。

当前不查 raw trajectories。

### 5. 渲染 experience

OV experience 当前主要结构是：

```markdown
## Situation
...

## Approach
...

## Reflect
...
```

OpenClaw 注入时映射为更适合执行期阅读的字段：

```markdown
### Experience: <filename>
Source: <uri>
Score: <score>

Trigger:
- from Situation

Do:
- from Approach

Avoid:
- from Reflect

Scope:
- from Situation

Check:
- from Reflect or Approach
```

如果原文缺某个字段，会用摘要或固定 fallback 补齐，避免给 LLM 一个空标题。

### 6. 检索长期记忆

原有 auto recall 保留，但输出不再直接包 `<relevant-memories>`。它现在只生成 `## Long-term Memories` section，再交给统一外壳。transformContext assemble 和非 transformContext assemble 都统一注入 `<openviking-context>`。

搜索范围：

- `viking://user/memories`
- `viking://resources`，仅 `recallResources=true`

后处理：

- 合并结果并按 URI 去重。
- 只保留 leaf memory。
- 过滤掉 experience memory，避免和 `Agent Experiences` 重复。
- 使用现有 `postProcessMemories()` / `pickMemoriesForInjection()` 排序和截断。
- 使用 `recallMaxInjectedChars` 做完整条目预算控制，单条记忆不截半。

### 7. 注入外壳

长期记忆单独注入时也使用统一外壳：

```markdown
<openviking-context>
## Long-term Memories

Source: openviking-auto-recall
The following OpenViking memories may be relevant:
- [profile] ...
</openviking-context>

<original latest user message>
```

同时注入 agent experience 和长期记忆时：

```markdown
<openviking-context>
## Agent Experiences

These are prior execution lessons learned by this agent. Use them as task guidance, not as user facts.

### Experience: openclaw-plugin-file-write-guard
Source: viking://user/default/memories/experiences/openclaw-plugin-file-write-guard.md
Score: 0.910

Trigger:
- 当修改 OpenClaw 插件 afterTurn 写回逻辑时。

Do:
- 在写回 OV session 前剥离注入上下文块。

Avoid:
- 避免把注入经验再次写回 transcript。

Scope:
- 当修改 OpenClaw 插件 afterTurn 写回逻辑时。

Check:
- 避免把注入经验再次写回 transcript。

## Long-term Memories

Source: openviking-auto-recall
The following OpenViking memories may be relevant:
- [profile] ...
</openviking-context>

<original latest user message>
```

规则：

- 外壳统一用 `<openviking-context>`，表达“OpenViking 注入的上下文”，不绑定 OpenClaw/VikingBot/Codex 任一消费方。
- 内部用 Markdown section 区分经验记忆和长期记忆。
- `Agent Experiences` 在前，因为它影响执行策略。
- `Long-term Memories` 在后，因为它更多是用户事实、偏好、资源。
- 没有命中的 section 直接省略。
- 两个 section 都没有时，不注入任何东西。
- 清理逻辑兼容历史 `<relevant-memories>` 和当前 `<openviking-context>`。

## 为什么 experience block 和 long-term block 不合成一个 section

它们都属于 recall，但语义不同：

- long-term memory 是事实/偏好/资源，回答时可以当作上下文事实。
- agent experience 是执行策略/踩坑/验证方式，只能当作任务指导，不能当成用户事实。

如果混在同一个 bullet list 里，模型容易把“以前修 bug 的做法”当成“当前用户事实”。所以它们共享 `<openviking-context>` 外壳，但必须分 section。

## 与 MemOS 的关系

MemOS OpenClaw adapter 的自动注入点是 `before_prompt_build`，返回 `{ prependContext }`。它不是每个 tool 调用前无脑注入，也不是 subagent 启动时一定注入。

OV OpenClaw 当前没有这个 hook，但 transformContext assemble 在语义上等价于“LLM prompt 构造前最后一次上下文改写”。所以本 PR 采用 transformContext assemble。

MemOS 值得参考的是三点：

- 用一个外层 block 包住注入内容，便于清理。
- 经验和普通记忆分区展示。
- 经验要渲染成行动指导，而不是原始轨迹。

本 PR 不复刻 MemOS 的 L1 Trace / L2 Policy / L3 World Model / Skill 层级。OV 服务端已经负责 trajectory 和 experience 的沉淀，OpenClaw 插件只消费结果。

## 与 VikingBot 的关系

VikingBot 目前已有多个经验注入点：

- skill 读取后追加 `## Related Experiences`
- subagent task 前追加 `## Agent Experience`
- write tool 前检测写类工具，插入 `## Relevant Agent Experience`
- cron/benchmark 场景通过任务 prompt 读取 experience

这些是 VikingBot 内部 agent loop 的直接拼接逻辑，不是一个跨插件的公共 envelope。

因此新外壳不应该叫 `vikingbot-context`，也不应该叫 `openclaw-context`。`<openviking-context>` 更适合作为 OV 面向不同 agent 插件的统一注入外壳。未来 VikingBot 如果迁移到公共插件协议，也可以选择复用这个 envelope；但本 PR 不改 VikingBot。

## 后续更精确接入点

下列入口从原理上适合注入 experience，但需要 OpenClaw host 暴露更精确的 hook 或可中断控制面。当前 PR 不实现。

### Skill 加载时

适合原因：agent 已经选择读取某个 `SKILL.md`，此时用 skill name/description 查经验，注入到 skill 内容旁边，相关性比全局 prompt 更高。

需要 host 支持：

```ts
tool_result_persist / after_tool_call
  -> 允许插件修改 read_file(SKILL.md) 返回给 LLM 的 tool result
```

插件逻辑：

```text
read_file(SKILL.md)
  -> parse name/description
  -> query experiences
  -> append "Related Experiences" to this tool result only
```

### Subagent 启动时

适合原因：subagent 是冷启动，最需要携带“类似子任务过去怎么做”的经验。

需要 host 支持：

```ts
before_subagent_start(event: {
  task?: string;
  mission?: string;
  profile?: string;
  childSessionKey?: string;
}) -> { prependContext?: string }
```

如果 host 只有 `subagent_spawned`，那通常已经太晚，只能记录元数据，不能保证改到子 agent 初始 prompt。

### Write 类工具调用前

适合原因：写文件、改文件、删除文件是副作用动作，经验最能减少“写了又改”的情况。

但正确实现必须支持 cancel/replan：

```text
LLM 准备调用 write_file/edit_file
  -> plugin 查经验
  -> 若命中，取消当前 tool call
  -> 把经验作为 user/context message 插入
  -> 重新让 LLM 决定是否还要写、怎么写
```

如果 host 只允许不可变的 `before_tool_call`，那这点不能正确做。继续执行原 tool call 意味着经验来得太晚。

### Cron 重复任务

cron 原理上适合注入 experience，因为任务重复、经验命中率高。

当前 PR 已经支持在 transformContext assemble 中识别：

```text
sessionKey includes ":cron:"
runtimeContext.isCron === true
runtimeContext.automationKind === "cron"
```

识别后 trigger 是 `cron_start`，并绕过普通 task gate。前提是该 cron session 没被 `bypassSessionPatterns` 跳过。不要默认把 cron 加入 bypass。

如果未来 host 暴露 automation name/prompt，query 应该从当前 latest user message 扩展为：

```text
sessionKey
automation name
automation prompt
latest task text
recent failure/success summary
```

## 清理与防污染

经验召回最容易出的问题是自我污染：本轮注入的经验被当成本轮用户输入写回 session，下次 commit 又把它抽成新的经验。

当前 PR 的防线：

1. assemble 前检查 latest user message 是否已有 `<openviking-context>`，有则不重复注入。
2. `sanitizeUserTextForCapture()` 会剥离：
   - `<openviking-context>`
   - 历史 `<relevant-memories>`
3. `afterTurn()` 写 session 前走现有文本清理路径，因此注入块不会写回 OV。
4. long-term recall 过滤 experience URI/category，避免 experience 被长期记忆 section 再注入一次。

## 测试覆盖

当前 PR 的单元测试覆盖：

- 普通知识问答不会触发 experience recall。
- 执行型任务会触发 experience recall。
- cron trigger 会强制 recall。
- experience memory 不会出现在 `Long-term Memories` section。
- 已存在 `<openviking-context>` 会阻止重复注入。
- `<openviking-context>` / 历史 `<relevant-memories>` 会被清理。
- afterTurn 写 session 前会剥离注入块。

本地严格 e2e 脚本保留在工作区，但不进入远端 PR。

## 验收标准

合并前应满足：

- `npm run typecheck` 通过。
- `npm run build` 通过。
- `npm run test` 通过。
- PR diff 不包含 `tests/integration/test_openclaw_openviking_strict_e2e.py`。
- 默认配置下不会触发 experience recall；必须显式设置 `agentExperience.enabled: true`。
- 默认关闭 experience 时，普通长期记忆 auto recall 也使用 `<openviking-context>` 外壳。
- 普通问答不会因为默认配置无脑注入 experience。
- 执行型任务在有相关 experience 命中时注入 `Agent Experiences` section。
- 同一 block 中长期记忆和经验记忆分区清晰。
- 注入块不会被写回 OV session。

## 最终边界

```text
OpenClaw 插件负责：
  - 判断当前 turn 是否值得使用 agent experience
  - 检索 viking://user/memories/experiences
  - 渲染 Agent Experiences section
  - 与 Long-term Memories 共同放入 <openviking-context>
  - 写 session 前清理注入块

OpenViking 服务端负责：
  - session commit
  - trajectory 抽取
  - experience 生成和更新
  - memory vectorization
  - 未来的抽取 policy / world model / skill 演进
```
