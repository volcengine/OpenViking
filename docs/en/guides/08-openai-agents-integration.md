# OpenAI Agents SDK and Multi-Agent Integration Guide

This guide describes a practical integration pattern for using OpenViking with the OpenAI Agents SDK in multi-agent and multi-pipeline systems.

It is aimed at teams building workflows such as writing pipelines, coding pipelines, review pipelines, and other long-running agent systems where:

- multiple agents operate on the same project or corpus
- each agent needs only a small slice of context at a time
- structured knowledge should outlive a single chat session
- token costs must stay predictable as work scales

## Core Recommendation

Use OpenViking as the durable context layer, and keep the agent runtime session thin.

In practice, this means:

1. Store durable knowledge in OpenViking resources and memories
2. Keep the OpenAI Agents SDK conversation focused on the current step only
3. Retrieve context just-in-time with `find()`, `ls()`, `abstract()`, and `read()`
4. Write back important outputs as structured resources instead of relying on long chat history
5. Separate shared knowledge (`viking://resources/`, `viking://user/`) from agent-specific working state (`viking://agent/`)

This avoids two common failure modes:

- **token explosion** from carrying old tool results and prior outputs in every turn
- **tool hallucination / tool leakage** when one agent sees another agent's tool calls in shared session history

## Recommended Context Layout

A good default layout for multi-agent applications is:

```text
viking://
├── resources/
│   ├── corpus/                  # shared reference material
│   ├── characters/              # durable domain entities
│   ├── locations/
│   ├── structure/
│   ├── scenes/
│   └── decisions/               # cross-pipeline distilled outputs
├── user/
│   └── memories/                # user preferences, stable facts
└── agent/
    ├── skills/
    ├── instructions/
    └── memories/                # agent-local heuristics, patterns, cases
```

### Scope Selection

- Use `viking://resources/` for facts and artifacts that other agents should discover later
- Use `viking://user/` for user or customer preferences that multiple agents should share
- Use `viking://agent/` for agent-specific strategies, temporary working patterns, and isolated memory

If multiple specialist agents collaborate on the same project, prefer shared project state in `resources/` and keep agent-specific reasoning in `agent/`.

## Tool Selection Pattern

For agent tool design, the simplest reliable pattern is:

- `ls(uri)` for browsing and directory discovery
- `abstract(uri)` for L1 overviews and quick navigation
- `read(uri)` for exact L2 content when the URI is already known
- `find(query, target_uri, limit)` for direct semantic retrieval within a chosen scope
- `search(query, session_info=...)` only when you need intent analysis and broader multi-hop retrieval

### `find()` vs `search()`

For most OpenAI Agents SDK tool integrations, start with `find()`.

Use `find()` when:
- the agent already knows the task focus
- you can constrain the target scope, such as `viking://resources/scenes/`
- you want predictable latency and token use
- the tool should behave deterministically inside a workflow step

Use `search()` when:
- the task is ambiguous or broad
- you want OpenViking to perform intent analysis and generate multiple retrieval subqueries
- the agent is in a planning phase rather than an execution phase

A useful pattern is:

1. plan with `ls()` + `abstract()` or `search()`
2. execute with scoped `find()` + exact `read()`

## Progressive Context Loading

OpenViking is designed for progressive loading.

### L0 / L1 / L2 in practice

- **L0**: abstract snippets for quick recall and filtering
- **L1**: overview content from `abstract(uri)` for decision-making and navigation
- **L2**: exact source content from `read(uri)` when the agent truly needs details

Recommended usage inside an agent loop:

1. use `ls()` to inspect candidate directories
2. use `abstract()` on the most relevant directories
3. use `find()` to pull a small shortlist of relevant files
4. use `read()` only for the files selected for the current step

This keeps active prompt context small while preserving access to the full knowledge base.

## Cross-Pipeline Knowledge Sharing

Do not rely on one pipeline's chat transcript as the main handoff surface.

Instead, publish important outcomes back into OpenViking as explicit resources.

Examples:

- `viking://resources/characters/kenji.md`
- `viking://resources/locations/jazz-club.md`
- `viking://resources/structure/act-2-turn.md`
- `viking://resources/decisions/visual-style/noir-lighting.md`
- `viking://resources/decisions/continuity/scene-12-constraints.md`

### Distill, don't dump

