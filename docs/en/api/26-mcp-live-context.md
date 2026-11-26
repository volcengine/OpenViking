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

- MCP servers must use Streamable HTTP. Endpoint URLs support both HTTP and HTTPS.
- Server names may contain only letters, numbers, `_`, and `-`, up to 64 characters. Tool names must be valid OpenViking filenames.
- Secret HTTP header and URL query parameter values submitted at registration are stored in the caller's Privacy. The registry and API contain ordinary values and relative Privacy references, never plaintext secrets.
- Tool definitions can live in the user space at `viking://~/tools/<server>/<tool>.json` or in the account-shared space at `viking://agent/tools/<server>/<tool>.json`. Invocation and mount arguments are validated against the tool's `inputSchema`.
- `target_uri` accepts only `viking://~/tools` or `viking://agent/tools`. Registration defaults to the user space. When omitted, `show`, `status`, `sync`, `remove`, and `mounts` with an `mcp` filter look in the private space first and fall back to the shared space only when the name is absent. Private servers take precedence when both spaces contain the name. An explicit target restricts the operation to that space without fallback. List and search merge both spaces and retain same-named servers.
- Name-based responses identify the selected space through `root_uri`. Once a private server is found, permission errors, missing credentials, or connection failures do not cause fallback to a shared server.
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

`POST /api/v1/mcps/validate` accepts only native connection information: `name`, `url`, and `headers`. It connects to the server, initializes MCP, and discovers tools without reading or writing Privacy, the registry, or the tool catalog. The table below describes registration parameters for `POST /api/v1/mcps`, which resolves or extracts Privacy credentials, checks connectivity, and publishes definitions. It defaults to `viking://~/tools/<name>/`; set `target_uri` to publish under `viking://agent/tools/<name>/`. Registration fails when the name already exists in the selected space.

**Request body**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `name` | string | Yes | - | Server name |
| `url` | string | Yes | - | Streamable HTTP endpoint; may include query parameters whose credentials are extracted during registration |
| `headers` | object | No | `{}` | Native headers; values containing secrets are automatically extracted to the caller's Privacy |
| `target_uri` | string | No | `viking://~/tools` | User or shared tools root |
| `wait` | boolean | No | `false` | Registration only; wait for tool catalog processing |

Unknown fields return `INVALID_ARGUMENT`. Inject credentials from environment variables or a secret manager; do not put them in resource files or commit them to source control.

### Automatic credential extraction and public MCPs

`ov mcps add` keeps its native `mcpServers` configuration with no additional credential options. Authentication headers such as `Authorization`, `X-API-Key`, and `Cookie`, as well as headers containing secrets detected by the same rules used for Skills, have their complete values saved in the current user's Privacy. Ordinary headers such as `Accept` and region settings remain in the registry.

Personal MCPs default to `viking://~/tools` and use `credential/mcp.user.<name>`. Public MCPs are published to `viking://agent/tools` and use each caller's own `credential/mcp.agent.<name>`. Privacy field names match header names. The registry stores relative references for secret headers, resolved against the caller's active Privacy version on each connection.

For example, Alice registers a public MCP using her own token:

```bash
ov mcps add -p viking://agent/tools --wait "{
  \"mcpServers\": {
    \"weather\": {
      \"url\": \"https://mcp.example.com/mcp\",
      \"headers\": {
        \"Authorization\": \"Bearer $WEATHER_MCP_TOKEN\",
        \"Accept\": \"application/json\"
      }
    }
  }
}"
```

Before using this public MCP, Bob configures the matching credential under his own identity, without registering the MCP again:

```bash
ov privacy upsert credential mcp.agent.weather \
  --field "Authorization=Bearer $WEATHER_MCP_TOKEN"
```

Missing targets or fields return `NOT_FOUND`, with no fallback to Alice's credentials. This version does not support sharing user credentials. Public tool invocations still enforce tool ACLs, and unauthenticated MCPs need no Privacy configuration. `sync` and `remove` check write access according to directory ACLs.

Automatic extraction follows the Skills Privacy write flow: unrelated fields are retained, equal values do not create a new version, and historical values cannot overwrite rotated credentials. Headers may also use the same `{{ov_privacy:<current-user>:viking://~/privacy/<category>/<target>#/values/<field>}}` references as Skills, optionally preceded by `Bearer `. Registration checks reference ownership, then stores a relative reference. Other callers of a public MCP must still configure the matching target and field in their own Privacy.

All headers are stored together in `headers`: secret values become references such as `{{ov_privacy:viking://~/privacy/credential/mcp.agent.weather#/values/Authorization}}`, while ordinary values remain unchanged. `mcps show` returns these safe values and references. Connections resolve references for the caller and preserve the surrounding text.

