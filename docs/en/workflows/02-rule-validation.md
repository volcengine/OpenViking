# Rule validation (Beta)

Compile mandatory constraints from agreements or coding standards in batches, then let an Agent recall rules for a concrete task, check evidence, and adjust its actions. This tutorial uses user-provided open-source license requirements.

OpenViking compiles rules and retrieves applicable candidates. The Agent decides whether requirements are satisfied or violated; search itself returns no compliance verdict. See [Rules (Beta)](../concepts/17-rule-scenarios.md) for concepts and lifecycle.

## Prerequisites

- Run an OpenViking Server that includes Rule support, with working Compiler and Embedding models. The Compiler defaults to the server VLM; `rules.compiler` can override it.
- Use a Python HTTP SDK with `rule_compile()` and `rule_search()`, configured with the current user's identity. Compile and search as the same user.
- Prepare mandatory constraints confirmed by the user, preserving conditions, exceptions, and thresholds. Do not treat ordinary suggestions or preferences as rules.

See [server Rules configuration](../configuration/01-server.md#rules) and [API conventions](../api/01-overview.md). Run the following snippets in order, replacing the URL and API key with your configuration.

## Step 1: Submit rule material

```python
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
task = client.rule_compile(
    instruction=(
        "When a repository declares itself open source, "
        "it must provide an explicit, complete, and locatable project license text."
    ),
)
print(task["task_id"])
```

To import files or a directory in a batch, replace the call above with:

```python
task = client.rule_compile(options={"sources": ["./compliance-rules/"]})
```

The SDK uploads local sources. Use `{"sources": ["viking://resources/compliance-rules"]}` for an already ingested resource to avoid importing it again. See the [Rules API](../api/26-rules.md) for HTTP source formats.

## Step 2: Wait and inspect the output

```python
import time


def wait_for_task(task_id, timeout=300):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = client.get_task(task_id)
        if current is None:
            raise RuntimeError(f"Task not found: {task_id}")
        if current["status"] == "completed":
            return current
        if current["status"] in {"failed", "cancelled"}:
            raise RuntimeError(current)
        time.sleep(1)
    raise TimeoutError(f"Task still running: {task_id}")


completed = wait_for_task(task["task_id"])
result = completed["result"]
print(result["operations"])
print(result["written_uris"])

# Compilation and subsequent index processing may have separate tasks.
write_task_id = result.get("write_result", {}).get("task_id")
if write_task_id:
    wait_for_task(write_task_id)

for uri in result["written_uris"]:
    print(client.read(uri))
```

Check that generated rules retain the subject, obligations, conditions, exceptions, and exact source evidence. `UNCERTAIN` and `REJECT` only record reasons; `NOOP` means an existing rule covers the input. A timeout stops local polling without cancelling the server task.

## Step 3: Search for a concrete task

Read the object under review first, then put the goal, object, planned actions, and available evidence into `task_context`. These are example facts; replace them with observations from your task:

```python
matches = client.rule_search(
    task_context=(
        "Perform a read-only open-source license review of a repository. "
        "README.md has been read and declares the project open source. "
        "A LICENSE file exists at the root, but its text has not been checked. "
        "Next, read the license text and check its completeness."
    ),
)
print(matches["rendered"])
print(matches.get("warning"))
```

Give the complete `rendered` text to the Agent. Applications using the API directly should also inspect `judge`, `warning`, and `scenario_selection`. When Jev is unavailable, candidates may pass through; their pass scores are not confirmed applicability probabilities.

## Step 4: Have the Agent check every rule

For each returned rule, the Agent should:

1. Check `applies_when` against concrete evidence without inventing missing facts. Empty conditions mean the rule applies except for its exception.
2. Check explicit exceptions, then validate planned actions or answers against `content`.
3. For reviews, record rule identities, sources, and evidence from the reviewed object, distinguishing satisfied, violated, and undetermined requirements. Already satisfied rules still belong in the applicable set.
4. Search again when new facts materially change the applicable situation.

In this example, the README declaration triggers the rule. The existence of LICENSE does not establish that its text is complete, so further reading is needed. Empty search results neither establish the absence of relevant rules nor constitute a passed check.

## Connect an existing Agent

[VikingBot](../guides/17-vikingbot.md) includes the `openviking_rule_search` tool (Beta), available on demand after connecting to OpenViking.

Other Agents can use the Python HTTP SDK or HTTP API and install the companion Skills. Copy each complete directory to the Agent's configured Skills directory, then load it:

| Task | Companion Skill |
| --- | --- |
| Prepare sources, compile, and inspect results | [add-rules](https://github.com/volcengine/OpenViking/tree/main/examples/skills/add-rules) |
| Build task context, recall, and check rules | [openviking-rule-search](https://github.com/volcengine/OpenViking/tree/main/examples/skills/openviking-rule-search) |

Skills provide operational and behavioral instructions; the Agent still needs callable interfaces. The current Beta is invoked explicitly by the user or Agent.

## Related documentation

- [Rules (Beta)](../concepts/17-rule-scenarios.md)
- [Rules API (Beta)](../api/26-rules.md)
- [Background Tasks](../api/17-tasks.md)
- [Application workflows](./01-overview.md)
