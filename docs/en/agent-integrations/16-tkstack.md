# HTTP Integration for Multiple Local Agents

Use this integration when several local coding agents need to share one OpenViking server through HTTP. The setup pattern is generic, while the fork-local validation record at the end documents one tkstack deployment. It does not require an MCP client.

## Deployment boundary

The recommended boundary is:

```text
local agents -> OpenViking HTTP server -> storage and vector index
                             |
                             +-> LiteLLM for embedding and VLM requests
```

Keep the OpenViking server behind a private network or a loopback gateway. Use `api_key` authentication for a shared service. Use `trusted` only when a trusted gateway terminates authentication and injects the account/user identity; do not expose a trusted server directly to an untrusted network. See [Authentication](../guides/04-authentication.md) and [Multi-Tenant](../concepts/11-multi-tenant.md).

Treat the Markdown or Git repository that feeds the deployment as the human-readable source of truth unless the deployment explicitly defines OpenViking as authoritative. The OpenViking workspace and vector index should be recoverable derived state.

## Model configuration

OpenViking supports LiteLLM as an embedding and VLM provider. The following is a template; replace the model routes, vector dimension, paths, and credentials at deployment time. Do not commit API keys.

```json
{
  "storage": {
    "workspace": "/var/lib/openviking",
    "vectordb": {
      "name": "context",
      "backend": "local"
    }
  },
  "embedding": {
    "dense": {
      "provider": "litellm",
      "api_base": "http://127.0.0.1:4000/v1",
      "api_key": "${LITELLM_API_KEY}",
      "model": "<embedding-route>",
      "dimension": 768,
      "input": "text",
      "max_concurrent": 1,
      "max_retries": 0
    }
  },
  "vlm": {
    "provider": "litellm",
    "api_base": "http://127.0.0.1:4000/v1",
    "api_key": "${LITELLM_API_KEY}",
    "model": "<chat-route>",
    "max_concurrent": 1,
    "max_retries": 0,
    "stream": false
  },
  "server": {
    "host": "127.0.0.1",
    "port": 1933,
    "auth_mode": "api_key",
    "root_api_key": "${OPENVIKING_ROOT_API_KEY}"
  }
}
```

The example binds to `127.0.0.1` because it assumes that the agents, OpenViking, and LiteLLM run on the same host. If agents run on separate hosts, bind OpenViking to a private interface/address, restrict TCP port `1933` with the network firewall, and keep `api_key` authentication enabled. Never expose an unauthenticated or trusted-mode listener on a public interface.

Before indexing data, verify the exact LiteLLM routes with a minimal `/v1/models`, chat, and embeddings request. `openviking-server doctor` validates configuration readiness, but it does not prove that every proxy route can complete an end-to-end request. Model names that contain provider keywords can be auto-detected by LiteLLM; use the route format required by the proxy and test it directly before importing a large corpus.

Self-hosted OpenAI-compatible and LiteLLM embedding endpoints are dense-only. OpenViking does not automatically add a BM25 or other sparse fallback when the configured endpoint returns dense vectors. If Japanese exact matching is required, keep `grep` or a separate lexical index in the retrieval path. See [Embedding Configuration](../guides/01-configuration.md#embedding) for the provider and hybrid-search constraints.

## Client pattern

Use a user-scoped API key for each agent in production. The server resolves the account and user from the key, so agents can share an account while retaining an auditable identity. `actor_peer_id` is optional request-scoped actor metadata; it is not a substitute for the user API key.

```python
import os

import openviking as ov


client = ov.SyncHTTPClient(
    url=os.environ["OPENVIKING_URL"],
    api_key=os.environ["OPENVIKING_API_KEY"],
    actor_peer_id=os.environ.get("OPENVIKING_ACTOR_PEER_ID", "local-agent"),
    timeout=60,
)

try:
    client.initialize()
    result = client.find("shared deployment decision", limit=5)
    print(result)
finally:
    client.close()
```

For a trusted loopback gateway, pass the account/user identity supplied by that gateway instead of using this pattern. Do not mix identity headers with `api_key` mode; in authenticated mode the key is the source of tenant identity.

## Validation and fallback order

After adding a resource, wait for processing before searching:

```python
client.add_resource("/path/to/context.md", parent="viking://resources", wait=True)
client.wait_processed(timeout=300)
```

Use the following validation order:

1. `GET /health` to confirm the server process.
2. `ls` and `read` to confirm the resource tree and content.
3. `grep` to confirm exact lexical retrieval.
4. `find` and `search` to confirm semantic retrieval.
5. A second client identity and a small concurrent request set to confirm sharing and latency.

Do not treat a successful upload or completed embedding queue as proof that semantic retrieval works. If `grep` succeeds while `find` returns no results, inspect the server queue and VLM/embedding logs before importing more data. OpenViking issue [#677](https://github.com/volcengine/OpenViking/issues/677) documents the same symptom pattern for uploaded resources; a local reproduction means the deployment is not ready for semantic production until the root cause is isolated or a lexical fallback is in place.

## Fork-local validation record

The following fork-local smoke test was run against OpenViking `0.4.13` on 2026-08-16 with Python 3.13 and a local HTTP server. It is an environment-specific record, not a compatibility guarantee for every provider or deployment.

| Check | Result |
| --- | --- |
| Install with Python/uv, without Bun | Pass |
| HTTP health on port 1933 | Pass |
| Read/list/grep from four agent identities with 12 concurrent requests | Pass; all requests succeeded, maximum observed latency about 147 ms |
| Japanese Markdown storage and exact retrieval | Pass |
| Semantic `find` for the uploaded resource | Blocked; zero results in this run |
| Existing LiteLLM VLM route | Blocked; the configured model alias was routed as DashScope and returned 429 |
| Existing LiteLLM embedding route | Blocked upstream by an LM Studio `node ENOENT` failure; a temporary local GGUF embedder was used for the storage smoke test |

The result validates the HTTP/read/grep path for the pilot but does not validate production semantic retrieval. Re-run the semantic checks after the LiteLLM routes are healthy, and record the exact OpenViking version, model IDs, vector dimension, and backend in the deployment change.

## Related documentation

- [Quick Start: Server Mode](../getting-started/03-quickstart-server.md)
- [Authentication](../guides/04-authentication.md)
- [Multi-Tenant](../concepts/11-multi-tenant.md)
- [Embedding and VLM Configuration](../guides/01-configuration.md)
