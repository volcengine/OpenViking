# Rules (Beta)

::: warning Beta notice
Rules are currently in Beta. The interfaces, parameters, and workflows described here may introduce breaking changes in future releases. Before upgrading, review the release notes and current documentation, and validate your existing integration in a test environment.
:::

A Rule stores an explicit requirement an agent should follow under specific conditions, such as a release check or a prohibition in a coding standard. OpenViking compiles these requirements into rules and retrieves them for a task. The agent then checks its actions or answers against the rules and source evidence.

In this walkthrough, save a release requirement for the Lighthouse demo project, then review two release notes. You will inspect the stored rule, retrieve it, and compare the review before and after the missing information is added.

## Use cases

| Scenario | Example rule | How the agent uses it |
| --- | --- | --- |
| Release review | Project release notes must include rollback steps | Check the notes before release and identify missing information |
| Code review | Team policy prohibits logging access keys in application code | Check changes and cite relevant code |
| Agreement review | User-provided obligations, conditions, and exceptions | Check evidence and distinguish satisfied, violated, and undetermined requirements |

A Skill describes how to do something; a Rule describes which requirements apply under which conditions. Explicit obligations and prohibitions belong in Rules. Personal preferences and ordinary suggestions can remain in memory.

## Prepare the environment

Prepare an OpenViking service with Rule support and a Python environment; see the [Quickstart](02-quickstart.md#prerequisites) for setup. The server needs working Embedding and Compiler models. The Compiler defaults to the VLM; see [Rules configuration](../configuration/01-server.md#rules). Compilation and retrieval call models and persist the demo rule in the current user's rule library, so a test user is suitable for this exercise.

Install the SDK in your Python environment:

```bash
python -m pip install --upgrade openviking-sdk
```

This example uses a Python HTTP SDK with `rule_compile()` and `rule_search()`. Both the server and SDK need Rule (Beta) support. For a managed service, confirm availability with the provider. See [API conventions](../api/01-overview.md) for connection and identity settings.

Put the following Python snippets into one `rule_demo.py` file in order. Set the service URL and current user's API key; omit `api_key` for a local service with authentication disabled.

## Save a rule

Use a requirement scoped to the demo project, with an explicit trigger and obligation:

```python
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
task = client.rule_compile(
    instruction=(
        "When reviewing release notes for the Lighthouse demo project, "
        "the release notes must include rollback steps."
    ),
)
print("Compile task:", task["task_id"])
```

The service returns a background task ID. Wait for compilation and its associated indexing task, then read the generated rule:

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


result = wait_for_task(task["task_id"])["result"]
print("Operations:", result["operations"])
write_task_id = result.get("write_result", {}).get("task_id")
if write_task_id:
    wait_for_task(write_task_id)
for uri in result["written_uris"]:
    print(uri)
    print(client.read(uri))
```

Check that the rule retains the Lighthouse demo project scope, the release-note review condition, and the requirement to include rollback steps. A repeated submission may return `NOOP`, meaning an existing rule covers the input; continue to retrieval. `UNCERTAIN` or `REJECT` explains why no rule was written; address that reason first. After a timeout, use the printed task ID to check progress again.

## Retrieve for a task

Use this fixed fictional input: the complete release note for the Lighthouse demo project is “This release adds export progress indicators.” Submit that evidence together with the review goal:

```python
matches = client.rule_search(
    task_context=(
        "Review release notes for the Lighthouse demo project. "
        "The complete release note has been read: "
        "This release adds export progress indicators. "
        "Next, check release requirements and list missing information."
    ),
)
print(matches["rendered"])
print("Warning:", matches.get("warning"))
print("Scenario selection:", matches.get("scenario_selection"))
```

Save the file and run it:

```bash
python rule_demo.py
```

Find the rule text and URI in the output and check that it contains the rollback requirement. `rendered` contains the full rule text to give to an agent. Search retrieves potentially applicable rules; the agent checks whether the evidence satisfies them.

## Check the review

Append the complete `rendered` output to this prompt and give it to your agent. Both notes are demo materials; the agent only needs to read them and return a review:

```text
Using the rules below, review these two release notes for the Lighthouse demo project.

Version A, complete text: This release adds export progress indicators.
Version B, complete text: This release adds export progress indicators.
Rollback steps: Stop the new service, switch to the previous image, start it
with the previous configuration, and check the health endpoint.

Check each rule's conditions and exceptions. Cite its URI and the release-note
evidence. For each version, report whether the requirement is satisfied,
violated, or undetermined, and list any information to add.
Perform a text review only.

Complete retrieved rules:
(Paste the rendered output here.)
```

| Check | Expected observation |
| --- | --- |
| Applicability | Both notes belong to the Lighthouse demo project |
| Version A | Rollback steps are missing; the agent asks for them to be added |
| Version B | Rollback steps are present, satisfying this rule; their operational validity still needs deployment testing |
| Traceability | The answer cites the rule URI and the relevant release-note text |

These are acceptance criteria for the example; actual results depend on your model output. If retrieval returns no rules, check the compilation and indexing tasks, user identity, and task description. If a `warning` appears, inspect the indicated model configuration and have the agent check each condition. Empty results and pass-through scores still require an evidence review.

## Continue

After connecting [VikingBot](11-vikingbot.md), you can ask it to call `openviking_rule_search` for retrieval. In the current Beta, users or agents explicitly trigger compilation and search. Ordinary session commits do not automatically create Rules.

For batch imports or connecting your own agent, continue with [Rule validation](../workflows/02-rule-validation.md). See the [Rule lifecycle](../concepts/17-rule-scenarios.md#lifecycle) for pausing, updating, and archiving rules.
