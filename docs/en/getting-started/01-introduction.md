# What OpenViking does

OpenViking is an open-source context database for AI agents. It organizes sources, memories and skills in one virtual file system so agents can find relevant content and reuse it in later tasks.

If your sources are hard to find, you keep repeating background in new conversations, or useful experience is difficult to reuse, choose a path below.

## Start with your task

| What you want to do | First step | Then continue to |
| --- | --- | --- |
| Help an agent find evidence in project sources | [Import and retrieve your first document](02-quickstart.md) | Replace the sample with your sources and [evaluate the result](06-evaluate.md) |
| Add cross-session memory to an existing agent | [Choose an agent integration](../agent-integrations/01-overview.md) | Follow its installation guide, then [check memory across two sessions](06-evaluate.md#check-memory-across-two-sessions) |
| Use context in your own application | [Application workflows](../workflows/01-overview.md) | Choose a resource, session or skill workflow, then look up its APIs |
| Turn sources into a wiki, graph or report | [Context Compilation Overview](../context-compilation/01-overview.md) | Choose an output and check it against familiar sources |

**Not sure where to start?** [Import and retrieve your first document](02-quickstart.md). This path uses a small document to connect, import, retrieve and read the source, without requiring you to study the architecture first.

If you already have a service URL and API key, [install and use the CLI](05-cli-setup.md). To provide a service yourself, choose a [deployment path](../guides/00-overview.md). To use the agent included with OpenViking, see [VikingBot Installation and Configuration](../guides/17-vikingbot.md).

## Turn a first result into a working habit

1. **Get a first result.** Confirm that you can read imported content, beyond checking service health.
2. **Try your own task.** Use sources with known answers or context needed across sessions. Check what is found and used through [Evaluate OpenViking on your tasks](06-evaluate.md).
3. **Connect it to your work.** Choose an agent, application or compilation workflow. Add deployment and access controls when you need to share the service.

If something fails, use [observability and diagnostics](../guides/05-observability.md) to distinguish connection, processing and retrieval problems.

## How context is organized

| Context | What it stores |
| --- | --- |
| Resources | Documents, code repositories and other external sources |
| Memories | Preferences, facts and experience extracted from sessions |
| Skills | Instructions and supporting files for reusable workflows |

Content is addressed by a `viking://` URI. Browse a known path, or retrieve a match and then read it. [Context Types](../concepts/02-context-types.md) and [Viking URI](../concepts/04-viking-uri.md) explain the objects and their scope.

## Load context in layers

During semantic processing, OpenViking can generate directory abstracts (L0) and overviews (L1), then load source content (L2) when needed. L0 and L1 are directory sidecars; availability depends on processing state and configuration. They are not a fixed pair of summaries for every file. See [Context Layers](../concepts/03-context-layers.md).

## Build memory from sessions

Applications record messages and commit sessions for asynchronous extraction under a memory policy. Plugins can automate some of these steps; check the [capability comparison](../agent-integrations/16-capability-reference.md) for their scope. Inspect the actual output and recall behavior to learn what was retained and used.

For a reading path through these mechanisms, start with [Concepts and principles](../concepts/00-overview.md). To continue with a task, choose a link under “Use it in your work” in the sidebar.
