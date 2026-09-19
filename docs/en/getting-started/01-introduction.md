# Introduction

OpenViking is an open-source context database for AI agents. It stores resources, memories, and skills in a virtual file system, so an application can browse known paths, retrieve relevant context, and load only the detail it needs.

Use it when an agent needs to reuse documents and experience across sessions, with one place to organize and retrieve that context.

## Start with your task

| I want to… | Start here |
| --- | --- |
| Connect to a service and retrieve my first document | [Quick Start](./02-quickstart.md) |
| Connect an existing agent or coding tool | [Agent Integrations](../agent-integrations/01-overview.md) |
| Use OpenViking from a terminal | [CLI Setup](./05-cli-setup.md) |
| Deploy and operate a shared server | [Deployment](../guides/03-deployment.md) and [Authentication](../guides/04-authentication.md) |
| Build against the SDK or HTTP API | [API Reference](../api/01-overview.md) |

## How context is organized

Each file or directory has a `viking://` URI. Use a known URI to list or read context, or search when you do not know where it lives.

| Context | What it contains | Learn more |
| --- | --- | --- |
| Resources | Documents, repositories, and other reference material | [Resources](../api/02-resources.md) |
| Memories | User preferences, entities, events, and experience extracted from sessions | [Memory](../api/16-memory.md) |
| Skills | Instructions and supporting files for reusable agent workflows | [Skills](../api/04-skills.md) |

Shared resources live under `viking://resources/`. User context lives under `viking://user/{user_id}/`, with Peer-specific context under `peers/{peer_id}/`. Shared skills can live under `viking://agent/skills/`. See [Viking URI](../concepts/04-viking-uri.md) for scope and path rules.

## Load context in layers

OpenViking can generate directory summaries during semantic processing:

| Layer | Content | Default body limit |
| --- | --- | --- |
| L0 | Abstract for quick filtering | 256 characters |
| L1 | Overview for navigation | 4,000 characters |
| L2 | Original content for detailed reading | No uniform limit |

L0 and L1 are directory sidecars, not a pair of summaries attached to every file. Their availability depends on processing state and configuration. See [Context Layers](../concepts/03-context-layers.md).

[Retrieval](../concepts/07-retrieval.md) combines semantic matching with directory traversal. Use `find` for retrieval without session context, or `search` when session context should inform the query. [Observability](../guides/05-observability.md) helps inspect processing and retrieval behavior.

## Build memory from sessions

Applications record messages in a session and commit it for asynchronous memory extraction. The active memory policy determines which memories are created or updated for the user or Peer. Integration plugins can automate parts of this workflow; check the integration's supported behavior before relying on it. See [Sessions](../concepts/08-session.md) and [Memory Configuration](../guides/01-configuration.md).

For implementation details, read the [Architecture](../concepts/01-architecture.md). For release-specific changes, check [GitHub Releases](https://github.com/volcengine/OpenViking/releases).