URL query parameters also support automatic credential extraction. Nonempty `key` parameters, authentication names containing `api_key`, `token`, `secret`, or `password`, and values detected as secrets are saved under `credential/mcp.user.<name>.query` for personal MCPs or `credential/mcp.agent.<name>.query` for public MCPs, using the parameter name as the field. The stored `url` has no query string; `query_params` contains ordinary values and relative Privacy references. Connections resolve these values and rebuild the URL. `query_params` is a response field, not a registration parameter; submit query parameters through `url`.

For example, after registering a public MCP at `https://mcp.example.com/mcp?key=<token>&region=cn`, other users configure their own credential with `ov privacy upsert credential mcp.agent.weather.query --field "key=$WEATHER_MCP_TOKEN"`. Explicit Privacy references in query parameters follow the same caller ownership checks, resolution, rotation, and revocation rules as headers.

Python, TypeScript, and Go SDK registration methods retain their existing arguments and accept native `headers`. CLI `add` supports `-p` and a per-server `target_uri`. `validate` continues testing native URLs and headers directly without involving Privacy.

**Rotation and revocation**

Updating or activating your own Privacy version affects the next connection without `mcps sync`. Deleting your credential target or field makes subsequent calls fail; requests already sent are not canceled.

Removing an MCP retains Privacy and separate live context nodes; use `ov privacy delete` to explicitly delete credentials.

Calls validate arguments against the registered tool definition; live reads also check its read-only annotations. After resolving the caller's credentials, invocation calls the upstream tool directly without an extra `list_tools` precheck. The upstream MCP server enforces final authorization. Update changed tool definitions with `mcps sync`.

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

Without `target_uri`, `GET /api/v1/mcps` merges user and shared servers and identifies each entry with `root_uri`. `GET /api/v1/mcps/{name}` and `GET /api/v1/mcps/status?name=...` look in the user space first and fall back to the shared space when absent, so `ov mcps show weather` needs no extra parameter for a shared-only server. Private servers take precedence for duplicate names; pass `target_uri=viking://agent/tools` to select the shared server explicitly. A connection failure still has a successful HTTP envelope, but `result.status` is `error`.

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
| `target_uri` | string | No | Private first, then shared | Restrict the operation to this tools root when specified |

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

`DELETE /api/v1/mcps/{name}` removes the registry entry and catalog from the selected space. Without `target_uri`, it selects the private server first and removes the shared server only when the private name is absent. An explicit target restricts deletion to that space. It does not remove mounted live context nodes. Those nodes can no longer execute, so list and unmount them first.

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
| `timeout_seconds` | number | No | `60` | Timeout per tool call; must be finite and positive, and is independent of the client's HTTP request timeout |

```python
result = client.invoke_mcp_tool(
    "viking://agent/tools/weather/current.json",
    {"city": "Beijing"},
    timeout_seconds=120,
)
```

```typescript
const result = await client.invokeMCPTool(
  "viking://agent/tools/weather/current.json",
  { city: "Beijing" },
  120,
);
```

```bash
ov mcps invoke viking://agent/tools/weather/current.json \
  --args '{"city":"Beijing"}' --timeout-seconds 120
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

`POST /api/v1/mcps/mount` creates or replaces one Markdown live context node. `POST /api/v1/mcps/batch-mount` validates each item's arguments and target write permissions, skips invalid items, then writes valid nodes in request order and immediately persists each mount record. Empty batches and duplicate `node_uri` values reject the entire request before any mutation.

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
    "total": 1,
    "succeeded": ["viking://resources/live/weather-beijing.md"],
    "failed": [],
    "not_attempted": []
  }
}
```

Each `mounts` item has the full set of fields shown in the single-mount response.
Mount responses do not include `tags` because vector indexing may still be asynchronous. Use the list-mounts API to read the current vector-backed tags.

### List mounts

`GET /api/v1/mcps/mounts` accepts optional `mcp` and `tool` filters. Without `mcp`, it lists all readable mounts from the current user's private space and the shared space, including nodes retained after server removal. Set `target_uri` to restrict the query to one space. The `tool` filter can be used independently.
When `mcp` is provided without a target, lookup selects the private server first and falls back to the shared server only when absent; duplicate names return only the private server's mounts. An explicit target restricts lookup to that space, and the selected server must still be registered. Responses use `root_uris` for both spaces and `root_uri` for a single space.
Returned `tags` are read from the vector index; the mount database does not keep a separate copy.

Mount status is computed from local tool definitions without contacting the upstream MCP or persisting a status file. Each tool definition is read once per request. Preset arguments are validated only when the input revision changes, using the same classification as `sync`. Status reflects the most recently synchronized local definitions.

| Status | Meaning |
|--------|---------|
| `VALID` | The local tool input revision matches the mounted revision |
| `REVIEW` | The input revision changed and preset arguments still match the new schema |
| `INVALID` | The input revision changed and preset arguments no longer match the new schema |
| `REMOVED` | The local tool definition no longer exists |
| `UNKNOWN` | The local definition cannot be read or is corrupt, so its status cannot be determined |

