# Choose a deployment path

If someone has already provided a service URL and API key, go directly to [CLI setup](../getting-started/05-cli-setup.md) or [agent integration](../agent-integrations/01-overview.md). The paths below are for people deploying and operating the service.

| Goal | Reading order | Completion check |
| --- | --- | --- |
| Try it locally | [Quick start](../getting-started/02-quickstart.md) | Import a document, retrieve a match and read its content |
| Host a shared service | [Self-host a server](03-deployment.md) → [Authentication](04-authentication.md) → [Public access](12-public-access.md) | Verify persistence, authorized access, unauthorized rejection and initial data processing |
| Install an enterprise delivery | [Pre-deployment checklist](19-deployment-checklist.md) → [Install and verify](20-private-deployment.md) → [Upgrade and troubleshoot](21-private-operations.md) | Verify each item in the delivery manifest and installation guide |

Enterprise deployment depends on delivery artifacts, images and environment requirements; check the manifest first.

## Configure models and access

For initial model setup, use [Configure models and services](01-configuration.md). For a field lookup, use [server configuration fields](../configuration/01-server.md). The server's `ov.conf` and the client's `ovcli.conf` have different responsibilities; see [client configuration](../configuration/02-client.md) for client fields.

Configure authentication and reverse proxies before exposing remote access. For MCP clients that require OAuth, continue to [OAuth 2.1](11-oauth.md). For shared installations, understand [multi-tenant identity](../concepts/11-multi-tenant.md) and [ACL inheritance](../concepts/15-acl.md). If you need encryption at rest, follow the [encryption guide](08-encryption.md).

## Check the running service

Choose a tool through the [observability and diagnostics guide](05-observability.md). Inspect individual requests with [operation telemetry](07-operation-telemetry.md), trends with [Prometheus / Grafana](11-grafana-prometheus.md), and metric meanings in [metric definitions](../concepts/12-metrics.md). See the [FAQ](../faq/faq.md) for common problems.

Add [multi-write storage](13-multi-write-storage.md), [RAGFS caching](14-ragfs-cache.md) or [cuVS](16-cuvs.md) when needed. These are optional configurations rather than prerequisites for a first deployment.
