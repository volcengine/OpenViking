# OpenClaw Context Engine Integration Design / OpenClaw 上下文引擎集成方案设计

## Context / 背景

本方案讨论在 OpenViking 中集成 OpenClaw Context Engine 的扩展机制，以及围绕新引擎的记忆管理、查询、注入等完整设计。

This proposal discusses the extension mechanism for integrating OpenClaw Context Engine into OpenViking, along with the complete design for memory management, retrieval, and injection around the new engine.

---

## Component 1: Memory Write Mechanism / 组件 1：记忆写入机制

### Compact-Triggered Automatic Write / compact 触发的自动写入

1. 当 compact 时一次性把对话上传到 ov。

   Upload conversation to OpenViking in one batch when compact is triggered.

2. 可选项：compact 时，可以把一些工作记忆（比如 TODO，摘要），留在压缩后的上下文里面（如果有的话），避免断档。

   Optional: During compact, keep some working memories (e.g., TODOs, summaries) in the compressed context (if available) to avoid discontinuity.

3. 由于涉及 agent 记忆的提取，建议把 system prompt 和工具调用也一起上传。

   Since agent memory extraction is involved, it is recommended to also upload the system prompt and tool calls together.

4. 相比于之前模式（每条消息都写入），一次性的写入可以减少记忆提取阶段的 token 消耗，带来的缺点是跨 session 的记忆同步会变慢（不敏感）。

   Compared to the previous mode (writing every message), one-time writing reduces token consumption during memory extraction. The downside is that cross-session memory synchronization will be slower (not sensitive).

5. compact 一般在 /new 或消息达到一定长度时触发，消息未达长度的部分不会触发记忆提取。这部分未来可以加入 timeout 触发提取的机制（当前可以不要）。

   Compact is typically triggered by /new or when messages reach a certain length. Messages that don't reach the length threshold won't trigger memory extraction. A timeout-based extraction mechanism can be added in the future (not needed currently).

---

### Active Memory (Tool-based) / 主动记忆（基于工具）（可选）

这允许 agent 通过 `commit_memory` 工具（或ov cli）主动记录记忆，支持用户请求如：

This allows the agent to actively record memories via a `commit_memory` tool (or ov cli), supporting user requests like:

- "Remember that I like dark mode" / "记住我喜欢深色模式"
- "Don't ask me for confirmation again" / "下次不要再让我确认了"
- "Note that project X is on hold" / "记下项目 X 暂停了"

**commit_memory Tool / 工具**:
- **Purpose / 用途**: Actively commit a memory to long-term storage / 主动将记忆提交到长期存储
- **When to use / 何时使用**: User asks to remember, strong preference, important decision, etc. / 用户要求记住、强烈偏好、重要决定等
- **Parameters / 参数**: `memory_content`, `memory_type`, `priority`, `category`
- **Behavior / 行为**: Immediate extraction and write (no compact delay) / 立即提取和写入（无 compact 延迟）

---

## Component 2: Memory Retrieval / 组件 2：记忆查询/召回

记忆查询分为三部分：

Memory retrieval has three parts:

1. **用户画像注入** - 在会话开始时注入到 system prompt / User Profile Injection - Injected into system prompt at session start
2. **每轮记忆召回** - 为每条用户消息召回相关记忆 / Per-turn Memory Retrieval - Retrieve relevant memories for each user message
3. **Agent主动通过工具调用召回记忆**（TODO） / Agent-initiated memory retrieval via tool calls (TODO)

---

### Part 1: User Profile Injection / 第一部分：用户画像注入

在会话开始时一次性注入到 system prompt。

Injected once at session start into the system prompt.

**Profile Sources / 画像来源**:
- `profile.md` - User's main profile file (always included) / 用户主画像文件（总是包含）
- High-quality memory abstracts (only if quality score >= threshold) / 高质量记忆摘要（TODO：是否加入取决于摘要机制的质量是否ok / TODO: inclusion depends on whether the quality of the abstraction mechanism is acceptable）

---

### Part 2: Per-turn Memory Retrieval / 第二部分：每轮记忆召回

为每条用户消息召回，仅用于该次 LLM 调用。

