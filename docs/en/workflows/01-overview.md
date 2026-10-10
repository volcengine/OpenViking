# Application workflows

Complete your [first import and retrieval](../getting-started/02-quickstart.md), then choose a task below. If you use an existing agent, start with [Connect Agents](../agent-integrations/01-overview.md). If you are writing an application, choose a client through the [SDK, HTTP and CLI conventions](../api/01-overview.md).

| Task | Reading order | Check the result |
| --- | --- | --- |
| Retrieve documents or code in an application | [Import resources](../api/02-resources.md) → [Retrieve](../api/06-retrieval.md) → [Read content](../api/12-content.md) | Ask a question with a known answer, read the returned URI and compare it with the source |
| Reuse memory across sessions | [Session and memory model](../concepts/08-session.md) → [Session API](../api/05-sessions.md) → [Memory API](../api/16-memory.md) | Commit a session, confirm background completion, then inspect the memories and their ownership |
| Reuse agent skills | [Skills API](../api/04-skills.md) → [Privacy model](../concepts/13-privacy.md) | Inspect the installed skill, its visibility and how it is invoked |
| Produce a wiki, graph or report from sources | [Context compilation](../context-compilation/01-overview.md) → choose an output tutorial | Check task status, output files and how the output relates to the input sources |
| Sync or migrate existing context | [Project asset sync](../guides/18-openviking-assets.md), [OVPack](../guides/09-ovpack.md) or [version recovery](../guides/15-snapshot.md) | Follow the relevant guide to verify sync state, imported data or restored content |

## Verify retrieval before wiring it into an application

Use a small document with known content. Import it, wait for processing, retrieve a match and read the source. A successful health check only confirms the service can respond; an empty search result alone does not prove ingestion failed. Use [background tasks](../api/17-tasks.md) and [diagnostic tools](../guides/05-observability.md) to distinguish processing state from retrieval results.

Add [Resource Watches](../api/15-watches.md) when remote sources need recurring updates. To change content processing, see [prompt customization](../guides/10-prompt-guide.md).

## Verify memory across sessions

Decide the [user, Peer and shared-context boundaries](../concepts/11-multi-tenant.md) before recording messages and committing sessions. Extraction depends on configuration and policy; a successful commit does not guarantee a particular memory will be created. Inspect the output through the [Session API](../api/05-sessions.md) and [Memory API](../api/16-memory.md). For existing tools, check the [integration capability comparison](../agent-integrations/16-capability-reference.md) to learn which steps the plugin automates.

## Add access control and operations before rollout

Shared services need [authentication](../guides/04-authentication.md) and [ACLs](../concepts/15-acl.md). Find deployment, monitoring and upgrade paths in [Deploy & Operate](../guides/00-overview.md). For parameters, responses and errors, use the [reference index](../reference/01-overview.md).