Set `status=INVALID` (CLI: `--status INVALID`) to filter by status. Multiple values are comma-separated, for example `status=INVALID,REMOVED,UNKNOWN`; omitting the parameter returns all statuses. Supported values are `VALID`, `REVIEW`, `INVALID`, `REMOVED`, and `UNKNOWN`, case-insensitively. Each item includes `status` and `reason`. `total` counts returned nodes, `matched_total` counts readable nodes matching the MCP, tool, and space filters, and `counts` summarizes their statuses. Status filtering does not change `matched_total` or `counts`.

The CLI shows the total, counts by status, and displayed count. It omits `status` and `reason` for `VALID` nodes, while retaining reasons for other statuses. JSON responses retain all status fields.

```python
result = client.list_mcp_mounts()
result = client.list_mcp_mounts("weather", tool="current")
```

```bash
ov mcps mounts
ov mcps mounts -p viking://agent/tools
ov mcps mounts --mcp weather --tool current
ov mcps mounts --status INVALID
ov mcps mounts --status INVALID,REMOVED,UNKNOWN
```

TypeScript callers can use `client.listMCPMounts()` to list all mounts. Go callers can use `client.ListMCPMounts(ctx, "", "")` to omit the MCP and tool filters.

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
      "status": "VALID",
      "reason": "Tool input revision matches the mounted definition",
      "tags": ["source=mcp"],
      "created_at": "2026-10-21T08:30:00+00:00"
    }],
    "total": 1,
    "matched_total": 1,
    "counts": {"VALID": 1, "REVIEW": 0, "INVALID": 0, "REMOVED": 0, "UNKNOWN": 0}
  }
}
```

### Read live context

After mounting, use the regular content read API or `ov read`. OpenViking recognizes the node's `OpenViking Live Context` metadata and, for tools allowed by the read-only policy, calls the tool with its stored `tool_uri`, `arguments`, and `timeout_seconds`.

```bash
ov read viking://resources/live/weather-beijing.md
```

Live reads preserve the node definition (YAML frontmatter, preset arguments, and any body guidance), then append the evaluated result under `## Live Result`. They support MCP text results and `structuredContent`. Multiple text blocks are joined with newlines. A structured-only result becomes formatted JSON. REST `read?raw=true` and MCP `read(raw=true)` return only the stored definition without calling the tool; paginated reads also skip execution. An upstream `isError=true`, image/audio-only content, or a tool explicitly marked non-read-only or destructive causes a `PROCESSING_ERROR` on read. Direct `/invoke` calls can execute write-capable tools and still preserve native MCP content blocks.

### Remove mounts

`DELETE /api/v1/mcps/mount` removes one mount record and its Markdown node. The batch endpoint `POST /api/v1/mcps/batch-unmount` takes at least one unique `node_uris` value. Missing mounts or denied deletion permissions are reported per item; other nodes continue. Each completed deletion immediately persists its record removal.

Both batch endpoints skip failed items and continue processing independent nodes, retaining completed changes. Results use HTTP 200: callers must inspect `failed`, including item validation and permission errors. `succeeded` lists successful node URIs and `failed` contains `node_uri`, `code`, `message`, and `applied`; both lists follow request order. `not_attempted` is empty after normal completion. Empty batches and duplicate URIs return `INVALID_ARGUMENT` without modifying files.

`failed[].applied=true` means the file mutation and corresponding record update completed but downstream processing failed. These items also appear in `mounts` or `unmounts`; `total` counts applied mutations. Later nodes still execute. Batch unmount never recreates deleted content, preserving the access boundaries of remaining nodes. A failed record save retries only the current database snapshot once, without repeating file mutations. A second failure stops the batch and returns `PROCESSING_ERROR` with `node_uri`, `reconciliation_required=true`, and `succeeded`, `failed`, and `not_attempted` in the error details. That node and its record require reconciliation; `not_attempted` lists remaining valid nodes whose file operations never started.

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
| `CONFLICT` | Re-registration supplied a historical value of a rotated credential field |
| `NOT_FOUND` | Server, tool, or mount does not exist |
| `PERMISSION_DENIED` | Directory or tool ACL denies access, a Privacy reference has another owner, or the current credential cannot use the tool |
| `UNAVAILABLE` | MCP connection, initialization, or tool call failed or timed out |

Tool calls are not retried automatically. After `UNAVAILABLE` or a timeout, do not assume that an upstream operation with side effects did not execute.

## Related documentation

- [Live context integration and arrangement](../guides/24-live-context-arrangement.md)
- [MCP integration](../guides/06-mcp-integration.md)
- [Privacy API](10-privacy.md)
- [Content API](12-content.md)
- [SDK, HTTP and CLI conventions](01-overview.md)