Retrieved for each user message, only used for that single LLM call.

**Query Construction / 查询构建**: Use last N user messages (default: 5) concatenated as the search query / 使用最近 N 条用户消息（默认：5条）拼接作为搜索查询

**Lightweight Intent Detection / 轻量级意图检测（TODO 可选模块，可基于轻量模型实现 / TODO optional module, can be implemented with lightweight models）**:
- Skip retrieval for greetings ("你好", "在吗", "hi", "hello") / 跳过问候语的召回
- Skip very short messages (<= 3 chars) / 跳过很短的消息（<= 3 字符）

---

**将自动召回的记忆作为模拟的 function call 结果注入，而不是直接注入到 prompt 中**

Inject auto-retrieved memories as simulated function call results instead of directly injecting into the prompt. This is a continuation of Part 2.

**Benefits / 好处**:
1. **Agent Awareness / Agent 感知**: The agent sees that a search was performed, so it knows this pattern exists and can use it itself later / Agent 看到执行了搜索，因此知道这种模式存在，以后可以自己使用
2. **Query Transparency / 查询透明**: The agent sees exactly what query was used, so it can choose different keywords if it searches again / Agent 看到具体使用了什么查询，因此如果再次搜索可以选择不同的关键词

**Example Flow / 示例流程**:
```
User: How do I optimize the database?

Assistant: [Function Call] search_memories({"query": "database optimization...", "max_results": 5})

System: [Function Result] {"success": true, "memories": [...]}

Assistant: Based on...
```

**Retrieval Flow / 召回流程**:
1. Get current user message / 获取当前用户消息
2. Check if should skip retrieval / 检查是否应该跳过召回
3. Build search query from last N user messages / 从最近 N 条用户消息构建搜索查询
4. Search in OpenViking / 在 OpenViking 中搜索
5. Apply relevance threshold filter / 应用相关性阈值过滤
6. Format memories (L0/L1/L2 based on config) / 格式化记忆（根据配置使用 L0/L1/L2）
7. Inject into THIS LLM call only (not persisted) / 仅注入到本次 LLM 调用（不持久化）

---

## Component 3: Agentic Memory Query / 组件 3：Agent 通过工具主动记忆查询

除了每轮自动注入之外，还提供 Agent 发起的查询机制，以满足更复杂的检索需求。这是一种"测试时计算"的方法，用于解决单轮回召可能遗漏的多跳和多背景检索问题。

In addition to per-turn auto-injection, provide an agent-initiated query mechanism to meet more complex retrieval needs. This is a "test-time compute" approach to solve multi-hop and multi-context retrieval problems that single-round retrieval may miss.

### Pre-inject Directory Structure / 预先注入目录结构

为了让主动记忆的路径尽可能短，可以默认把 `ov ls viking://` 的结果预先注入到 system prompt 里面，让 agent 预先知道 ov 里有哪些数据可以用。如果能模拟成是 agent 主动调用的，效果可能更好。

To make the path to active memory as short as possible, pre-inject the results of `ov ls viking://` into the system prompt by default, so the agent knows in advance what data is available in OpenViking. Effect may be better if simulated as an agent-initiated call.

**Design / 设计**:
- At session start / 在会话开始时
- Run `ov ls viking://` (or equivalent) / 运行 `ov ls viking://`（或等效操作）
- Format results as directory tree / 将结果格式化为目录树
- Inject into system prompt, optionally simulate as function call / 注入到 system prompt，可选模拟为 function call

**Example / 示例**:
```
Assistant: [Function Call] ov_ls({"path": "viking://"})

System: [Function Result] {
  "directories": [
    "viking://docs/",
    "viking://user/memories/",
    "viking://agent/skills/",
    "viking://assets/"
  ]
}
```

### When to Use / 何时使用

| Scenario / 场景 | Example / 示例 |
|----------------|---------------|
| **Multi-hop reasoning / 多跳推理** | "What did I say about project X last week, and how does that relate to the Y file we discussed?" |
| **Need for comprehensive context / 需要全面上下文** | "Tell me everything I've said about database optimization" |
| **Temporal queries / 时间查询** | "What decisions did we make in the last month about authentication?" |
| **Cross-session retrieval / 跨会话检索** | "Find my previous conversation about API design patterns" |
| **Directory semantic exploration / 目录语义探索** | "What's in the /docs folder that's relevant to my current task?" |

