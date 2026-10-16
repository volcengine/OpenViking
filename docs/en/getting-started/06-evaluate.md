# Evaluate OpenViking on your tasks

After your [first import and retrieval](02-quickstart.md), try documents or conversations from your own work. Check whether OpenViking helps you find evidence, reuse context or produce useful content. Start with one scenario, then expand after you have a result.

| What you want to improve | Start here | What to check |
| --- | --- | --- |
| Spend less time looking through project files | [Import and retrieve documents](02-quickstart.md) | Find the source needed to answer the question, beyond words that merely look similar |
| Reuse background in a new agent session | [Choose an agent integration](../agent-integrations/01-overview.md) | Confirm that memory is stored and recalled for a relevant task without entering the same background again |
| Organize scattered sources into readable content | [Context compilation](../context-compilation/01-overview.md) | Check which sources the output uses, whether its structure helps the reader, and what it omits or gets wrong |

## Check retrieval with your own sources

Choose a small set of sources you know, such as project notes, an operating manual or an incident review. Import them using the [quick start](02-quickstart.md). Prepare questions with known answers, recording the expected source for each. After retrieval, read the returned URI and compare it with the original text.

Include a direct-answer question, one that requires two pieces of information, and one the sources cannot answer. Record whether the evidence was found, whether anything essential was missed, and whether a subsequent agent answer follows the source. `find` returns URIs and scores; your agent or application still needs to read and use the content to generate an answer.

Add more sources after this first check. If results change, you can distinguish ingestion, retrieval and answer-generation problems. Use [observability and diagnostics](../guides/05-observability.md) to inspect processing state and request behavior.

## Check memory across two sessions

Use the [capability comparison](../agent-integrations/16-capability-reference.md) to check which capture, commit and recall steps your plugin automates. Then follow that plugin's verification instructions.

1. In the first session, provide a non-sensitive preference or piece of project context that a later task will need.
2. End or commit the session using the plugin's mechanism. Check processing state, the actual memory output and its user or Peer ownership. A successful commit does not guarantee a particular memory will be created.
3. Start a new session with the same identity. Ask for a task that needs the earlier context without entering that context again. Inspect recall records and the final answer to confirm the stored information was used.
4. Ask an unrelated question and check whether old context is carried into the answer inappropriately.

Installing a plugin is only part of verifying memory. If no memory was produced, inspect [sessions and extraction](../concepts/08-session.md). If memory exists but is not recalled, inspect identity, retrieval scope and plugin behavior. For applications that manage their own sessions, follow the [application workflows](../workflows/01-overview.md) to the relevant APIs.

## Check one compiled output

Choose a [knowledge wiki](../context-compilation/02-llm-wiki.md), [knowledge graph](../context-compilation/03-knowledge-graph.md) or [daily report](../context-compilation/04-daily-report.md). Follow the tutorial to prepare sources and run the task. Confirm completion and the output location, then review the content:

- Can you verify key facts against their sources?
- What is missing, repeated or unsupported?
- Can you use the output for the reading or organizing task you intended?

Start with familiar sources so you can recognize mistakes. Task completion and generated files show that processing finished; the output still needs review before you rely on it.

## Choose what to do next

| Your result | Next step |
| --- | --- |
| Retrieval meets your needs and you want it in a daily tool | [Connect an existing agent](../agent-integrations/01-overview.md) |
| You have verified the capability and want it in your application | [Application workflows](../workflows/01-overview.md) |
| You want to use the agent included with OpenViking | [VikingBot Installation and Configuration](../guides/17-vikingbot.md) |
| You are ready to share a service with your team | [Choose a deployment path](../guides/00-overview.md), then verify identity and access control |
| You want to understand why the system behaves this way | [Concepts and principles](../concepts/00-overview.md) |

Keep your questions, expected sources and observed results. Reuse them when changing configuration or upgrading. When comparing results before and after a change, keep tasks, models and inputs consistent; a retrieval score or one successful run does not establish an improvement.
