# Memory Extractor Templating and Update Mechanism Optimization / 记忆抽取模版与更新机制优化

## Context / 上下文

### Problem Background / 问题背景

- [#578](https://github.com/volcengine/OpenViking/issues/578) "[Feature]: 允许提示词模板自定义添加和指定" - Current OpenViking memory_extractor uses 8 fixed memory categories (profile, preferences, entities, events, cases, patterns, tools, skills), adding new memory types requires modifying multiple core code files, prompt templates and merge logic are hard-coded. This design implements the custom template capability requested in #578. / [#578](https://github.com/volcengine/OpenViking/issues/578) "[Feature]: 允许提示词模板自定义添加和指定" - 当前 OpenViking 的 memory_extractor 使用 8 个固定的记忆类别（profile, preferences, entities, events, cases, patterns, tools, skills），新增记忆类型需要修改多处核心代码，提示模板和合并逻辑都是硬编码的。本设计实现了 #578 中要求的自定义模板能力。
- User memory filenames use random ID encoding, not semantic, wasting the navigation value of filenames / 用户记忆文件名采用随机 ID 编码，没有语义化，浪费了文件名的导航价值
- After extraction, merging is still required, involving multiple LLM calls, low efficiency / 抽取之后还需要做合并，涉及多次 LLM 调用，效率较低

### Goals / 目标

Implement a memory templating system based on OpenViking storage, such that: / 实现基于 OpenViking 存储的记忆模版化系统，使得：
1. Design memory templating mechanism, can add new memory types without modifying core code / 设计记忆模版机制，无需修改核心代码即可添加新的记忆类型
2. Maintain backward compatibility (existing 8 categories continue to work) / 保持向后兼容（现有 8 个类别继续工作）
3. Adopt ReAct (Reasoning + Action) pattern for memory updates, update all memories in one ReAct-based LLM call / 采用 ReAct (Reasoning + Action) 模式进行记忆更新，一次基于 ReAct 的 LLM 调用更新所有记忆
4. Adopt semantic filenames, fully utilize the navigation value of filenames / 采用语义化文件名，充分利用文件名的导航价值
5. Learn from commercial memory library implementations, adopt patch incremental update mechanism (including abstract and overview), reduce LLM call count / 借鉴商业化记忆库的实现，采用 patch 增量更新机制（包括abstract和overview），减少 LLM 调用次数

### Reference Design / 参考设计

This design references the following core ideas from the `../memory` project: / 本设计参考了 `../memory` 项目的以下核心思想：
- Memory templating (OpenViking's memory template is a subset of the commercial version, preserving the possibility of future upgrade to commercial version) / 记忆模版化（openviking的记忆模版是商业化版本的一个子集，保留未来升级到商业化版本的可能性）
- Three-operation Schema (write/edit/delete) / 三种操作 Schema（write/edit/delete）
- RoocodePatch patch protocol (patch + replace) / RoocodePatch patch协议（patch + replace）

---

## Two Memory Modes / 两种记忆模式

The system provides two memory modes, distinguished by whether `content_template` exists. / 系统提供两种记忆模式，用是否有 `content_template` 来区分。

### Simple Mode (without content_template) / 模式一：简单模式（无 content_template）

**Use Cases**: profile, preferences, entities, events, cases, patterns / 适用场景：profile、preferences、entities、events、cases、patterns

**Characteristics**: / 特点：
- Only `name`, `content` two fields (or a few simple fields) / 只有 `name`、`content` 两个字段（或简单的几个字段）
- `content` is Markdown content, no rendering needed / `content` 就是 Markdown 内容，不需要渲染
- No `MEMORY_FIELDS` comment needed / 不需要 `MEMORY_FIELDS` 注释
- Directly use patch to incrementally update content during updates / 更新时直接用 patch 增量更新 content

**Config Examples** / **配置示例**：

**profile.yaml**
```yaml
name: profile
description: |
  User profile memory - captures "who the user is" as a person.
  Extract relatively stable personal attributes that define the user's identity, work style, and preferences.
  Include: profession, experience level, technical background, communication style, work habits, etc.
  Do NOT include transient conversation content or temporary mood states.
directory: "viking://user/{user_space}/memories"
filename_template: "profile.md"

fields:
  - name: content
    type: string
    description: |
      User profile content describing "who the user is".
      Includes relatively stable personal attributes: profession, experience, tech stack, communication style, etc.
      Example: "User is an AI development engineer with 3 years of LLM application development experience, mainly using Python and LangChain tech stack. Communication style is concise and direct, prefers efficient code implementation."
    merge_op: patch
```

**preferences.yaml**
```yaml
name: preferences
description: |
  User preference memory - captures "what the user likes/dislikes or is accustomed to".
  Extract specific preferences the user has expressed across conversations.
  Each preference should be about a specific topic (not generic).
  Topics can be: code style, communication style, tools, workflow, food, commute, etc.
  Store different topics as separate memory files, do NOT mix unrelated preferences.
directory: "viking://user/{user_space}/memories/preferences"
filename_template: "{topic}.md"

fields:
  - name: topic
    type: string
    description: |
      Preference topic used to uniquely identify this preference memory.
      Should be a semantic topic description such as "Python code style", "Communication style", "Food preference", "Commute preference", etc.
      Different preference topics should be stored as separate memories, do not mix unrelated preferences.
    merge_op: immutable

  - name: content
    type: string
    description: |
      Specific preference content describing "what the user prefers/is accustomed to".
      Example: "User has shown clear preferences for Python code style in multiple conversations: dislikes using type hints, considers them redundant; requires concise function comments, limited to 1-2 lines; prefers direct implementation, avoids excessive fallbacks and over-engineering."
    merge_op: patch
```

**entities.yaml**
```yaml
name: entities
description: |
  Entity memory - captures "what this named thing is, what properties it has".
  Extract information about specific entities mentioned in conversation.
  Entity types include: projects, people, organizations, systems, technologies, concepts, products, etc.
  Each entity is a named thing that has attributes worth remembering for future conversations.
  Store each entity as a separate memory file, keyed by entity name.
directory: "viking://user/{user_space}/memories/entities"
filename_template: "{entity_name}.md"

fields:
  - name: entity_name
    type: string
    description: |
      Entity name used to uniquely identify this entity memory.
      Should be the specific name of the entity such as "OpenViking project", "Alice (colleague)", "Redis", etc.
    merge_op: immutable

  - name: entity_type
    type: string
    description: |
      Entity type describing what type of entity this is.
      Possible values: project, person, organization, system, technology, concept, etc.

  - name: content
    type: string
    description: |
      Detailed entity content describing "what this named thing is, what properties it has".
      Includes: basic information, core attributes, status, etc.
      Example: "OpenViking is an AI Agent long-term memory management system the user is developing. The project uses Python and AGFS tech stack, core features include memory extraction, deduplication, and retrieval. Currently in active development, goal is to build Claude-like long-term memory capabilities."
    merge_op: patch
```

**events.yaml**
```yaml
name: events
description: |
  Event memory - captures "what happened, what decision was made, and why".
  Extract notable events, decisions, milestones, and turning points from the conversation.
  Events should be things worth remembering for future context: decisions made, agreements reached, milestones achieved, problems solved, etc.
  Each event should include: what happened, why it happened, what the outcome was, and any relevant context/timeline.
  Use absolute dates for event_time, not relative time like "today" or "recently".
directory: "viking://user/{user_space}/memories/events"
filename_template: "{event_time}_{event_name}.md"

fields:
  - name: event_name
    type: string
    description: |
      Event name used to uniquely identify this event memory.
      Should be a specific event description such as "Decided to refactor memory system", "Started OpenViking project", "Completed Q3 review", etc.
      Example: "[Action]: [Description]"
    merge_op: immutable

  - name: event_time
    type: string
    description: |
      Time when the event occurred, use absolute time format, do not use relative time (such as "today", "recently").
      Can be empty if time is unknown.
      Example: "2026-03-17"
    merge_op: immutable

  - name: content
    type: string
    description: |
      Detailed event content describing "what happened".
      Includes: decision content, reasons, results, background, timeline, etc.
      Example: "During memory system design discussion, found that the original 6 categories had blurry boundaries. Especially states, lessons, insights often overlapped and were hard to distinguish. Decided to refactor to 5 categories, removing these three to make classification boundaries clearer."
    merge_op: patch
```

**cases.yaml**
```yaml
name: cases
description: |
  Case memory - captures "what problem was encountered and how it was solved".
  Extract specific problem-solution pairs from the conversation that are worth remembering for future reference.
  Cases should be about specific problems that have clear solutions.
  Each case should include: what the problem was (symptoms, error messages, context), what the solution was (steps taken, principles used), and why it worked.
  Case names should be in "Problem → Solution" format to make them easily searchable.
directory: "viking://agent/{agent_space}/memories/cases"
filename_template: "{case_name}.md"

fields:
  - name: case_name
    type: string
    description: |
      Case name used to uniquely identify this case memory.
      Should be in "Problem → Solution" format such as "Band not recognized → Request member/album/style details", "Memory merge timeout → Split into smaller chunks", etc.
      Example: "[Problem] → [Solution]"
    merge_op: immutable

  - name: problem
    type: string
    description: |
      Problem description specifically detailing what problem was encountered.
      Includes: error messages, symptoms, context, and other specific details.
      Example: "User feedback that a band cannot be recognized by system."
    merge_op: patch

  - name: solution
    type: string
    description: |
      Solution description detailing how to solve this problem.
      Includes: solution method, steps, principles, etc.
      Example: "Request user to provide more identification details: band member names, representative album names, music style, etc. This information can improve recognition accuracy."
    merge_op: patch

  - name: content
    type: string
    description: |
      Complete case content including full narrative of problem and solution.
      Example: "User feedback mentioned a band that the system could not recognize. Solution is to request user to provide more identification details: band member names, representative album names, music style, etc. This information can improve recognition accuracy."
    merge_op: patch
```

**patterns.yaml**
```yaml
name: patterns
description: |
  Pattern memory - captures "under what circumstances to follow what process".
  Extract reusable workflows, processes, and methods that the agent should follow in similar future situations.
  Patterns should be about: how to approach certain types of tasks, what steps to follow, what considerations to keep in mind.
  Each pattern should include: trigger conditions (when to use this pattern), process steps (what to do), and considerations (what to watch out for).
  Pattern names should be in "Process name: Step description" format.
directory: "viking://agent/{agent_space}/memories/patterns"
filename_template: "{pattern_name}.md"

fields:
  - name: pattern_name
    type: string
    description: |
      Pattern name used to uniquely identify this pattern memory.
      Should be in "Process name: Step description" format such as "Teaching topic handling: Outline→Plan→Generate PPT", "Code refactoring: Understand→Test→Refactor→Verify", etc.
      Example: "[Pattern name]: [Step sequence]"
    merge_op: immutable

  - name: pattern_type
    type: string
    description: |
      Pattern type describing what type of pattern this is.
      Possible values: workflow, method, process, etc.

  - name: content
    type: string
    description: |
      Detailed pattern content describing "under what circumstances to follow what process".
      Includes: trigger conditions, process steps, considerations, etc.
      Example: "When user requests teaching content for a topic, use a four-step process: first list the topic outline to understand overall structure; then create a detailed learning plan; next generate PPT framework; finally refine specific content for each section. This process ensures content is systematic and complete."
    merge_op: patch
```

**Memory File Storage Format Example** / **记忆文件存储格式示例**：
```markdown
# User Profile

User is an AI development engineer with 3 years of experience...
```

---

### Template Mode (with content_template) / 模式二：模板模式（有 content_template）

**Use Cases**: tools, skills / 适用场景：tools、skills

**Characteristics**: / 特点：
- Has multiple structured fields (including statistical fields like usage count, success rate, etc.) / 有多个结构化字段（包括统计字段如使用次数、成功率等）
- `content` is rendered from fields via `content_template` / `content` 是通过 `content_template` 从字段渲染出来的
- `MEMORY_FIELDS` JSON comment is always placed at the end of the file / `MEMORY_FIELDS` JSON 注释永远放在文件最后
- Update workflow: / 更新流程：
  1. Parse JSON fields from `<!-- MEMORY_FIELDS -->` comment at the end of Markdown file / 从 Markdown 文件最后的 `<!-- MEMORY_FIELDS -->` 注释中解析 JSON 字段
  2. Perform memory updates according to field's `merge_op` / 根据字段的 `merge_op` 做记忆更新
  3. Re-render Markdown content via `content_template` after update / 更新后通过 `content_template` 重新渲染 Markdown 内容
  4. Write updated `MEMORY_FIELDS` back to end of file / 把更新后的 `MEMORY_FIELDS` 写回文件最后

**Config Examples** / **配置示例**：

**tools.yaml**
```yaml
name: tools
description: |
  Tool usage memory - captures "how this tool is used, what works well, and what doesn't".
  Extract tool usage patterns, statistics, and learnings from [ToolCall] records and conversation context.
  For each tool, track: how many times it's been called, success rate, average time/tokens, what it's best for, optimal parameters, common failure modes, and actionable recommendations.
  Also accumulate complete guidelines with "Good Cases" and "Bad Cases" examples.
  Tool memories help the agent learn from experience and use tools more effectively over time.
directory: "viking://agent/{agent_space}/memories/tools"
filename_template: "{tool_name}.md"

content_template: |
  Tool: {tool_name}

  Static Description:
  "{static_desc}"

  Tool Memory Context:
  Based on {total_calls} historical calls:
  - Success rate: {success_rate}% ({success_count} successful, {fail_count} failed)
  - Avg time: {avg_time}, Avg tokens: {avg_tokens}
  - Best for: {best_for}
  - Optimal params: {optimal_params}
  - Common failures: {common_failures}
  - Recommendation: {recommendation}

  {guidelines}

fields:
  - name: tool_name
    type: string
    description: |
      Tool name, copied exactly from [ToolCall] records without modification.
      Used to uniquely identify this tool memory.
      Examples: "web_search", "read_file", "execute_code"
    merge_op: immutable

  - name: static_desc
    type: string
    description: |
      Static description of the tool, basic functionality description.
      Examples: "Searches the web for information", "Reads files from the file system"

  - name: total_calls
    type: int64
    description: |
      Total number of tool calls, accumulated from historical statistics.
      Used to calculate success rate and average duration.
    merge_op: sum

  - name: success_count
    type: int64
    description: |
      Number of successful tool calls, accumulated from historical statistics.
      Counts calls with status "completed".
    merge_op: sum

  - name: fail_count
    type: int64
    description: |
      Number of failed tool calls, accumulated from historical statistics.
      Counts calls with status not "completed".
    merge_op: sum

  - name: total_time_ms
    type: int64
    description: |
      Total tool call duration in milliseconds, accumulated from historical statistics.
      Used to calculate average duration.
    merge_op: sum

  - name: total_tokens
    type: int64
    description: |
      Total tokens used by tool calls (prompt tokens + completion tokens), accumulated from historical statistics.
      Used to calculate average token consumption.
    merge_op: sum

  - name: best_for
    type: string
    description: |
      Best use cases for the tool, describing in what scenarios this tool works best.
      Examples: "Technical documentation, tutorials, API references"
    merge_op: patch

  - name: optimal_params
    type: string
    description: |
      Optimal parameter range/best practices for the tool, describing general parameter optimization suggestions.
      Should describe general best practices (such as "max_results=5-20", "timeout>30s for large files"),
      do not describe specific case values (such as "command: 'echo hello'").
      Examples: "max_results: 5-20 (larger values may timeout); language: 'en' for better results; query: specific multi-word phrases with qualifiers"
    merge_op: patch

  - name: common_failures
    type: string
    description: |
      Common failure modes of the tool, describing problems and error patterns this tool frequently encounters.
      Examples: "Single-word queries return irrelevant results; max_results>50 causes timeout; non-English queries have lower quality"
    merge_op: patch

  - name: recommendation
    type: string
    description: |
      Actionable recommendations for tool usage, short actionable recommendations.
      Examples: "Use specific multi-word queries like 'Python asyncio tutorial'; add qualifiers like 'guide', 'docs', 'example'"
    merge_op: patch

  - name: guidelines
    type: string
    description: |
      Tool usage guidelines, complete usage guide content.
      Must include exact English headings:
      - "## Guidelines" - best practices
      - "### Good Cases" - successful usage examples
      - "### Bad Cases" - failed usage examples
      Headings must be in English, content can be in target language.
    merge_op: patch
```

**skills.yaml**
```yaml
name: skills
description: |
  Skill execution memory - captures "how this skill is executed, what works well, and what doesn't".
  Extract skill execution patterns, statistics, and learnings from skill usage in conversation.
  For each skill, track: how many times it's been executed, success rate, what it's best for, recommended execution flow, key dependencies, common failure modes, and actionable recommendations.
  Also accumulate complete guidelines with "Good Cases" and "Bad Cases" examples.
  Skill memories help the agent learn from experience and execute skills more effectively over time.
directory: "viking://agent/{agent_space}/memories/skills"
filename_template: "{skill_name}.md"

content_template: |
  Skill: {skill_name}

  Skill Memory Context:
  Based on {total_executions} historical executions:
  - Success rate: {success_rate}% ({success_count} successful, {fail_count} failed)
  - Best for: {best_for}
  - Recommended flow: {recommended_flow}
  - Key dependencies: {key_dependencies}
  - Common failures: {common_failures}
  - Recommendation: {recommendation}

  {guidelines}

fields:
  - name: skill_name
    type: string
    description: |
      Skill name, copied exactly from [ToolCall] if skill_name is present, otherwise inferred from conversation context.
      Used to uniquely identify this skill memory.
      Examples: "create_presentation", "analyze_code", "write_document"
    merge_op: immutable

  - name: total_executions
    type: int64
    description: |
      Total number of skill executions, accumulated from historical statistics.
      Used to calculate success rate.
    merge_op: sum

  - name: success_count
    type: int64
    description: |
      Number of successful skill executions, accumulated from historical statistics.
      Counts successful executions.
    merge_op: sum

  - name: fail_count
    type: int64
    description: |
      Number of failed skill executions, accumulated from historical statistics.
      Counts failed executions.
    merge_op: sum

  - name: best_for
    type: string
    description: |
      Best use cases for the skill, describing in what scenarios this skill works best.
      Examples: "Slide creation tasks with clear topic and target audience"
    merge_op: patch

  - name: recommended_flow
    type: string
    description: |
      Recommended execution flow for the skill, describing the best steps to execute this skill.
      Examples: "1. Confirm topic and audience → 2. Collect reference materials → 3. Generate outline → 4. Create slides → 5. Refine content"
    merge_op: patch

  - name: key_dependencies
    type: string
    description: |
      Key dependencies/prerequisites for the skill, describing what prerequisites and inputs are needed to execute this skill.
      Examples: "Clear topic (e.g., 'Q3 project update', 'Python tutorial'); Target audience (e.g., 'executives', 'beginners'); Reference materials (optional but recommended)"
    merge_op: patch

  - name: common_failures
    type: string
    description: |
      Common failure modes of the skill, describing problems and error patterns this skill frequently encounters.
      Examples: "Vague topic like 'make a PPT' leads to multiple rework cycles; Missing audience info causes style mismatch; No reference materials results in generic content"
    merge_op: patch

  - name: recommendation
    type: string
    description: |
      Actionable recommendations for skill usage, short actionable recommendations.
      Examples: "Always confirm topic and audience before starting; Collect 2-3 reference materials for better quality"
    merge_op: patch

  - name: guidelines
    type: string
    description: |
      Skill usage guidelines, complete usage guide content.
      Must include exact English headings:
      - "## Guidelines" - best practices
      - "### Good Cases" - successful usage examples
      - "### Bad Cases" - failed usage examples
      Headings must be in English, content can be in target language.
    merge_op: patch
```

**Memory File Storage Format Example** / **记忆文件存储格式示例**：
```markdown
Tool: web_search

Static Description:
"Searches the web for information"

Tool Memory Context:
Based on 100 historical calls:
- Success rate: 92.0% (92 successful, 8 failed)
- Avg time: 1.2s, Avg tokens: 1500
- Best for: Technical documentation, tutorials, API references
- Optimal params: max_results=5-20, timeout>30s
- Common failures: Single-word queries return irrelevant results
- Recommendation: Use specific multi-word queries

## Guidelines
...

### Good Cases
...

### Bad Cases
...

<!-- MEMORY_FIELDS
{
  "tool_name": "web_search",
  "static_desc": "Searches the web for information",
  "total_calls": 100,
  "success_count": 92,
  "fail_count": 8,
  "total_time_ms": 120000,
  "total_tokens": 150000,
  "best_for": "Technical documentation, tutorials, API references",
  "optimal_params": "max_results=5-20, timeout>30s",
  "common_failures": "Single-word queries return irrelevant results",
  "recommendation": "Use specific multi-word queries",
  "guidelines": "## Guidelines\n...\n\n### Good Cases\n...\n\n### Bad Cases\n..."
}
-->
```

---

## Overall Architecture / 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│              Phase 0: Pre-fetch (System)                       │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  System automatically executes before LLM reasoning:      │  │
│  │  1. ls: Get all memory directory structures               │  │
│  │  2. read: Read all .abstract.md (L0) and .overview.md (L1)│  │
│  │  3. search: Perform one semantic search in all directories│  │
│  │  Output: Pre-fetched Context (dir + L0/L1 + search results)│  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│          Phase 1: Reasoning + Action (LLM + Optional Reads)    │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  LLM: Analyze conversation + Pre-fetched Context          │  │
│  │  Output: Reasoning + Actions (optional additional reads)   │  │
│  │  - Reasoning: What memories need to change               │  │
│  │  - Actions: Additional read operations (only if needed)   │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│          Phase 2: Generate Operations (Final Output)            │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  LLM: Generate final operations based on existing memories  │  │
│  │  Output: MemoryOperations                                    │  │
│  │  - WriteOp: New memory data (create or full replace)       │  │
│  │  - EditOp: patch (SEARCH/REPLACE) incremental update       │  │
│  │  - DeleteOp: URI to delete                                  │  │
│  │  [Note] This is model's final output, directly executed    │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│              Phase 3: System Execute                              │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  MemoryUpdater: Directly execute MemoryOperations          │  │
│  │  - Write to OpenViking Storage (L0/L1/L2)                 │  │
│  │  - Update VikingDB vector index                             │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Core Components Design / 核心组件设计

### 1. Memory Data Structures / 记忆数据结构

#### 1.1 MemoryField (Memory Field Definition / 记忆字段定义)

Field properties / 字段属性:
- name: Field name / 字段名称
- field_type: Field type (string/int64/float32/bool) / 字段类型（string/int64/float32/bool）
- description
- merge_op: Merge operation (patch/sum/avg/immutable), default patch / 合并操作（patch/sum/avg/immutable），默认 patch
  - patch: Default, SEARCH/REPLACE incremental update / 默认，SEARCH/REPLACE 增量更新
  - sum: Sum (numeric fields like total_calls, success_count) / 累加（数字字段，如 total_calls、success_count）
  - avg: Average (calculated from sum fields, non-storage field) / 平均值（从 sum 字段计算，非存储字段）
  - immutable: Immutable after initial generation (for filename-related fields) / 初次生成后不可变（用于文件名相关字段）

**Special fields** / **特殊字段**:
- `content`: Default Markdown content field, only this field is used in simple mode / 默认的 Markdown 内容字段，简单模式下只用这个字段
- `abstract`: L0 summary field (one-sentence summary for indexing) / L0 摘要字段（用于索引的一句话摘要）
- `overview`: L1 overview field (structured Markdown summary) / L1 概览字段（结构化 Markdown 摘要）

#### 1.2 MemoryType (Memory Type Definition / 记忆类型定义)

Type properties / 类型属性:
- name: Type name, e.g., "preferences", "tools" / 类型名称，如 "preferences", "tools"
- description
- directory: Full URI for memory data storage, e.g., "viking://user/{user_space}/memories/preferences" / 记忆数据存放的完整 URI，如 "viking://user/{user_space}/memories/preferences"
- fields: MemoryField list / MemoryField 列表
- filename_template: Filename generation template, e.g., "{name}_{topic}.md" / 文件名生成模板，如 "{name}_{topic}.md"
- content_template: Content rendering template (supports field placeholders), used to render Markdown content from fields / 内容渲染模板（支持字段占位符），用于从 fields 渲染 Markdown 内容

**content_template example** (for tools type) / **content_template 示例**（用于 tools 类型）:
```yaml
content_template: |
  # Tool: {tool_name}

  Tool Memory Context:
  Based on {total_calls} historical calls:
  - Success rate: {success_rate}% ({success_count} successful, {fail_count} failed)
  - Best for: {best_for}
  - Common failures: {common_failures}
  - Recommendation: {recommendation}

  {content}
```

#### 1.3 MemoryData (Dynamic Memory Data / 动态记忆数据)

Data properties / 数据属性:
- memory_type: Memory type name / 记忆类型名称
- uri: Memory URI (provided during update) / 记忆 URI（更新时提供）
- fields: Dynamic field data (Dict) / 动态字段数据（Dict）
- abstract/overview/content: L0/L1/L2 content / L0/L1/L2 内容
- name/tags/created_at/updated_at: Metadata / 元数据

### 2. ReAct Flow Data Structures / ReAct 流程数据结构

#### 2.1 Reasoning + Action (Phase 1: Reasoning Output / 阶段 1: 推理输出)

```
ReasoningAction:
  - reasoning: str  # LLM's thinking string (natural language description)
  - memory_changes: List[MemoryChange]  # Memories that need changes
  - actions: List[ReadAction]             # Read operations to execute

MemoryChange:
  - change_type: write/edit/delete
  - memory_type: Memory type
  - uri: Memory URI (if known)
  - reason: Reason for change

ReadAction:
  - action_type: read/find/ls
  - params: Dict  # Call parameters
```

**Available Tools** (consistent with OpenViking VikingFS API) / **可使用的工具**（与 OpenViking VikingFS API 保持一致）:

**read**
- Parameters / 参数: `uri: str, offset: int = 0, limit: int = -1`
- Returns / 返回: Memory file content (str) / 记忆文件内容（str）
- Description / 说明: Read single file, offset is start line number (0-indexed), limit is number of lines to read, -1 means read to end / 读取单个文件，offset 为起始行号（0-indexed），limit 为读取行数，-1 表示读取到末尾

**find**
- Parameters / 参数: `query: str, target_uri: str = "", limit: int = 10, score_threshold: Optional[float] = None, filter: Optional[Dict] = None`
- Returns / 返回: FindResult (includes memories, resources, skills) / FindResult（包含 memories, resources, skills）
- Description / 说明: Semantic search, target_uri is target directory URI / 语义搜索，target_uri 为目标目录 URI

**ls**
- Parameters / 参数: `uri: str, output: str = "agent", abs_limit: int = 256, show_all_hidden: bool = False, node_limit: int = 1000`
- Returns / 返回: List[Dict] (directory entry list) / List[Dict]（目录条目列表）
- Description / 说明: List directory content, includes abstract field when output="agent" / 列出目录内容，output="agent" 时包含 abstract 字段

**tree**
- Parameters / 参数: `uri: str = "viking://", output: str = "agent", abs_limit: int = 256, show_all_hidden: bool = False, node_limit: int = 1000, level_limit: int = 3`
- Returns / 返回: List[Dict] (recursive directory tree) / List[Dict]（递归目录树）
- Description / 说明: Recursively list all content, includes abstract field when output="agent", level_limit controls traversal depth / 递归列出所有内容，output="agent" 时包含 abstract 字段，level_limit 控制遍历深度

**Important** / **重要**: No function calls like add_memory/update_memory/delete_memory, add/edit/delete operations are model's final output as MemoryOperations, directly executed by system / 没有 add_memory/update_memory/delete_memory 这样的 function call，增删改操作通过模型最终输出 MemoryOperations，由系统直接执行

#### 2.2 MemoryOperations (Phase 2: Final Output / 阶段 2: 最终输出)

```
MemoryOperations:
  - write_operations: List[WriteOp]
  - edit_operations: List[EditOp]
  - delete_operations: List[DeleteOp]

WriteOp:
  - uri: Target memory URI / 目标记忆的 URI
  - memory_data: MemoryData

EditOp:
  - uri: Target memory URI / 目标记忆的 URI
  - patches: Field-level updates / 字段级更新

DeleteOp:
  - uri: Memory URI to delete / 要删除的记忆 URI
```

### 3. Patch Handler / Patch 处理器

#### 3.1 Content-level Patch (SEARCH/REPLACE) / 内容级 Patch (SEARCH/REPLACE)

```
Patch format / Patch 格式:
<<<<<<< SEARCH
:start_line:10
-------
Original content / 原始内容
=======
New content / 新内容
>>>>>>> REPLACE
```

#### 3.2 Field-level Patch / 字段级 Patch

Handle according to field's merge_op / 根据字段的 merge_op 处理:
- patch: Default, SEARCH/REPLACE incremental update / 默认，SEARCH/REPLACE 增量更新
- sum: Sum (numeric fields like total_calls, success_count) / 累加（数字字段，如 total_calls、success_count）
- avg: Average (calculated from sum fields, non-storage field) / 平均值（从 sum 字段计算，非存储字段）

### 4. MemoryTypeRegistry (Type Registry / 类型注册表)

Features / 功能:
- register(memory_type): Register memory type / 注册记忆类型
- get(name): Get memory type / 获取记忆类型
- list_all(): List all types / 列出所有类型
- load_from_dir(dir_path): Load all MemoryType YAML files from directory (one type per file) / 从目录加载所有 MemoryType YAML 文件（每个文件一个类型）
  - Default load built-in types from `openviking/session/memory/schemas/` / 默认从 `openviking/session/memory/schemas/` 加载内置类型

### 5. MemoryReActOrchestrator (ReAct Orchestrator / ReAct 编排器)

#### Workflow / 工作流:

**Optimization Strategy** / **优化策略**: To avoid excessive time from multiple ReAct rounds, system automatically performs pre-fetch before LLM reasoning / 为避免多次 ReAct 导致耗时过长，系统在 LLM 推理前自动执行前置读取:

**Phase 0: Pre-fetch (system executes, before LLM reasoning)** / **阶段 0: Pre-fetch（系统执行，LLM 推理前）**:
1. **ls**: Get all memory directory structures / 获取所有记忆目录结构
   - `viking://user/{user_space}/memories/` and subdirectories / 及子目录
   - `viking://agent/{agent_space}/memories/` and subdirectories / 及子目录
2. **read**: Read all `.abstract.md` (L0) and `.overview.md` (L1) / 读取所有 `.abstract.md` (L0) 和 `.overview.md` (L1)
   - These summary files provide memory overview with small size / 这些摘要文件提供记忆的概览信息，体积小
3. **search**: Perform one semantic search in all directories / 在所有目录执行一次语义搜索
   - Use current conversation as query / 使用当前对话作为查询
   - Return list of relevant memory URIs / 返回相关的记忆 URI 列表

**Phase 1: Reasoning + Action**: LLM analyzes conversation + Pre-fetched Context, outputs ReasoningAction / LLM 分析对话 + Pre-fetched Context，输出 ReasoningAction
   - reasoning: LLM's thinking string (natural language description) / LLM 的 thinking 字符串（自然语言描述）
   - memory_changes: Memories that need changes (write/edit/delete) / 需要变更的记忆（write/edit/delete）
   - actions: Additional read operations (only execute when needed, e.g., read specific L2 content) / 额外的读取操作（仅在需要时执行，如读取具体 L2 内容）
   - System executes additional read operations (if needed) / 系统执行额外读取操作（如需要）

**Phase 2: Generate Operations**: LLM generates MemoryOperations based on existing memories (final output) / LLM 基于现有记忆生成 MemoryOperations（最终输出）

**Phase 3: System Execute**: System directly executes MemoryOperations / 系统直接执行 MemoryOperations

### 6. MemoryUpdater (Patch Applier - System Execution / Patch 应用器 - 系统执行)

Features / 功能:
- apply_operations(operations, user): Apply memory operations, return list of changed URIs / 应用记忆操作，返回变更的 URI 列表
- Directly execute MemoryOperations from model output, no function call / 直接执行模型输出的 MemoryOperations，不经过 function call

### 7. Structured Output Implementation / 结构化输出实现

Based on the implementation from ../memory project, the following tech stack is used / 基于 ../memory 项目的实现方案，采用以下技术栈：

#### 7.1 Core Components / 核心组件

**Pydantic BaseModel**: Used to define all data structures / 用于定义所有数据结构
- MemoryField, MemoryType, MemoryData
- ReasoningAction, MemoryChange, ReadAction
- MemoryOperations, WriteOp, EditOp, DeleteOp

**json_repair**: Fault-tolerant LLM output parsing / 容错解析 LLM 输出
- Auto-repair incomplete JSON / 自动修复不完整的 JSON
- Handle trailing content (e.g., safety warnings) / 处理尾随内容（如安全警告）
- Compatible with non-standard formats / 兼容非标准格式

**Pydantic TypeAdapter**: Type validation and conversion / 类型验证和转换
- validate_python(value, strict=False) - non-strict mode validation / 非严格模式验证
- Automatic type conversion / 自动类型转换
- Fault-tolerant filtering for list types / 列表类型容错过滤

**BaseModelCompat**: Compatibility base class (refer to ../memory) / 兼容性基类（参考 ../memory）
- Extract base type from Optional/Union / 从 Optional/Union 提取基础类型
- Convert 'None' string to None / 'None' 字符串转 None
- Automatic string conversion (array→comma-separated string, dict→JSON) / 字符串自动转换（数组→逗号分隔字符串，dict→JSON）
- Numeric fault tolerance (string→int/float) / 数字容错（字符串→int/float）
- List fault tolerance (string→[string], dict→[dict]) / 列表容错（字符串→[string]，dict→[dict]）

#### 7.2 JSONAdapter Flow / JSONAdapter 流程

```
1. format() - Build prompt / 构建提示
   - prepare_instructions(): Generate input/output field descriptions / 生成输入输出字段说明
   - format_turn(): Format user/assistant messages / 格式化用户/助手消息
   - format_fields(): Format field values / 格式化字段值

2. llm_request() - Call LLM / 调用 LLM
   - Check if json_schema is supported / 检查是否支持 json_schema
   - If supported: generate response_format (type: json_schema) / 如果支持：生成 response_format (type: json_schema)
   - If not supported: use json_object or no format / 如果不支持：使用 json_object 或无格式
   - Call LM with static_messages + dynamic_messages / 调用 LM，传入 static_messages + dynamic_messages

3. parse() - Parse output / 解析输出
   - remove_trailing_content(): Remove content after JSON ends / 去除 JSON 结束后的内容
   - json_repair.loads(): Fault-tolerant parsing / 容错解析
   - parse_value(): Type conversion + fault tolerance / 类型转换 + 容错
     - List type: filter invalid items / 列表类型：过滤无效项目
     - Other types: use TypeAdapter validation / 其他类型：使用 TypeAdapter 验证
```

#### 7.3 Fault Tolerance Strategy / 容错策略

**JSON Parsing Fault Tolerance** / **JSON 解析容错**:
- Remove trailing content after JSON ends (e.g., safety warnings) / 去除 JSON 结束后的尾随内容（如安全警告）
- json_repair auto-repairs incomplete JSON / json_repair 自动修复不完整 JSON
- Compatible with array output (take first element) / 兼容数组输出（取第一个元素）

**Type Validation Fault Tolerance** / **类型验证容错**:
- List type: validate items one by one, skip invalid items / 列表类型：逐个验证元素，跳过无效项
- Non-strict mode (strict=False) validation / 非严格模式 (strict=False) 验证
- BaseModelCompat preprocessor handles common format issues / BaseModelCompat 预处理器处理常见格式问题

**Field Fault Tolerance** / **字段容错**:
- Missing fields: use default values / 缺失字段：使用默认值
- Extra fields: automatically ignore / 多余字段：自动忽略
- Type mismatch: attempt automatic conversion / 类型不匹配：尝试自动转换

---

## Existing Design Analysis / 现有设计分析

### Current Architecture / 当前架构

### Storage / 存储方式

- **File Storage**: L0/L1/L2 three-level structure / L0/L1/L2 三层结构
  - L0: `.abstract.md` - summary / 摘要
  - L1: `.overview.md` - overview / 概览
  - L2: content file / 内容文件

- **URI Structure**:
  - User: `viking://user/{space}/memories/{category}/`
  - Agent: `viking://agent/{space}/memories/{category}/`

- **Vector Index**: stored in context collection of VikingDB / 存储在 VikingDB 的 context 集合中

### Key Files / 关键文件

| File / 文件                                                         | Purpose / 作用                                                  |
| ----------------------------------------------------------------- | ------------------------------------------------------------- |
| `openviking/session/memory_extractor.py`                          | Memory extraction main logic (~1200 lines) / 记忆提取主逻辑 (~1200行) |
| `openviking/session/memory_deduplicator.py`                       | Deduplication decision (~395 lines) / 去重决策 (~395行)            |
| `openviking/session/compressor.py`                                | Session compressor (~447 lines) / 会话压缩器 (~447行)               |
| `openviking/prompts/templates/compression/memory_extraction.yaml` | Extraction prompt template (~400 lines) / 提取提示模板 (~400行)      |

---

## Implementation Steps / 实施步骤

### Phase 1: Core Data Structures / 核心数据结构

1. Create `openviking/session/memory/memory_data.py` / 创建 `openviking/session/memory/memory_data.py`
   - Field type, merge operation Enum / 字段类型、合并策略 Enum
   - MemoryField, MemoryType, MemoryData definitions / MemoryField、MemoryType、MemoryData 定义

2. Create `openviking/session/memory/memory_operations.py` / 创建 `openviking/session/memory/memory_operations.py`
   - WriteOp, EditOp, DeleteOp
   - MemoryOperations (LLM final output format) / MemoryOperations（LLM 最终输出格式）

3. Create `openviking/session/memory/memory_react.py` / 创建 `openviking/session/memory/memory_react.py`
   - Standard LLM ReAct implementation / 标准的 LLM ReAct 实现

4. Create `openviking/session/memory/memory_functions.py` / 创建 `openviking/session/memory/memory_functions.py`
   - ReadArgs/ReadResult
   - FindArgs/FindResult
   - LsArgs/LsResult
   - READ_TOOL, FIND_TOOL, LS_TOOL definitions / READ_TOOL、FIND_TOOL、LS_TOOL 定义

### Phase 2: Patch Handling / Patch 处理

5. Create `openviking/session/memory/memory_patch.py` / 创建 `openviking/session/memory/memory_patch.py`
   - MemoryPatchHandler class / MemoryPatchHandler 类
   - apply_content_patch() - SEARCH/REPLACE format / SEARCH/REPLACE 格式
   - apply_field_patches() - Field-level updates / 字段级更新

### Phase 3: Type Registration / 类型注册

6. Create `openviking/session/memory/memory_types.py` / 创建 `openviking/session/memory/memory_types.py`
   - MemoryTypeRegistry class / MemoryTypeRegistry 类
   - YAML loading functionality / YAML 加载功能
   - Built-in type registration / 内置类型注册

7. Create YAML config files / 创建 YAML 配置文件
   - Place in `openviking/session/memory/schemas/` directory / 放在 `openviking/session/memory/schemas/` 目录
   - preferences.yaml, entities.yaml, tools.yaml, etc. / preferences.yaml、entities.yaml、tools.yaml 等

### Phase 4: MemoryUpdater

8. Create `openviking/session/memory/memory_updater.py` / 创建 `openviking/session/memory/memory_updater.py`
   - MemoryUpdater class / MemoryUpdater 类
   - apply_operations() method - system direct execution / apply_operations() 方法 - 系统直接执行
   - Integration with OpenViking Storage / 与 OpenViking Storage 集成

### Phase 5: ReAct Orchestrator / ReAct 编排器

9. Complete `openviking/session/memory/memory_react.py` / 完善 `openviking/session/memory/memory_react.py`
   - Standard LLM ReAct orchestrator implementation / 标准的 LLM ReAct 编排器实现

### Phase 6: Prompt Templates / 提示模板

10. Create LLM prompt templates / 创建 LLM 提示模板
    - Reasoning prompt (output MemoryChangePlan format) / 推理提示（输出 MemoryChangePlan 格式）
    - Operation generation prompt (output MemoryOperations format) / 操作生成提示（输出 MemoryOperations 格式）
    - L0/L1 summary generation prompt / L0/L1 摘要生成提示

11. Create new memory_extractor_v2.py / 创建新的 memory_extractor_v2.py
    - Implement new memory extractor entry point / 实现新的记忆提取器入口
    - Preserve existing memory_extractor.py without modification / 保留现有 memory_extractor.py 不修改
    - Can switch via config later / 后续可通过配置切换

### Phase 7: Testing and Verification / 测试与验证

12. Unit tests / 单元测试
    - MemoryPatchHandler tests / MemoryPatchHandler 测试
    - MemoryUpdater tests / MemoryUpdater 测试
    - MemoryReActOrchestrator tests / MemoryReActOrchestrator 测试

13. Integration tests / 集成测试
    - End-to-end ReAct flow tests / 端到端 ReAct 流程测试
    - Backward compatibility tests / 向后兼容测试

---

## File List / 文件清单

### New Files / 新增文件

```
openviking/session/
├── memory_extractor_v2.py  # New memory extractor (replaces existing) / 新的记忆提取器（替换现有）

openviking/session/memory/
├── memory_data.py          # Core data structures / 核心数据结构
├── memory_operations.py    # Three operation definitions (LLM final output) / 三种操作定义（模型最终输出）
├── memory_react.py         # ReAct orchestrator / ReAct 编排器
├── memory_functions.py     # Function call definitions (read/find/ls) / Function call 定义（read/find/ls）
├── memory_patch.py         # Patch handler / Patch 处理器
├── memory_types.py         # Type registry / 类型注册表
├── memory_updater.py       # Patch applier (system execution) / Patch 应用器（系统执行）
└── schemas/                # Config directory / 配置目录
    ├── profile.yaml       # directory: viking://user/{user_space}/memories
    ├── preferences.yaml   # directory: viking://user/{user_space}/memories/preferences
    ├── entities.yaml      # directory: viking://user/{user_space}/memories/entities
    ├── events.yaml        # directory: viking://user/{user_space}/memories/events
    ├── cases.yaml         # directory: viking://agent/{agent_space}/memories/cases
    ├── patterns.yaml      # directory: viking://agent/{agent_space}/memories/patterns
    ├── tools.yaml         # directory: viking://agent/{agent_space}/memories/tools
    └── skills.yaml        # directory: viking://agent/{agent_space}/memories/skills

tests/session/memory/
├── test_memory_data.py
├── test_memory_operations.py
├── test_memory_react.py
├── test_memory_patch.py
├── test_memory_types.py
└── test_memory_updater.py
```

### Preserved Files / 保留文件

```
openviking/session/memory_extractor.py  # Preserve existing version, do not modify / 保留现有版本，不修改
```

---

## ReAct Flow Details / ReAct 流程详细说明

### Phase 0: Pre-fetch (Pre-fetch - System Execution / 前置读取 - 系统执行)

**Purpose** / **目的**: Avoid excessive time from multiple ReAct rounds / 避免多次 ReAct 导致耗时过长

**System automatically executes** / **系统自动执行**:
1. **ls**: Get all memory directory structures / 获取所有记忆目录结构
   - `viking://user/{user_space}/memories/` and subdirectories / 及子目录
   - `viking://agent/{agent_space}/memories/` and subdirectories / 及子目录
2. **read**: Read all `.abstract.md` (L0) and `.overview.md` (L1) / 读取所有 `.abstract.md` (L0) 和 `.overview.md` (L1)
3. **search**: Perform one semantic search in all directories (using current conversation as query) / 在所有目录执行一次语义搜索（使用当前对话作为查询）

**Output** / **输出**: Pre-fetched Context (directory structure + L0/L1 summaries + search results) / Pre-fetched Context（目录结构 + L0/L1 摘要 + 搜索结果）

### Phase 1: Reasoning + Action (Reasoning + Action / 推理 + 行动)

**Input** / **输入**: Conversation history + Pre-fetched Context / 对话历史 + Pre-fetched Context

**LLM Task** / **LLM 任务**: Analyze conversation + Pre-fetched Context, identify memories that need changes + decide if additional reads are needed / 分析对话 + Pre-fetched Context，识别需要变更的记忆 + 决定是否需要额外读取

**Output** / **输出**: ReasoningAction (reasoning + optional additional actions) / ReasoningAction（reasoning + 可选的额外 actions）
- reasoning: List of memories that need changes (write/edit/delete) / 需要变更的记忆列表（write/edit/delete）
- actions: List of additional read operations (only execute when needed, e.g., read specific L2 content) / 额外的读取操作列表（仅在需要时执行，如读取具体 L2 内容）

**System Execution** / **系统执行**: Execute additional read operations in actions (if needed) / 执行 actions 中的额外读取操作（如需要）

### Phase 2: Generate Operations (Generate Final Operations / 生成最终操作)

**Input** / **输入**: Conversation history + Reasoning + read existing memories / 对话历史 + Reasoning + 读取的现有记忆

**LLM Task** / **LLM 任务**: Generate specific operations based on existing memories / 基于现有记忆生成具体的操作

**Output** / **输出**: MemoryOperations (model final output) / MemoryOperations（模型最终输出）

**Important** / **重要**: This is the model's final output, directly executed by system, no more function calls / 这是模型的最后输出，直接由系统执行，不再通过 function call

### Phase 3: System Execute (System Execution / 系统执行)

**Input** / **输入**: MemoryOperations

**Execution** / **执行**: MemoryUpdater.apply_operations() directly applies patch, writes to OpenViking Storage / MemoryUpdater.apply_operations() 直接应用 patch，写入 OpenViking Storage

---

## Summary / 总结

This design is based on practices from ../memory project, uses ReAct pattern: / 本设计基于 ../memory 项目的实践，采用 ReAct 模式：
- ✅ Removed aggregation operators (not considered for now) / 移除了聚合算子（先不考虑）
- ✅ Adopt write/edit/delete three-operation Schema / 采用 write/edit/delete 三种操作 Schema
- ✅ Adopt RoocodePatch style dual-mode Patch (replace + patch) / 采用 RoocodePatch 风格的双模式 Patch（replace + patch）
- ✅ **Dual-mode design** / **双模式设计**:
  - Simple mode: no content_template, only name + content fields / 简单模式：无 content_template，只有 name + content 字段
  - Template mode: with content_template, supports multiple structured fields + MEMORY_FIELDS comment / 模板模式：有 content_template，支持多个结构化字段 + MEMORY_FIELDS 注释
- ✅ **Pre-fetch Optimization** / **前置读取优化**:
  - Phase 0: System automatically executes before LLM reasoning / 阶段 0: 系统在 LLM 推理前自动执行
  - ls: Get all memory directory structures / ls: 获取所有记忆目录结构
  - read: Read all .abstract.md (L0) and .overview.md (L1) / read: 读取所有 .abstract.md (L0) 和 .overview.md (L1)
  - search: Perform one semantic search in all directories / search: 在所有目录执行一次语义搜索
  - Purpose: Avoid excessive time from multiple ReAct rounds / 目的：避免多次 ReAct 导致耗时过长
- ✅ **ReAct (Reasoning + Action) 3+1 phase flow** / **ReAct (Reasoning + Action) 3+1 阶段流程**:
  0. Pre-fetch: System automatically pre-fetches directories, L0/L1, search results / Pre-fetch: 系统自动前置读取目录、L0/L1、搜索结果
  1. Reasoning + Action: LLM outputs reasoning (memories that need changes) and actions (optional additional reads) based on pre-fetched context / LLM 基于 pre-fetched context 输出 reasoning（需要变更的记忆）和 actions（可选的额外读取）
  2. Generate Operations: Generate final MemoryOperations based on existing memories / 基于现有记忆生成最终的 MemoryOperations
  3. System Execute: System directly executes MemoryOperations (no more function calls) / 系统直接执行 MemoryOperations（不再通过 function call）
- ✅ Adopt semantic filenames, fully utilize navigation value of filenames / 采用语义化文件名，充分利用文件名的导航价值
- ✅ Learn from commercial memory library implementations, adopt patch incremental update mechanism, reduce LLM call count / 借鉴商业化记忆库的实现，采用 patch 增量更新机制，减少 LLM 调用次数
- ✅ Fully compatible with OpenViking existing storage structure / 完全兼容 OpenViking 现有存储结构
