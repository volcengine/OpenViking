# MCP and Live Context API

This module registers Streamable HTTP MCP servers, synchronizes and searches their tool catalogs, invokes tools through OpenViking, and mounts tools as live context nodes. Read-only mounted tools execute transparently on `read`; tools explicitly marked non-read-only or destructive require explicit invocation. Every REST path starts with `/api/v1/mcps` and uses the [standard response envelope](01-overview.md#response-format).

> **Scope**
>
> This page describes **OpenViking-registered MCP Servers**: MCP servers registered
> in OpenViking for tool discovery, proxied invocation, and Live Context mounts. It does not
> describe the **OpenViking MCP Interface** that OpenViking exposes to external MCP clients,
> or **VikingBot-connected MCP Servers** configured directly under
> `bot.tools.mcp_servers`.

## Prerequisites and constraints

- MCP servers must use Streamable HTTP, and URLs may use HTTP or HTTPS. HTTP does not encrypt transport; users are responsible for assessing exposure risks for credentials and tool data.
- Server names may contain only letters, numbers, `_`, and `-`, up to 64 characters. Tool names must be valid OpenViking filenames.
- HTTP headers submitted at registration are stored within the account scope. APIs return only sorted `header_names`, never header values.
- Tool definitions can live in the user space at `viking://~/tools/<server>/<tool>.json` or in the account-shared space at `viking://agent/tools/<server>/<tool>.json`. Invocation and mount arguments are validated against the tool's `inputSchema`.
- `target_uri` accepts only `viking://~/tools` or `viking://agent/tools`. Registration and name-based operations default to the user space; list and search merge both spaces when omitted. The same server name may exist in both spaces.
- Both tools roots are managed directories. Do not mutate them with ordinary `mkdir`, `rm`, `cp`, or `mv`; use the MCP management APIs.
- A live context node URI must be a Viking URI available to the current account and end in `.md`.
- `wait` controls whether catalog or node writes wait for background processing. It is not a tool-call timeout. `timeout_seconds` is the timeout for each live tool invocation.

## Endpoint summary

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/v1/mcps/validate` | Check connectivity and discover tools without saving |
| POST | `/api/v1/mcps` | Register a server and publish its tool catalog |
| GET | `/api/v1/mcps` | List registered servers |
| GET | `/api/v1/mcps/{name}` | Read a server and its current tool definitions |
| GET | `/api/v1/mcps/status?name=...` | Check whether a server is reachable |
| POST | `/api/v1/mcps/{name}/sync` | Synchronize tools and report affected mounts |
| DELETE | `/api/v1/mcps/{name}` | Remove a server and its tool catalog |
| POST | `/api/v1/mcps/find` | Search tools semantically |
| POST | `/api/v1/mcps/invoke` | Invoke a tool |
| POST | `/api/v1/mcps/mount` | Create or replace one live context node |
| POST | `/api/v1/mcps/batch-mount` | Create or replace live context nodes in a batch |
| DELETE | `/api/v1/mcps/mount` | Remove one mount and its node |
| POST | `/api/v1/mcps/batch-unmount` | Remove mounts and nodes in a batch |
| GET | `/api/v1/mcps/mounts` | List mounts for a server or tool |

## MCP server lifecycle

### Validate and register

`POST /api/v1/mcps/validate` connects to the server, performs MCP initialization, and discovers tools without changing the registry or tool catalog. `POST /api/v1/mcps` performs the same checks, then registers the server and publishes definitions. It defaults to `viking://~/tools/<name>/`; set `target_uri` to publish under `viking://agent/tools/<name>/`. Registration fails when the name already exists in the selected space.

**Request body**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `name` | string | Yes | - | Server name |
| `url` | string | Yes | - | Streamable HTTP endpoint |
| `headers` | object | No | `{}` | HTTP headers sent to the upstream server |
| `target_uri` | string | No | `viking://~/tools` | User or shared tools root |
| `wait` | boolean | No | `false` | Registration only; wait for tool catalog processing |

Unknown fields return `INVALID_ARGUMENT`. Inject credentials from environment variables or a secret manager; do not put them in resource files or commit them to source control.

**Python SDK**

```python
import os
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(url="http://localhost:1933", api_key="your-key")

headers = {"Authorization": f"Bearer {os.environ['WEATHER_MCP_TOKEN']}"}
client.validate_mcp("weather", "https://mcp.example.com/mcp", headers=headers)
result = client.add_mcp(
    "weather",
    "https://mcp.example.com/mcp",
    headers=headers,
    target_uri="viking://agent/tools",
    wait=True,
)
```

**TypeScript SDK**

```typescript
const headers = { Authorization: `Bearer ${process.env.WEATHER_MCP_TOKEN}` };
await client.validateMCP("weather", "https://mcp.example.com/mcp", headers);
const result = await client.addMCP(
  "weather",
  "https://mcp.example.com/mcp",
  headers,
  { wait: true, targetUri: "viking://agent/tools" },
);
```

**Go SDK**

```go
headers := map[string]string{
    "Authorization": "Bearer " + os.Getenv("WEATHER_MCP_TOKEN"),
}
_, err := client.ValidateMCP(ctx, "weather", "https://mcp.example.com/mcp", headers)
if err != nil {
    return err
}
result, err := client.AddMCP(
    ctx,
    "weather",
    "https://mcp.example.com/mcp",
    headers,
    &openviking.WaitOptions{Wait: true, TargetURI: "viking://agent/tools"},
)
```

**HTTP API**

```bash
curl -X POST http://localhost:1933/api/v1/mcps/validate \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "weather",
    "url": "https://mcp.example.com/mcp",
    "headers": {"Authorization": "Bearer <token>"}
  }'

curl -X POST http://localhost:1933/api/v1/mcps \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "weather",
    "url": "https://mcp.example.com/mcp",
    "headers": {"Authorization": "Bearer <token>"},
    "target_uri": "viking://agent/tools",
    "wait": true
  }'
```

**CLI**

```bash
ov mcps validate '{"mcpServers":{"weather":{"url":"https://mcp.example.com/mcp","headers":{"Authorization":"Bearer <token>"}}}}'
ov mcps add -p viking://agent/tools --wait '{"mcpServers":{"weather":{"url":"https://mcp.example.com/mcp","headers":{"Authorization":"Bearer <token>"}}}}'
```

**Validation response**

```json
{
  "status": "ok",
  "result": {
    "name": "weather",
    "root_uri": "viking://agent/tools",
    "valid": true,
    "protocol_version": "2025-03-26",
    "server_info": {"name": "weather-server", "version": "1.0.0"},
    "tool_count": 1
  }
}
```

**Registration response**

```json
{
  "status": "ok",
  "result": {
    "name": "weather",
    "url": "https://mcp.example.com/mcp",
    "created_at": "2026-10-21T08:00:00+00:00",
    "updated_at": "2026-10-21T08:00:00+00:00",
    "header_names": ["Authorization"],
    "tool_count": 1,
    "protocol_version": "2025-03-26"
  }
}
```

With `wait=false`, the registration result may also contain the underlying catalog write's `processing` object.

### List, inspect, and check status

Without `target_uri`, `GET /api/v1/mcps` merges user and shared servers and identifies each entry with `root_uri`. `GET /api/v1/mcps/{name}` and status default to the user space; pass `target_uri=viking://agent/tools` for a same-named shared server. A connection failure still has a successful HTTP envelope, but `result.status` is `error`.

**SDK and CLI**

```python
servers = client.list_mcps()
server = client.get_mcp("weather", target_uri="viking://agent/tools")
status = client.get_mcp_status("weather", target_uri="viking://agent/tools")
```

```typescript
const servers = await client.listMCPs();
const server = await client.getMCP("weather", "viking://agent/tools");
const status = await client.getMCPStatus("weather", "viking://agent/tools");
```

```bash
ov mcps list
ov mcps show -p viking://agent/tools weather
ov mcps status -p viking://agent/tools weather
```

**HTTP API**

```bash
curl -H "X-API-Key: your-key" http://localhost:1933/api/v1/mcps
curl -H "X-API-Key: your-key" \
  "http://localhost:1933/api/v1/mcps/weather?target_uri=viking%3A%2F%2Fagent%2Ftools"
curl -H "X-API-Key: your-key" \
  "http://localhost:1933/api/v1/mcps/status?name=weather&target_uri=viking%3A%2F%2Fagent%2Ftools"
```

**List response**

```json
{
  "status": "ok",
  "result": {
    "servers": [{
      "name": "weather",
      "root_uri": "viking://agent/tools",
      "url": "https://mcp.example.com/mcp",
      "created_at": "2026-10-21T08:00:00+00:00",
      "updated_at": "2026-10-21T08:00:00+00:00",
      "header_names": ["Authorization"],
      "tool_count": 1
    }],
    "total": 1
  }
}
```

The inspect response adds `tools` to the same server object. Each item contains `uri`, `server`, `address`, `revision`, `input_revision`, and the upstream MCP `tool` definition. `revision` tracks the complete Tool definition; `input_revision` tracks the tool name and `inputSchema` for mount compatibility.

**Status response**

```json
{
  "status": "ok",
  "result": {
    "name": "weather",
    "status": "running",
    "protocol_version": "2025-03-26",
    "tool_count": 1
  }
}
```

On a connection failure, `result` is `{"name":"weather","status":"error","error":"...","tool_count":1}`. Clients should check both the HTTP envelope and this domain status.

### Synchronize the tool catalog

`POST /api/v1/mcps/{name}/sync` discovers upstream tools again, rewrites only added tools or tools whose `revision` changed, and removes tools that disappeared upstream. The `changed` and `unchanged` classifications use `input_revision`, so a description, annotations, or output-schema-only update refreshes the catalog while remaining `unchanged`. Sync does not rewrite or delete existing live context nodes. Instead, `affected_mounts` reports:

| Status | Meaning |
|--------|---------|
| `REVIEW` | The tool input revision changed and preset arguments still satisfy the new schema; review behavior |
| `INVALID` | The tool input revision changed and preset arguments no longer satisfy the new schema |
| `REMOVED` | The upstream server no longer provides the tool |

**Request body**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `wait` | boolean | No | `false` | Wait for tool catalog processing; the whole body may be omitted |
| `target_uri` | string | No | `viking://~/tools` | Server's tools root |

**SDK, HTTP, and CLI**

```python
result = client.sync_mcp("weather", target_uri="viking://agent/tools", wait=True)
```

```typescript
const result = await client.syncMCP("weather", {
  targetUri: "viking://agent/tools",
  wait: true,
});
```

```bash
curl -X POST http://localhost:1933/api/v1/mcps/weather/sync \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"target_uri":"viking://agent/tools","wait":true}'

ov mcps sync -p viking://agent/tools --wait weather
```

**Response contract**

```json
{
  "status": "ok",
  "result": {
    "name": "weather",
    "synced": true,
    "protocol_version": "2025-03-26",
    "tool_count": 1,
    "changes": {
      "added": [],
      "changed": ["current"],
      "unchanged": [],
      "removed": ["forecast"]
    },
    "affected_mounts": [{
      "node_uri": "viking://resources/weather/current.md",
      "tool_uri": "viking://agent/tools/weather/current.json",
      "tool": "current",
      "mounted_input_revision": "old-input-revision",
      "current_input_revision": "new-input-revision",
      "status": "REVIEW",
      "reason": "Tool input revision changed; mounted arguments remain valid under the current input schema"
    }]
  }
}
```

### Remove a server

`DELETE /api/v1/mcps/{name}` removes the registry entry and catalog from the selected space. It does not remove mounted live context nodes. Those nodes can no longer execute, so list and unmount them first.

```python
result = client.remove_mcp("weather", target_uri="viking://agent/tools")
```

```bash
ov mcps mounts -p viking://agent/tools --mcp weather
ov mcps remove -p viking://agent/tools weather
```

```json
{
  "status": "ok",
  "result": {"name": "weather", "removed": true}
}
```

## Tool discovery and invocation

### Search tools semantically

Without `target_uri`, `POST /api/v1/mcps/find` searches both the user and shared spaces and merges the results. Set it to restrict search to one tools root. REST and SDK responses include the complete `tool` definition, allowing callers to supply only parameters required by its `inputSchema`.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `query` | string | Yes | - | Non-empty natural-language query |
| `limit` | integer | No | `10` | Result limit, from 1 to 100 |
| `target_uri` | string | No | Both spaces | Restrict search to the user or shared tools root |

```python
result = client.find_mcp_tools("current weather for a city", limit=5)
```

```typescript
const result = await client.findMCPTools("current weather for a city", 5);
```

```bash
ov mcps find --limit 5 "current weather for a city"
```

```json
{
  "status": "ok",
  "result": {
    "tools": [{
      "uri": "viking://agent/tools/weather/current.json",
      "server": "weather",
      "name": "current",
      "description": "Get current weather",
      "revision": "sha256-revision",
      "input_revision": "sha256-input-revision",
      "score": 0.87,
      "tool": {
        "name": "current",
        "description": "Get current weather",
        "inputSchema": {
          "type": "object",
          "properties": {"city": {"type": "string"}},
          "required": ["city"]
        }
      }
    }],
    "total": 1
  }
}
```

### Invoke a tool

`POST /api/v1/mcps/invoke` invokes a tool through its registered server. OpenViking injects stored upstream headers without exposing credentials to the caller. Calls are not retried automatically. A disconnect or timeout reports that the outcome may be unknown.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `uri` | string | Yes | - | `viking://agent/tools/<server>/<tool>.json` |
| `arguments` | object | No | `{}` | Values satisfying the tool's `inputSchema` |

```python
result = client.invoke_mcp_tool(
    "viking://agent/tools/weather/current.json",
    {"city": "Beijing"},
)
```

```typescript
const result = await client.invokeMCPTool(
  "viking://agent/tools/weather/current.json",
  { city: "Beijing" },
);
```

```bash
ov mcps invoke viking://agent/tools/weather/current.json \
  --args '{"city":"Beijing"}'
```

```json
{
  "status": "ok",
  "result": {
    "content": [{"type": "text", "text": "Beijing: 18 C, clear"}],
    "isError": false
  }
}
```

`result` preserves the MCP `CallToolResult` and may contain `content`, `structuredContent`, and `isError`. An upstream `isError=true` still has a successful HTTP envelope, so callers must inspect the field.

## Live context mounts

### Create or replace mounts

`POST /api/v1/mcps/mount` creates or replaces one Markdown live context node. `POST /api/v1/mcps/batch-mount` validates the full batch before writing it. A batch cannot contain duplicate `node_uri` values.

Any tool can be mounted. Missing or empty annotations are treated as read-only for compatibility. A complete read refuses to execute tools with `annotations.readOnlyHint: false` or `destructiveHint: true`; the error returns the `tool_uri` and stored arguments needed by the integration's explicit invocation mechanism.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `tool_uri` | string | Yes | - | Registered tool URI |
| `node_uri` | string | Yes | - | Target URI ending in `.md` |
| `title` | string | Yes | - | Non-empty node title |
| `description` | string/null | No | `null` | Node description |
| `timeout_seconds` | integer | No | `60` | Per-call timeout; must be positive |
| `arguments` | object | No | `{}` | Preset arguments validated against the tool schema |
| `tags` | string[]/null | No | `null` | Retrieval tags; omission or `[]` preserves existing tags. Use `set_tags` with clear mode to remove tags |
| `wait` | boolean | No | `false` | Wait for node processing; at the outer level for a batch |

**Python SDK**

```python
result = client.mount_mcp_tool(
    "viking://agent/tools/weather/current.json",
    "viking://resources/live/weather-beijing.md",
    "Beijing live weather",
    description="Query current weather in Beijing",
    arguments={"city": "Beijing"},
    tags=["source=mcp"],
    wait=True,
)
```

**TypeScript SDK**

```typescript
const result = await client.mountMCPTool(
  "viking://agent/tools/weather/current.json",
  "viking://resources/live/weather-beijing.md",
  "Beijing live weather",
  {
    description: "Query current weather in Beijing",
    arguments: { city: "Beijing" },
    tags: ["source=mcp"],
    wait: true,
  },
);
```

**HTTP API**

```bash
curl -X POST http://localhost:1933/api/v1/mcps/mount \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{
    "tool_uri": "viking://agent/tools/weather/current.json",
    "node_uri": "viking://resources/live/weather-beijing.md",
    "title": "Beijing live weather",
    "description": "Query current weather in Beijing",
    "arguments": {"city": "Beijing"},
    "tags": ["source=mcp"],
    "wait": true
  }'
```

**CLI**

```bash
ov mcps mount \
  viking://agent/tools/weather/current.json \
  viking://resources/live/weather-beijing.md \
  --title "Beijing live weather" \
  --description "Query current weather in Beijing" \
  --args '{"city":"Beijing"}' \
  --wait
```

The current CLI `mount` command has no `--tags` option. Use an SDK or HTTP when tags are needed.

**Response contract**

```json
{
  "status": "ok",
  "result": {
    "node_uri": "viking://resources/live/weather-beijing.md",
    "mcp": "weather",
    "tool": "current",
    "tool_uri": "viking://agent/tools/weather/current.json",
    "input_revision": "sha256-input-revision",
    "title": "Beijing live weather",
    "description": "Query current weather in Beijing",
    "timeout_seconds": 60,
    "arguments": {"city": "Beijing"},
    "created_at": "2026-10-21T08:30:00+00:00",
    "mounted": true
  }
}
```

A batch body is `{"mounts":[<single-item parameters without wait>],"wait":false}` and must contain at least one item. The Python, TypeScript, and Go SDKs expose `batch_mount_mcp_tools`, `batchMountMCPTools`, and `BatchMountMCPTools`, respectively. The response is:

```json
{
  "status": "ok",
  "result": {
    "mounts": [{"node_uri": "viking://resources/live/weather-beijing.md", "mounted": true}],
    "total": 1
  }
}
```

Each `mounts` item has the full set of fields shown in the single-mount response.
Mount responses do not include `tags` because vector indexing may still be asynchronous. Use the list-mounts API to read the current vector-backed tags.

### List mounts

`GET /api/v1/mcps/mounts` requires `mcp`, accepts an optional `tool`, and uses `target_uri` to select the same-named server's space. It defaults to the user space. The server must still be registered.
Returned `tags` are read from the vector index; the mount database does not keep a separate copy.

```python
result = client.list_mcp_mounts("weather", tool="current")
```

```bash
ov mcps mounts --mcp weather --tool current
```

```json
{
  "status": "ok",
  "result": {
    "mounts": [{
      "node_uri": "viking://resources/live/weather-beijing.md",
      "mcp": "weather",
      "tool": "current",
      "tool_uri": "viking://agent/tools/weather/current.json",
      "input_revision": "sha256-input-revision",
      "arguments": {"city": "Beijing"},
      "title": "Beijing live weather",
      "description": "Query current weather in Beijing",
      "timeout_seconds": 60,
      "tags": ["source=mcp"],
      "created_at": "2026-10-21T08:30:00+00:00"
    }],
    "total": 1
  }
}
```

### Read live context

After mounting, use the regular content read API or `ov read`. OpenViking recognizes the node's `OpenViking Live Context` metadata and, for tools allowed by the read-only policy, calls the tool with its stored `tool_uri`, `arguments`, and `timeout_seconds`.

```bash
ov read viking://resources/live/weather-beijing.md
```

Live reads support MCP text results and `structuredContent`. Multiple text blocks are joined with newlines. A structured-only result becomes formatted JSON. An upstream `isError=true`, image/audio-only content, or a tool explicitly marked non-read-only or destructive causes a `PROCESSING_ERROR` on read. Direct `/invoke` calls can execute write-capable tools and still preserve native MCP content blocks.

### Remove mounts

`DELETE /api/v1/mcps/mount` removes one mount record and its Markdown node. The batch endpoint `POST /api/v1/mcps/batch-unmount` takes at least one unique `node_uris` value. The entire batch fails if any mount does not exist.

```python
client.unmount_mcp_tool(
    "viking://resources/live/weather-beijing.md",
    wait=True,
)
```

```bash
ov mcps unmount viking://resources/live/weather-beijing.md --wait
```

```bash
curl -X DELETE \
  "http://localhost:1933/api/v1/mcps/mount?node_uri=viking%3A%2F%2Fresources%2Flive%2Fweather-beijing.md&wait=true" \
  -H "X-API-Key: your-key"
```

```json
{
  "status": "ok",
  "result": {
    "node_uri": "viking://resources/live/weather-beijing.md",
    "mcp": "weather",
    "tool": "current",
    "tool_uri": "viking://agent/tools/weather/current.json",
    "unmounted": true
  }
}
```

The batch body is `{"node_uris":["viking://resources/live/weather-beijing.md"],"wait":true}`. Its response contains `unmounts` and `total`; each `unmounts` item has the same fields as the single-item response.

## Common errors

| Error code | Common causes |
|------------|---------------|
| `INVALID_ARGUMENT` | Invalid name or URL, empty search, malformed URI, non-`.md` node, schema-invalid arguments, duplicate batch URI |
| `ALREADY_EXISTS` | A server with the same name is already registered |
| `NOT_FOUND` | Server, tool, or mount does not exist |
| `UNAVAILABLE` | MCP connection, initialization, or tool call failed or timed out |

Tool calls are not retried automatically. After `UNAVAILABLE` or a timeout, do not assume that an upstream operation with side effects did not execute.

## Related documentation

- [Live context integration and arrangement](../guides/24-live-context-arrangement.md)
- [MCP integration](../guides/06-mcp-integration.md)
- [Content API](12-content.md)
- [SDK, HTTP and CLI conventions](01-overview.md)
