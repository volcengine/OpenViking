# Memory

Memory is produced by session commit or explicit extraction, stored in the user memory namespace, and consumed through the content, file-system, and retrieval APIs.

## Built-in Memory Types

| Category | Location | Description |
|----------|----------|-------------|
| profile | `viking://user/{user_id}/memories/profile.md` | User profile information |
| preferences | `viking://user/{user_id}/memories/preferences/` | User preferences by topic |
| entities | `viking://user/{user_id}/memories/entities/` | Important entities (people, projects) |
| events | `viking://user/{user_id}/memories/events/` | Significant events |
| identity | `viking://user/{user_id}/memories/identity.md` | Assistant identity and self-introduction |
| soul | `viking://user/{user_id}/memories/soul.md` | Assistant principles, boundaries, style, and continuity |
| cases | `viking://user/{user_id}/memories/cases/` | Trainable and evaluable task cases |
| trajectories | `viking://user/{user_id}/memories/trajectories/` | Reusable operation contracts distilled from agent task trajectories |
| experiences | `viking://user/{user_id}/memories/experiences/` | Reusable execution insights |
| tools | `viking://user/{user_id}/memories/tools/` | Tool usage knowledge and best practices |
| skills | `viking://user/{user_id}/memories/skills/` | Skill execution knowledge and workflow strategies |

These are the bundled types. `tools` and `skills` are disabled by default; extraction also depends on the effective memory policy and Agent Evolution settings. The table shows Self paths. Peer-enabled types can also be stored under `viking://user/{user_id}/peers/{peer_id}/memories/`. Deployment templates can extend or override the registry; the account template API permits only its documented fields and types.

---

## Memory Recall

Use [`search(mode="context")`](06-retrieval.md#search-mode-context) to retrieve memories and assemble an injection-ready block. The server MCP exposes the same operation as `search(mode="context")`; there is no separate `recall` tool.

```http
POST /api/v1/search/recall
Content-Type: application/json
```

`/api/v1/search/recall` is deprecated. It is a preset over `search(mode="context")` with `purpose="coding"` and the v1 defaults, kept for existing callers; responses carry a `Deprecation: true` header. Do not use it for new integrations.

## Rules

Rules are compiled behavioral constraints stored as Markdown under `viking://user/{user_id}/memories/rules`, scoped to the authenticated account and user.

### Compile rules

```http
POST /api/v1/rules/compile
Content-Type: application/json
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| instruction | string | No | null | Rule instructions, up to 64,000 characters; whitespace is trimmed |
| sources | object[] | No | [] | Up to 16 sources, each either `{"resource_uri":"viking://resources/policy"}` for an ingested resource or a [resource import request](02-resources.md) |

Provide a non-empty `instruction` or at least one source. Sources must be parseable into readable text; multimodal sources are not supported. Unknown top-level fields are rejected.

```json
{
  "instruction": "Ask for confirmation before deleting files.",
  "sources": []
}
```

Returns HTTP `202 Accepted` with `{"status":"ok","result":...}`, where `result` is a task record with `task_type="rule_compile"` and a `rul_` task ID. Poll `GET /api/v1/tasks/{task_id}` using the [Tasks API](17-tasks.md). On completion, the task result contains `rules_root`, `source_uris`, `plan_reason`, `batch_count`, `operations`, `written_uris`, and `write_result`. Content indexing runs as separate background work identified by `write_result`.

### Search applicable rules

```http
POST /api/v1/rules/search
Content-Type: application/json
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| task_context | string | Yes | - | Current task facts, 1–64,000 characters; must be non-empty after trimming |
| rule_probability_threshold | number | No | 0.65 | Minimum rule applicability probability, from 0 to 1 inclusive |

```json
{
  "task_context": "Delete a generated file from the workspace.",
  "rule_probability_threshold": 0.65
}
```

Returns HTTP `200 OK` with `{"status":"ok","result":...}`. Only active rules are eligible. Unknown request fields are rejected.

| Result field | Description |
|--------------|-------------|
| rules | Matching rules, each with `atomic_id`, `revision`, `scenario_id`, `probability`, `uri`, and complete Markdown `content` |
| scenarios | Recalled scenario IDs |
| rendered | Rendered rules and application guidance for agent context |
| delivery | `complete`, `rule_count`, and `rendered_chars` describe the delivered content |
| scenario_selection | Scenario filtering diagnostics, or null |
| judge, warning | Optional judge identifier and fallback warning |

## Related Documentation

- [Sessions](05-sessions.md) - commit and extract
- [Retrieval](06-retrieval.md) - search memory
- [Content](12-content.md) - read memory content
