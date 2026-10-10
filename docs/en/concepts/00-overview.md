# Concepts and principles

Follow this reading path through OpenViking's context model, processing flow, architecture and governance. If you have not used OpenViking yet, [import and retrieve your first document](../getting-started/02-quickstart.md), then return here to understand how the result was produced.

## Start with the context model

Read these three pages in order to understand what is stored, where it lives and how much to read:

1. [Context Types](02-context-types.md): the roles of resources, memories and skills.
2. [Viking URI](04-viking-uri.md): how to locate context and distinguish user, Peer and shared spaces.
3. [Context Layers (L0/L1/L2)](03-context-layers.md): the relationship between abstracts, overviews and source content, including the limits of layered reading.

## Follow processing and memory

Trace the paths from source ingestion and recorded conversations:

- [Context Extraction](06-extraction.md): how sources are parsed, organized and processed semantically.
- [Retrieval Mechanism](07-retrieval.md): how relevant context is found and how `find` differs from `search`.
- [Session Management](08-session.md): how messages, session commits and memory extraction connect.

When you are ready to build, choose an [application workflow](../workflows/01-overview.md). Look up parameters and responses in the [reference](../reference/01-overview.md).

## Explore architecture and governance by question

| Question | Reading path |
| --- | --- |
| How do modules work together, and where is data stored? | [Architecture Overview](01-architecture.md) → [Storage Architecture](05-storage.md) |
| When is processing complete, and how does recovery work? | [Queue State and Completion Semantics](16-queue-lifecycle.md) → [Path Locks and Crash Recovery](09-transaction.md) |
| How do primary and backup writes, reads and observation work? | [Multi-Write Storage](14-multi-write-storage.md) → [Metrics](12-metrics.md) |
| Who can see which content, and how are permissions inherited? | [Multi-Tenant](11-multi-tenant.md) → [Resource Access Control (ACL)](15-acl.md) |
| How are data encrypted and skill privacy handled? | [Data Encryption](10-encryption.md) → [Privacy Configs and Skill Privacy Extraction/Restore](13-privacy.md) |

Configuration and deployment steps live in [Deploy & Operate](../guides/00-overview.md). Concept pages explain behavior and constraints; task guides explain how to configure and verify them, with links between the two.

## See an application example

[VikingBot](15-vikingbot.md) shows how an agent combines context with multiple conversation channels. To run it, continue to [VikingBot Installation and Configuration](../guides/17-vikingbot.md). For other existing tools, [choose an agent integration](../agent-integrations/01-overview.md).
