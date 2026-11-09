# Tune recall latency

Use this guide after connecting a plugin. For a first installation, start with [integration selection](01-overview.md).

Query expansion and recall compression add model calls. Disable both when response time takes priority; retrieval, budgets, and cross-turn deduplication remain enabled:

```bash
export OPENVIKING_RECALL_QUERY_EXPANSION=off
export OPENVIKING_RECALL_COMPRESS=off
```

Or configure them in `~/.openviking/ovcli.conf`:

```json
{
  "plugin": {
    "recallQueryExpansion": "off",
    "recallCompress": "off"
  }
}
```

### Choose a compression mode

| Value | Behavior |
| --- | --- |
| `off` | Do not compress recall results |
| `server` | Request compression on the OpenViking server |
| `client` | Use a local compressor only; supported by Claude Code and Codex |
| `auto` | Prefer a local compressor when available; otherwise request automatic server processing |

Claude Code and Codex default to `auto`. Their local compressors are `claude -p` and `codex exec`; see [Recall digests](./16-capability-reference.md#_3-2-5-recall-digest). Other integrations that support cloud compression retain `off` as their default and require explicit opt-in. Server compression is supported by Claude Code, Codex, OpenCode, DSH, pi, Cursor, TRAE, TRAE CN, ZCode, OpenClaw, and the Hermes external plugin. The bundled Hermes provider does not support recall digests. Compression requires a server with context-search rewrite support.

These settings control automatic recall. Explicit MCP `search` calls use the arguments supplied in that call. See the [shared plugin documentation](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/README.md#cloud-recall-compression) for details and older-server fallback behavior.

Shared plugins read the `plugin` section in `ovcli.conf`; `plugin.<harness>` overrides a setting for one client. Environment variables take precedence; the older `OPENVIKING_RECALL_REWRITE` still works as an alias for `OPENVIKING_RECALL_COMPRESS`. See [Plugin settings](../configuration/02-client.md#plugin-settings). Restart the agent after changing settings so its hooks load the new configuration. These are plugin-client settings; the server's `ov.conf` does not need to change.

### Request timeout

Query expansion, retrieval, and digest compression run in sequence. Query expansion defaults to a 5-second timeout (`retrieval.recall_intent_timeout_s`); digest rewriting defaults to 30 seconds (`retrieval.recall_rewrite_timeout_s`).

`OPENVIKING_RECALL_CONTEXT_TIMEOUT_MS` or `plugin.recallContextTimeoutMs` sets the client's timeout for the entire context request. When unset, the client waits the plugin's ordinary timeout, raised to at least 15 seconds when the request carries a session and query expansion is not `off` and at least 45 seconds when it asks for a digest. An override should exceed the server timeouts the request will spend and stay below the host's hook timeout. Ending the request early discards the entire response.