When a pipeline finishes a meaningful step, write a distilled artifact instead of raw conversation logs.

Good write-back targets include:

- final decisions
- constraints for downstream agents
- entity profiles
- style guides
- continuity facts
- reusable intermediate artifacts

This gives later pipelines stable retrieval targets and avoids forcing them to reconstruct decisions from noisy transcripts.

## Agent-Scoped Relevance

OpenViking does not require a separate retrieval engine per agent. Instead, bias relevance through structure and retrieval scope.

### Practical ways to increase relevance

1. **Scope by directory**
   - query `viking://resources/scenes/scene-12/` instead of all resources when possible
2. **Write structured project state**
   - keep scene-, character-, or task-specific state in predictable URIs
3. **Use agent-local memory for heuristics**
   - store reusable agent-specific patterns under `viking://agent/.../memories/`
4. **Compose the query with current task facts**
   - include active entities, location, phase, or constraints in the query text
5. **Load broad context first, then narrow**
   - use `abstract()` on parent directories before exact reads

For example, a scene-writing agent can query:

```python
results = await client.find(
    "scene 12 noir lighting jazz club kenji yuki emotional tension",
    target_uri="viking://resources/",
    limit=5,
)
```

This is usually more effective than a generic query like `"noir lighting"`, because the query carries the current dramatic context.

## Session Design with OpenAI Agents SDK

A strong default pattern is:

- one OpenAI Agents SDK run or activity = one narrow task
- one OpenViking project/resource tree = durable shared context
- one agent identity = isolated `agent/` memory scope when needed

### Recommended rule

Keep the LLM session short-lived and treat OpenViking as the memory system of record.

That means:

- do not stuff prior scenes, all tool calls, or all previous outputs into each turn
- do not share one giant conversation across unrelated agents
- do write durable outputs back to OpenViking between steps

## Example Retrieval Workflow

A typical workflow for a specialist agent:

1. Browse project state
2. Load one or two L1 overviews
3. Retrieve a narrow semantic shortlist
4. Read only the selected files
5. Produce output
6. Save distilled result back to OpenViking

```python
# 1. Browse available project structure
scene_dirs = await client.ls("viking://resources/scenes/")

# 2. Load compact overview for current scene directory
scene_overview = await client.abstract("viking://resources/scenes/scene-12/")

# 3. Retrieve current-task context
hits = await client.find(
    "scene 12 kenji yuki jazz club continuity constraints noir lighting",
    target_uri="viking://resources/",
    limit=5,
)

# 4. Read only the files selected for this step
selected_docs = []
for hit in hits:
    selected_docs.append(await client.read(hit["uri"]))

# 5. Generate output with the OpenAI Agents SDK
# 6. Persist distilled result as a resource for downstream agents
```

## Anti-Patterns

Avoid these patterns in multi-agent systems:

### 1. Shared giant transcript

Do not make every agent consume the same long transcript with all historical tool calls.

Why it fails:
- token growth becomes unbounded
- irrelevant tool results pollute later steps
- one agent may imitate another agent's unavailable tools

### 2. Full L2 loading by default

Do not read every full profile, document, or scene on each turn.

Why it fails:
- expensive
- noisy
- often worse for reasoning quality

Prefer `abstract()` and scoped `find()` first.

### 3. Unstructured write-back

Do not treat raw chat logs as the only durable output.

Why it fails:
- downstream discovery is weak
- retrieval quality drops
- important decisions are hard to reuse

Prefer structured resource artifacts under predictable paths.

## Suggested Integration Checklist

- [ ] keep agent sessions narrow and task-scoped
- [ ] use `resources/` for shared durable project knowledge
- [ ] use `agent/` for agent-local memory and heuristics
- [ ] use `ls()` and `abstract()` before loading full files
- [ ] prefer scoped `find()` for execution-time retrieval
- [ ] persist distilled outputs after each major step
- [ ] avoid sharing giant transcripts across agents

## Related References

- [Context Types](../concepts/02-context-types.md)
- [Context Layers](../concepts/03-context-layers.md)
- [Retrieval Mechanism](../concepts/07-retrieval.md)
- [Session Management](../concepts/08-session.md)
- [Resources API](../api/02-resources.md)
- [Retrieval API](../api/06-retrieval.md)
- [MCP Integration Guide](./06-mcp-integration.md)