---

## Component 4: Skill Memory Injection / 组件 4：Skill 记忆注入

Skill 记忆是Agent记忆的一种，区别点在于他锚定一个确定性的Skill

因此可以通过拦截工具调用中的 `read skills/xxx/SKILL.md` 文件调用，在返回结果中添加 skill 记忆的方式注入。

Skill memories are a type of agent memory, with the key distinction that they are anchored to a specific skill.

Therefore, they can be injected by intercepting `read skills/xxx/SKILL.md` file calls in tool calls, and adding skill memories to the returned results.

**Design / 设计**:

1. **Intercept skill file reads / 拦截 skill 文件读取**
   - When agent reads `skills/<skill_name>/SKILL.md` / 当 agent 读取 `skills/<skill_name>/SKILL.md` 时
   - Intercept the read operation / 拦截读取操作
   - Look up skill memories from memory store / 从记忆存储中查找 skill 记忆

2. **Augment skill content / 增强 skill 内容**
   - Prepend/append skill memories to the SKILL.md content / 在 SKILL.md 内容前后添加 skill 记忆
   - Include usage patterns, success tips, common pitfalls, etc. / 包含使用模式、成功技巧、常见陷阱等
   - Keep the original SKILL.md intact / 保持原始 SKILL.md 不变

3. **Skill memory structure / Skill 记忆结构**
   - Usage statistics (how often used, success rate) / 使用统计（使用频率、成功率）
   - Past examples (successful invocations) / 过去的示例（成功调用）
   - Tips and tricks (learned from experience) / 技巧和窍门（从经验中学习）
   - Known issues and workarounds / 已知问题和解决方法

**Example / 示例**:
```
Original SKILL.md:
## Create Presentation
Create a PowerPoint presentation...

Augmented with skill memory:
## Create Presentation (Used 15 times, 93% success)

Create a PowerPoint presentation...

---
## Past Examples
- Successfully created Q3 financial report (2024-03-01)
- Created project kickoff deck (2024-02-15)

## Tips
- User prefers dark theme templates
- Always include executive summary slide
- Use company logo from /assets/logo.png

## Known Issues
- Large images (>10MB) sometimes fail - compress first
```

---

## Component 5: Tool Memory Injection / 组件 5：工具记忆注入

工具记忆可以通过 system prompt 方式注入。

Tool memories can be injected via system prompt.

**Design / 设计**:

1. **Inject into system prompt / 注入到 system prompt**
   - At the start of each session or turn / 在每个会话或轮次开始时
   - Include tool usage memories / 包含工具使用记忆
   - Keep it concise to avoid token bloat / 保持简洁以避免 token 膨胀

2. **Tool memory content / 工具记忆内容**
   - Tool usage statistics (call count, success rate, average time) / 工具使用统计（调用次数、成功率、平均时间）
   - Common parameter patterns / 常见参数模式
   - Error patterns and how to avoid them / 错误模式和如何避免
   - Best practices learned / 学到的最佳实践

3. **Format / 格式**
   - Structured, easy to parse / 结构化，易于解析
   - Priority-based (most important first) / 基于优先级（最重要的在前）
   - Include only high-value insights / 仅包含高价值洞察

**Example / 示例**:
```
## Tool Usage Memories

### run_shell
- Called 42 times, 88% success rate
- Average time: 2.3s
- Common issues:
  - Forgetting to use `cd` before relative paths
  - Long-running commands need `--async` flag
- Best practice: Always use `&&` for chained commands

### edit_file
- Called 156 times, 95% success rate
- Best practice: Use `search_replace` instead of full rewrite when possible
```

---

## Appendix: OpenViking Tool Injection / 附录：OpenViking 工具注入

本节讨论如何将 OpenViking 能力注入到 Agent 中。提出了两种方案，都避免了基于 skill 的注入模式。

This section discusses how to inject OpenViking capabilities into the agent. Two options are proposed, both avoiding the skill-based injection pattern.

