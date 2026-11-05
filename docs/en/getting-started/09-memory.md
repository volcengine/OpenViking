# Memory use

Memory lets an agent reuse preferences, project context, and working experience in a new session, reducing repeated explanations. After a session is committed, OpenViking can extract information worth retaining for agents to retrieve and use in later tasks.

This walkthrough uses the fictional Cape project. Save its review conventions, then start a new session and ask VikingBot to prepare a review outline using those memories. You will inspect the memory text, check retrieval sources, and confirm that the answer follows the conventions.

## Use cases

| Scenario | Information to retain | Later use |
| --- | --- | --- |
| Personalized answers | Preferred language, summary format, and writing style | Organize answers in the user's preferred form |
| Project collaboration | Project context, prior decisions, and file-naming conventions | Continue project work in a new session |
| Reusing experience | Effective approaches and considerations from past work | Retrieve experience when a similar problem arises |

Memory extraction and organization depend on the input and server configuration. One conversation may produce several memories or update existing ones. See [Memory types](../api/16-memory.md#built-in-memory-types) for the categories.

<a id="prepare-an-isolated-experiment"></a>

## Prepare the environment

Follow the [Quickstart](02-quickstart.md) to connect to the service and enable and configure [VikingBot](11-vikingbot.md). The server needs working memory-extraction and Embedding models, and VikingBot needs access to OpenViking retrieval tools.

You will save a session through the CLI and recall its memories through VikingBot. Connect both to the same service with the same user identity so the new session can access the saved memories. This example uses the user's self memory space; see the [integration capability comparison](../agent-integrations/16-capability-reference.md) for identity and Peer scope in project plugins.

These operations call models and write fictional project information into the current user's memory. A dedicated demo account makes cleanup easier. Use the session names below, or choose new names and update the subsequent commands if you have used them before.

<a id="session-a-save-conventions"></a>

## Save project context

Create a session to record the Cape review conventions. `--no-auto-commit` leaves submission to the manual commit below:

```bash
ov session new --session-id cape-docs-a --no-auto-commit
```

Add a user message describing the context to reuse in future reviews:

```bash
ov session add-message cape-docs-a --role user --content "This is the fictional Cape project. Each review covers exactly two risks. Summaries use three bullet points in Chinese, and the delivery file is named cape-review.csv. Please follow these conventions when preparing future Cape reviews."
```

Commit the session to archive the message and start memory extraction:

```bash
ov session commit cape-docs-a
```

Record the returned `task_id` and `archive_uri`. `accepted` means the messages have been archived and memory extraction is processing in the background. Next, check the task to confirm that memories have been generated.

## Inspect the memories

Replace `<task_id>` with the task ID returned by the commit:

```bash
ov task status <task_id>
```

Read the results after the status becomes `completed`. Check every 5 seconds for an initial 2 minutes; if processing continues, keep the task ID and check later. For `failed` or `cancelled`, inspect the task error or cancellation reason before deciding how to proceed.

Replace `<archive_uri>` with the returned archive URI to read the memory changes from this commit:

```bash
ov read "<archive_uri>/memory_diff.json"
```

Find a `uri` in `operations.adds` or `operations.updates`, then copy it to read the memory text:

```bash
ov read "<memory-uri>"
```

Check for all three conventions: two risks per review, three summary bullet points in Chinese, and the delivery filename `cape-review.csv`. Memory filenames and categories can vary; use the returned URI.

<a id="session-b-use-the-background"></a>

## Use memory in a new session

Use a new VikingBot session ID and provide only the project name and new task. The agent retrieves the risk count, summary format, and filename from saved memory:

```bash
ov chat --session cape-docs-b -m "Search saved Cape project memories. Prepare the next review outline following the project conventions and suggest the delivery filename. Cite the memory URIs used."
```

Check that the answer has space for two risks, three Chinese summary bullet points, and the filename `cape-review.csv`. No specific risks were supplied, so the outline can mark their content as needing input.

Use tool-call output or Bot logs to check saving, retrieval, and use:

| Stage | What to check |
| --- | --- |
| Saved | `memory_diff.json` records changes, and the memory text contains the conventions |
| Retrieved | Retrieval tool results contain the corresponding memory URI and content |
| Used | The new answer follows the conventions, and its memory citations match the retrieval results |

Start another session to check that the answer stays relevant to the current task:

```bash
ov chat --session cape-docs-c -m "What is 17 plus 25?"
```

The answer should be 42, with no need to include the Cape review format or filename.

<a id="locate-a-problem"></a>

## Troubleshooting

| Observation | What to check |
| --- | --- |
| Commit returns `skipped` | No messages were available to archive; use `ov session get cape-docs-a` to check whether messages were added or already committed |
| Task fails or remains incomplete | Inspect task status and errors, model connectivity, and server memory settings |
| Expected memories are missing | Check the original message, `memory_diff.json`, and effective extraction policies; the same information may already exist in memory |
| Saved memory is not retrieved in the new session | Compare the CLI and VikingBot service URLs, user identities, memory scope, and retrieval-tool availability |
| Retrieved memory is not used in the answer | Inspect tool results and agent instructions, then ask the agent to revise the answer using the retrieved memory |

## Continue

Try your own project context or answer preferences and use [Evaluation](06-evaluate.md) to record results for related and unrelated tasks. For existing agents, see the [integration capability comparison](../agent-integrations/16-capability-reference.md) for session capture, commit, and retrieval behavior. See the [Sessions API](../api/05-sessions.md) for parameters and the change-record format.

When finished, use this commit's `memory_diff.json` to identify the demo memories in Studio, then clean up sessions and memories as needed. If the commit updated existing memories, review the changes first and preserve information you still need.
