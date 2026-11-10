# Feature overview

OpenViking is a context database for AI agents. It organizes resources, memories, skills, and rules as browsable, searchable files. OpenViking supplies context; an agent reads that context to answer questions or carry out tasks.

Start with the [quickstart](02-quickstart.md) to get an answer you can check against its source. Then choose a task below. You do not need to read every page in sequence.

## Start with a task

| Your task | What OpenViking provides | Start here |
| --- | --- | --- |
| Answer questions using project sources | Import, retrieve, and read source content | [Resource retrieval](08-resources.md) |
| Carry background into a new session | Record sessions, extract memory, and recall it for relevant tasks | [Memory use](09-memory.md) |
| Reuse a working method | Store and retrieve a Skill for an agent to read and apply | [Skill use](10-skills.md) |
| Check tasks and actions against requirements | Compile mandatory constraints and recall rules for an agent to check against the current task | [Rules (Beta)](16-rules.md) |
| Organize sources into readable knowledge | Compile sources into a wiki or another Skill-defined output | [Context compilation](13-compile.md) |

<a id="three-kinds-of-context"></a>

## Context types

| Type | What it stores | How to check its value |
| --- | --- | --- |
| **Resource** | External documents, web pages, and code | Key conclusions in an answer can be verified against sources |
| **Memory** | Background, preferences, and experience extracted from sessions | A new session uses relevant background without you repeating it |
| **Skill** | Workflow instructions and supporting files | After reading the Skill, the agent produces an output that meets its requirements |
| **Rule (Beta)** | Mandatory requirements under specific conditions, including obligations, prohibitions, and exceptions | The agent checks applicable rules against task evidence and adjusts its actions or answer |

A `viking://` URI identifies content. Read it directly when you know the location; otherwise retrieve matches and open the source. Directory abstracts and overviews support progressive loading; their generation depends on the processing method and configuration.

**Rules:** For example, “A repository that declares itself open source must provide a locatable license text.” Explicitly compile mandatory requirements from a policy into Rules, then recall them for a specific task. OpenViking supplies the rules and their applicability conditions; the agent uses current evidence to check whether the requirements are met. Rules are in Beta. See [Rules](../concepts/17-rule-scenarios.md) for concepts and usage.

## Choose an entry point

![The CLI, existing agents, and VikingBot connect to the OpenViking context service. The gateway connects compatible clients.](/quickstart/overview.en.svg)

| Entry point | Use it for |
| --- | --- |
| **CLI** | Browse, import, retrieve, and read from a terminal; use `ov chat` with an enabled VikingBot. See [CLI basics](05-cli-setup.md). |
| **Existing agent** | Add context and memory to your daily tool. Capture, commit, and recall behavior varies by plugin; verify behavior in a new session after installation. |
| **VikingBot** | Use the built-in agent for answers and tool-based tasks. The [quickstart](02-quickstart.md) follows this route. |
| **Gateway** | Connect a compatible client's model requests to context and memory. Verify the integration through capture, extraction, and recall results. |
| **SDK / HTTP** | Control import, retrieval, sessions, and data management from your own application. |

OpenViking manages and supplies context. The CLI, plugins, gateway, and agents have distinct responsibilities. For deeper integration, see [Agent integrations](../agent-integrations/01-overview.md), [Gateway setup](12-gateway.md), or [SDK and HTTP conventions](../api/01-overview.md).

## Try skills and knowledge organization

**Skills:** Choose a workflow with a concrete output, install its Skill, confirm the agent can discover and read it, then run a task and inspect the result. OpenViking stores and retrieves the Skill; the agent activates it, manages tool permissions, and executes the task. See [Skills](10-skills.md) for the operations.

**Knowledge organization:** Prepare sources, compile them with the Wiki Skill, wait for completion, and open an output page to check its evidence and omissions. Skill-based compilation needs a configured Compile Runtime. Compare the output with the original sources to check its accuracy. Continue with the [Wiki example](13-compile.md).

## Next steps

Once you have a result, [evaluate your own tasks](06-evaluate.md). If you do not have a service endpoint, compare [deployment modes](07-options.md).

<!-- Keep incoming links to the previous headings working. -->
<a id="what-openviking-does"></a>
<a id="start-with-your-task"></a>
<a id="turn-a-first-result-into-a-working-habit"></a>
<a id="how-context-is-organized"></a>
<a id="load-context-in-layers"></a>
<a id="build-memory-from-sessions"></a>
