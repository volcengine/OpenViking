# Rules (Beta)

The Rules API compiles user-provided mandatory constraints into durable Rules and retrieves applicable rules using task context. The current interfaces are the Python HTTP SDK and HTTP API; there are no dedicated CLI, TypeScript SDK, or Go SDK methods. VikingBot includes a [rule search tool (Beta)](../guides/17-vikingbot.md).

Compilation and search use the authenticated user's library at `viking://user/<user_id>/memories/rules/`. See [Rules (Beta)](../concepts/17-rule-scenarios.md) for concepts and [Rule validation (Beta)](../workflows/02-rule-validation.md) for the complete workflow.

## API Reference

### rule_compile()

#### 1. Purpose and prerequisites

Submit rule text or source material for background compilation, creating or updating rules or changing their status on explicit user instructions. A working Compiler model is required; it defaults to the server VLM and can be overridden with `rules.compiler`.

Sources can be ingested resources or new files, directories, or URLs. New sources go through resource ingestion before constraint extraction. Ordinary session commits do not trigger compilation.

#### 2. Interface and parameters

```text
POST /api/v1/rules/compile
```

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `instruction` | string | At least one of this or `sources` | `null` | Rule text or explicit change instructions; at most 64,000 characters. Without sources, it must remain non-empty after trimming |
| `sources` | array | At least one of this or `instruction` | `[]` | At most 16 sources; use `options["sources"]` in the Python SDK |

Each HTTP source uses one of these forms:

| Source | Format | Description |
| --- | --- | --- |
| Existing resource | `{"resource_uri": "viking://resources/standards"}` | Read resource content without importing it again |
| New remote source | `{"path": "https://example.com/standards.md"}` | Reuse the resource ingestion request; see the [Resources API](./02-resources.md) for fields |
| New local source | `{"temp_file_id": "upload-id", "source_name": "standards.md"}` | Upload through the resource temporary upload endpoint first; package directories as ZIP files |

The Python SDK also accepts source strings: local file or directory paths, URLs, and `viking://` resource URIs. It uploads local sources and converts them into HTTP objects.

The Compiler generates mutations; callers do not submit them directly:

| Operation | Behavior |
| --- | --- |
| `NEW` | Create an active Rule and reuse or create a Scenario |
| `UPDATE` | Update one rule, keeping its identity and active/paused status |
| `REPLACE` | On explicit full replacement of one old rule, create a new identity and archive the old rule |
| `NOOP` | An active Rule fully covers the input; no write |
| `UNCERTAIN` / `REJECT` | Record ambiguity or rejection reasons without writing a Rule |
| `PAUSE` | Explicitly pause or disable an active Rule |
| `RESTORE` | Explicitly reactivate a paused Rule |
| `ARCHIVE` | Explicit deletion, discard, or recycle-bin intent; authorization must come from `instruction` |

Compilation candidates include only active and paused rules. There is no public path to restore archived rules or a dedicated interface for submitting lifecycle operations directly by ID.

#### 3. Examples

**Python SDK (HTTP)**

```python
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
task = client.rule_compile(
    instruction="When a repository declares itself open source, it must provide a complete project license text.",
)
print(task["task_id"])

# One status query; see the workflow for polling and index checks.
print(client.get_task(task["task_id"]))
```

**HTTP API**

```bash
curl -X POST http://localhost:1933/api/v1/rules/compile \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{
    "instruction": "When a repository declares itself open source, it must provide a complete project license text."
  }'
```

#### 4. Response contract and errors

HTTP returns **202 Accepted** with a background task record. The Python SDK returns the task object inside the outer `result`. This example omits timestamps and metadata:

```json
{
  "status": "ok",
  "result": {
    "task_id": "rule_compile_example",
    "task_type": "rule_compile",
    "status": "pending",
    "resource_id": "viking://user/alice/memories/rules",
    "stage": "queued",
    "result": null,
    "error": null
  }
}
```

Receiving a task does not mean compilation has completed. Poll the [Tasks API](./17-tasks.md) until `completed`, handling `failed` and `cancelled`. A newly returned task may already be `running`. On completion, `task.result` includes:

