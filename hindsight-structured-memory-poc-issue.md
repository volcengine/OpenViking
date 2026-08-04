# Hindsight-Style Structured Memory POC

## Summary

This POC introduces a Hindsight-style structured memory layer into OpenViking with minimal intrusion.

The core idea is to keep the existing storage, vector index, transaction model, and `MemoryUpdater` flow unchanged, and only extend:

- memory schema definitions
- extraction JSON schema generation
- recall type quota and rendering
- MCP recall tool description

The feature is gated behind `memory.experimental_memory_switch=true`, so default behavior remains unchanged.

## Background

OpenViking currently supports long-term memory extraction through configurable YAML memory schemas. Existing memory types such as `events`, `entities`, `preferences`, and `experiences` are useful, but they do not explicitly separate raw evidence, atomic facts, inferred patterns, and actionable agent beliefs.

Hindsight-style memory introduces a clearer hierarchy:

- `events`: episodic / experience evidence
- `facts`: explicit atomic facts stated by the user
- `observations`: stable patterns inferred from facts and events
- `beliefs`: actionable working beliefs or strategies for the agent, with confidence and evidence

This POC explores whether that structure can be represented using OpenViking's existing schema and recall mechanisms.

## Goals

- Add structured memory types without changing the storage layer.
- Keep `events` as the existing episodic evidence layer.
- Add `facts`, `observations`, and `beliefs` as experimental memory schemas.
- Ensure new schemas only load when `memory.experimental_memory_switch=true`.
- Use only existing primitive field types: `string` and `float32`.
- Extend recall so structured memory can be retrieved and rendered by type quota.
- Keep the default OpenViking behavior unchanged.

## Non-Goals

- Do not change VikingFS.
- Do not change the vector backend.
- Do not change transaction semantics.
- Do not change indexing logic.
- Do not change `MemoryUpdater` core flow.
- Do not add a graph database.
- Do not add new YAML field types such as list or object.
- Do not add a separate reflection pass in this POC.
- Do not introduce a new `episodes` type; reuse existing `events`.

## Proposed Design

### 1. Add Experimental Memory Schemas

Add three new schema files under:

```text
openviking/prompts/templates/memory/experimental_memory/
```

New files:

- `facts.yaml`
- `observations.yaml`
- `beliefs.yaml`

These schemas should only be loaded when:

```yaml
memory:
  experimental_memory_switch: true
```

When the switch is disabled, `facts`, `observations`, and `beliefs` should not appear in the memory type registry.

### 2. Memory Type Mapping

#### events

Keep existing `events` unchanged.

`events` represent episodic memory and experience evidence. They are the raw evidence layer for later structured memory.

#### facts

`facts` represent explicit atomic facts stated by the user.

The type should be append-only so historical evidence is preserved instead of overwritten.

Operation mode:

```yaml
operation_mode: add_only
```

Fields:

| Field | Type | Merge Behavior | Description |
| --- | --- | --- | --- |
| `subject` | `string` | immutable | Entity, user, project, or topic the fact is about |
| `fact_name` | `string` | immutable | Short stable fact key |
| `claim` | `string` | immutable | Atomic factual claim |
| `source` | `string` | immutable | Source description |
| `ranges` | `string` | immutable | Source message ranges or evidence ranges |
| `confidence` | `float32` | replace | Confidence score |
| `content` | `string` | replace | Human-readable fact content |

#### observations

`observations` represent stable inferred patterns derived from events and facts.

Operation mode:

```yaml
operation_mode: upsert
```

Fields:

| Field | Type | Merge Behavior | Description |
| --- | --- | --- | --- |
| `subject` | `string` | immutable | Entity, user, project, or topic the observation is about |
| `topic` | `string` | immutable | Observation topic |
| `confidence` | `float32` | replace/patch | Confidence score |
| `evidence` | `string` | patch | Markdown bullet list of event/fact URIs or source ranges |
| `content` | `string` | patch | Inferred pattern; must be clearly marked as inferred, not raw fact |

