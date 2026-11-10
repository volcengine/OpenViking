# Roadmap

This page describes the current `main` branch and future directions, without committing to release dates or priorities. For packaged versions, check [release notes](https://github.com/volcengine/OpenViking/releases).

## Implemented on main

- **Context and retrieval:** L0/L1/L2 context layers, Viking URIs, semantic search, and context-aware retrieval. [Concepts](../concepts/03-context-layers.md)
- **Resources:** document, code, web, and media imports; scheduled refreshes for re-readable sources. Audio/video files can be stored; understanding requires an enabled, compatible VLM. [Resources](../api/02-resources.md)
- **Updates and history:** freshness-aware parent-summary refreshes and Git-backed snapshot commit, history, and restore. Parent refreshes can be deferred; snapshots require explicit commits and restore file content, not historical ACLs or vector indexes. [Context layers](../concepts/03-context-layers.md) · [Snapshots](../guides/15-snapshot.md)
- **Sessions and memory:** conversation tracking, memory extraction, and session archival. [Sessions](../concepts/08-session.md)
- **Access and integration:** HTTP API, SDKs, CLI, MCP, and agent plugins. [API overview](../api/01-overview.md) · [MCP guide](../guides/06-mcp-integration.md)
- **Operations:** JSON configuration (`ov.conf`), multiple model providers, tenant isolation, encryption, observability, and local/S3 storage. [Configuration](../guides/01-configuration.md) · [Deployment](../guides/03-deployment.md)

## Future directions

- Further distributed-storage development.
- More agent-framework adapters.

Track proposals and discuss scope in [GitHub issues](https://github.com/volcengine/OpenViking/issues). To contribute, see the [contribution guide](https://github.com/volcengine/OpenViking/blob/main/CONTRIBUTING.md).