| Field | Type | Description |
| --- | --- | --- |
| `rules_root` | string | Current user's rule library URI |
| `source_uris` | array | Source resource URIs; can be empty for inline text |
| `plan_reason` | string | Explanation of the compilation plan |
| `batch_count` | integer | Number of input compilation batches |
| `operations` | array | Actual mutations or reasons for no write; fields depend on operation type |
| `written_uris` | array | Rule and Scenario URIs written |
| `write_result` | object | Batch write result; writes can include a separate `task_id` for index processing |

Example completed task result, omitting other task fields and write statistics:

```json
{
  "status": "ok",
  "result": {
    "task_id": "rule_compile_example",
    "task_type": "rule_compile",
    "status": "completed",
    "result": {
      "rules_root": "viking://user/alice/memories/rules",
      "source_uris": [],
      "plan_reason": "Create the project license requirement.",
      "batch_count": 1,
      "operations": [
        {
          "operation": "NEW",
          "atomic_id": "project-license",
          "uri": "viking://user/alice/memories/rules/repository-license/project-license.md"
        }
      ],
      "written_uris": [
        "viking://user/alice/memories/rules/repository-license/abstract.md",
        "viking://user/alice/memories/rules/repository-license/project-license.md"
      ],
      "write_result": {"task_id": "write_example"}
    },
    "error": null
  }
}
```

Inspect the actual operations and generated content, then wait for `write_result.task_id` before searching. Invalid request structures or lengths return HTTP 422. Background parsing, model, or validation failures appear in the task's `error`; compilation failures carry `RULE_COMPILE_FAILED`.

---

### rule_search()

#### 1. Purpose and prerequisites

Retrieve active rules for the current task, with complete constraints and applicability information. Confirm that compilation and associated index tasks have completed, and search as the same user.

Task context should include the goal, object, planned actions, and known facts. Search judges applicability; the Agent checks evidence and whether rules are violated.

#### 2. Interface and parameters

```text
POST /api/v1/rules/search
```

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `task_context` | string | Yes | — | Non-empty task context, at most 64,000 characters |
| `rule_probability_threshold` | number | No | `0.65` | `0`–`1`, inclusive; return rules scoring at least this value. Pass in SDK `options` |

The public interface has no `limit`, `scenario_limit`, or `target_uri`. Search semantically recalls Scenarios, filters unrelated situations, then reads their active Rules.

#### 3. Examples

**Python SDK (HTTP)**

```python
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
result = client.rule_search(
    task_context="Read-only license review: README declares the repository open source; next, inspect LICENSE text.",
    options={"rule_probability_threshold": 0.65},
)
print(result["rendered"])
print(result.get("warning"))
```

**HTTP API**

```bash
curl -X POST http://localhost:1933/api/v1/rules/search \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{
    "task_context": "Read-only license review: README declares the repository open source; next, inspect LICENSE text.",
    "rule_probability_threshold": 0.65
  }'
```

#### 4. Response contract and errors

HTTP returns **200 OK** with a result envelope. The Python SDK returns the outer `result` directly.

| Field | Type | Description |
| --- | --- | --- |
| `rules` | array | Matches, each with `atomic_id`, `revision`, `scenario_id`, `probability`, `uri`, and `content` |
| `rules[].content` | string | Complete stored Rule Markdown, including metadata and the constraint, not just its constraint sentence |
| `scenarios` | array | Scenario identities passed to the rule loading stage |
| `rendered` | string | Application protocol and complete rule blocks for the Agent |
| `delivery` | object | `complete`, `rule_count`, and `rendered_chars`; delivery completeness for matched rules |
| `scenario_selection` | object / null | Scenario filtering information, including backend, passed or filtered details, and possible warnings |
| `judge` | string, optional | `jev_noul` or `pass`; may be absent with no candidates |
| `warning` | string, optional | Pass-through explanation when Rule judgment is unavailable |

Example response with no Scenario candidates:

```json
{
  "status": "ok",
  "result": {
    "rules": [],
    "scenarios": [],
    "rendered": "# Applicable OpenViking Rules\n\nThese Rules are behavioral constraints.\n\nApplication protocol:\n1. Evaluate every Rule independently against concrete facts from the current task.\n2. Check the applicability boundary and exception before applying a Rule. An empty\n   applies_when means the Rule applies in every situation except its exception.\n   For a non-empty applies_when, topic similarity alone does not establish applicability.\n3. Do not infer facts that are absent.\n4. Before acting or answering, validate the planned action or answer against every\n   returned Rule. Returned Rules are mandatory when their applicability conditions hold.\n\nMatched 0 active Rule(s).\n\nNo active compiled Rule met the current retrieval thresholds.",
    "scenario_selection": null,
    "delivery": {"complete": true, "rule_count": 0, "rendered_chars": 730}
  }
}
```