#### beliefs

`beliefs` represent actionable working beliefs or strategies that guide future agent behavior.

Operation mode:

```yaml
operation_mode: upsert
```

Fields:

| Field | Type | Merge Behavior | Description |
| --- | --- | --- | --- |
| `subject` | `string` | immutable | Entity, user, project, or topic the belief is about |
| `belief_name` | `string` | immutable | Short stable belief key |
| `confidence` | `float32` | replace/patch | Confidence score |
| `evidence` | `string` | patch | Markdown bullet list of evidence URIs or source ranges |
| `content` | `string` | patch | Actionable guidance for future agent behavior; can be revised by new evidence |

### 3. Recall Changes

Extend:

```text
openviking/retrieve/type_quota_recall.py
```

Update `TYPE_ORDER` to include:

```python
TYPE_ORDER = (
    "events",
    "facts",
    "entities",
    "observations",
    "beliefs",
    "preferences",
    "experiences",
)
```

Update `DEFAULT_QUOTAS` with small quotas for the structured memory types:

```python
DEFAULT_QUOTAS = {
    "events": 10,
    "facts": 5,
    "entities": 10,
    "observations": 3,
    "beliefs": 2,
    "preferences": 3,
    "experiences": 0,
}
```

Update `DEFAULT_OTHER_PEER_PENALTIES` to support:

- `facts`
- `observations`
- `beliefs`

Update `type_char_budgets` to include:

- `facts`
- `observations`
- `beliefs`

Recall rendering should be able to produce groups such as:

```xml
<memory_group type="facts">
...
</memory_group>
```

```xml
<memory_group type="observations">
...
</memory_group>
```

```xml
<memory_group type="beliefs">
...
</memory_group>
```

### 4. MCP Recall Description

Update the MCP recall tool docstring in:

```text
openviking/server/mcp_endpoint.py
```

The tool description should mention that recall searches:

- events
- facts
- entities
- observations
- beliefs
- preferences
- experiences

## Test Plan

### Prompt Manager / Registry

Update:

```text
tests/test_prompt_manager.py
```

Add coverage for:

- `experimental_memory_switch=false`
  - `facts` is not loaded
  - `observations` is not loaded
  - `beliefs` is not loaded

- `experimental_memory_switch=true`
  - `facts` is loaded
  - `observations` is loaded
  - `beliefs` is loaded

### Schema Models

Update:

```text
tests/session/memory/test_schema_models.py
```

Add coverage for:

- new schemas can generate structured operations JSON schema
- `facts`, `observations`, and `beliefs` appear as top-level operation fields
- operation modes are correct:
  - `facts`: `add_only`
  - `observations`: `upsert`
  - `beliefs`: `upsert`

### Recall

Update:

```text
tests/retrieve/test_type_quota_recall.py
tests/server/test_recall_endpoint.py
tests/server/test_mcp_endpoint.py
tests/server/test_recall_peer_scope.py
```

Add or update coverage for:

- quota normalization supports `facts`, `observations`, and `beliefs`
- default penalties support the new types
- recall can search structured memory groups
- rendered recall output contains:
  - `<memory_group type="facts">`
  - `<memory_group type="observations">`
  - `<memory_group type="beliefs">`
- MCP recall can return structured memory groups

## Assumptions

- First version lets the existing session extraction flow directly produce structured memories.
- There is no separate reflection pass in this POC.
- `events` remain the experience/evidence layer.
- Evidence references are represented as Markdown/string fields.
- Future versions may add richer reflection, graph links, or stronger consistency semantics, but this POC intentionally avoids those changes.

## Expected Outcome

When `memory.experimental_memory_switch=true`, OpenViking can represent Hindsight-style structured memory:

- `events` preserve episodic evidence
- `facts` preserve explicit user-stated facts
- `observations` summarize stable inferred patterns
- `beliefs` provide actionable agent guidance with evidence and confidence

When the switch is disabled, behavior remains compatible with the current default memory system.