### Problem with Skill-based Injection / 基于 Skill 注入的问题

- **Unstable triggering / 触发不稳定**
- **Competition with other skills / 需要和其他 skill 竞争**
- **Hard to predict when it will be used / 难以预测何时会被使用**

---

### Option 1: System Prompt + Bash CLI (Recommended if CLI is LLM-friendly) / 方案 1：System Prompt + Bash CLI（如果 CLI 对 LLM 友好，推荐此方案）

直接将 OpenViking CLI 用法说明注入到 system prompt 中。Agent 使用内置的 bash 工具调用 `ov` 命令。

Inject OpenViking CLI usage instructions directly into the system prompt. The agent uses the built-in bash tool to call `ov` commands.

**Advantages / 优势**:
- No tool definition needed / 不需要定义 tool
- Uses agent's existing bash capabilities / 使用 Agent 已有的 bash 能力
- More flexible (agent can compose commands) / 更灵活（Agent 可以组合命令）
- Works well if CLI is simple and intuitive / 如果 CLI 简单直观，效果很好

**Requirements / 要求**:
- CLI must be LLM-friendly (simple commands, good help text) / CLI 必须对 LLM 友好（命令简单、帮助文本完善）
- Predictable output format (JSON by default) / 可预测的输出格式（默认 JSON）
- Clear, self-documenting commands / 清晰、自解释的命令

**Example Commands / 示例命令**:
```bash
ov search --query "your query" [--category <category>] [--limit N]
ov ls memories [--category <category>]
ov search-docs --query "your query" [--path <directory>]
ov history [--limit N]
ov remember --content "what to remember" [--type <type>] [--priority N]
```

---

### Option 2: Tool Definition Injection / 方案 2：工具定义注入

将 OpenViking 能力定义为显式的工具定义（function calling）。

Define OpenViking capabilities as explicit tool definitions (function calling).

**Advantages / 优势**:
- More predictable triggering / 触发更可预测
- Structured input validation / 结构化输入验证
- Clear separation from bash usage / 与 bash 用法清晰分离
- Works even if agent doesn't have bash access / 即使 Agent 没有 bash 访问权限也能工作

**Disadvantages / 劣势**:
- Need to maintain tool definitions / 需要维护工具定义
- Less flexible than free-form bash / 不如自由形式的 bash 灵活
- More verbose for complex operations / 复杂操作更冗长

---

### Comparison / 对比

| Aspect / 方面 | Option 1: System Prompt + Bash / 方案 1：System Prompt + Bash | Option 2: Tool Definition / 方案 2：工具定义 |
|--------------|----------------------------------------------------------------|----------------------------------------------|
| **Trigger Stability / 触发稳定性** | Depends on bash tool reliability / 取决于 bash 工具可靠性 | More predictable / 更可预测 |
| **Flexibility / 灵活性** | High - can compose commands / 高 - 可以组合命令 | Lower - fixed schema / 较低 - 固定 schema |
| **Maintenance / 维护成本** | Maintain CLI help text / 维护 CLI 帮助文本 | Maintain tool definitions / 维护工具定义 |
| **LLM Friendliness / LLM 友好度** | Requires good CLI design / 需要好的 CLI 设计 | Explicit schema helps / 显式 schema 有帮助 |
| **Bash Required / 需要 Bash** | Yes / 是 | No / 否 |

---

### Recommendation / 建议

**Primary Recommendation: Option 1 (CLI + Bash) if CLI can be made LLM-friendly**

**主要建议：如果 CLI 能做到对 LLM 友好，选择方案 1（CLI + Bash）**

Why / 为什么：
1. More flexible for power users / 对高级用户更灵活
2. Single source of truth (CLI works for humans and agents) / 单一事实来源（CLI 对人类和 Agent 都有效）
3. Less code to maintain (no duplicate tool definitions) / 维护代码更少（没有重复的工具定义）
4. Agents can discover and experiment with commands / Agent 可以发现和实验命令

**Fallback: Option 2 (Tool Definition) if CLI can't be simplified enough**

**备选方案：如果 CLI 无法足够简化，选择方案 2（工具定义）**

---