Even with no matches, `rendered` contains the application protocol and an empty-match message. `delivery.complete=true` means selected rules were not truncated, not that the entire library was recalled. Empty results are not a compliance verdict.

When Jev is unconfigured or judgment fails, affected candidates pass through. Failed Rule judgment returns `judge="pass"`, `probability=1.0`, and `warning`; the score is not an estimated probability. Scenario judgment warnings appear in `scenario_selection`. Agents must check every rule's conditions and exceptions.

Blank context, overlong text, an out-of-range threshold, or extra parameters return HTTP 422. Failures reading Rule files can fail the request; handle interface errors without treating them as an empty rule set. See [API conventions](./01-overview.md) for authentication and general error formats.

## Related documentation

- [Rules (Beta)](../concepts/17-rule-scenarios.md)
- [Rule validation (Beta)](../workflows/02-rule-validation.md)
- [Background Tasks](./17-tasks.md)
- [Server Rules configuration](../configuration/01-server.md#rules)


## Opt-in automatic context delivery

`POST /api/v1/search/search` accepts `rules` only in `mode="context"`:

```json
{
  "query": "Prepare a release",
  "mode": "context",
  "max_tokens": 4000,
  "rules": {"task_context": "Prepare a release", "max_tokens": 1500}
}
```

MCP `search(mode="context")` accepts the same `rules` option and returns the Rule
block before the ordinary memory text.

Omitting `rules` preserves the existing response and performs no Rule lookup.
`task_context` follows Rule Search validation (nonblank, at most 64,000 characters);
`rule_probability_threshold` is optional and retains the Rule Search default.
`rules.max_tokens` defaults to 1024 (64–32000), and is a cap **within**, not in
addition to, the outer `max_tokens`. The complete matched Rule block, including
conditions, exceptions and citations, is budgeted first. If it cannot fit, none
of its Rules are injected; a bounded incomplete-check notice is returned instead.
Ordinary context uses the remaining budget. Token counts are estimates, not a
model-specific tokenizer guarantee.

The response adds an independent `rules` object with `rendered`, `used_tokens`,
`status`, and `delivery` (`complete`, `reason`, `rule_count`). `entries`, `rendered`
and `digest` remain ordinary memory/resource/skill context. Consumers must inject
`rules.rendered` separately, before their selected memory block; never summarize
or truncate the Rule block. Empty memory or a `no_relevant` memory digest does not
suppress Rules. `delivery.complete` describes delivery of the selected Rules, not
proof that every relevant policy was retrieved or that an agent will obey it.

The same authenticated request context is passed to Rule Search. A Rule lookup
has a 10-second deadline; failure or oversized/incomplete delivery is explicitly
marked incomplete while ordinary retrieval can continue. No Rule compilation or
new policy storage is performed. Matching can make model calls and adds latency
and provider cost. Cancellation is propagated.

### Claude Code and Codex hooks

In the existing `ovcli.conf` plugin section, opt in with:

```json
{"plugin": {"rulesAutoInject": true, "rulesMaxTokens": 1500}}
```

The environment equivalents are `OPENVIKING_RULES_AUTO_INJECT=true` and
`OPENVIKING_RULES_MAX_TOKENS=1500`. Keep automatic recall enabled. User-prompt hooks
supply the actual prompt after existing input filters, including short nonempty
prompts, without requiring the model to invoke a tool. A filter that drops the
prompt still prevents transmission. Rule text is kept outside both server-side
memory rewriting and local memory compression. Older servers or incomplete
responses produce an explicit incomplete-check notice, not a claim of no Rules.
Other consumers using the shared recall block builders inherit the opt-in setting;
custom/low-level integrations must explicitly forward and consume Rules. This does
not add a Hermes prefetch trigger or a `PreToolUse` execution gate. Rule injection
is advisory context delivery, not a security enforcement boundary.
