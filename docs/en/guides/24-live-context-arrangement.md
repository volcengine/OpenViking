---
description: Connect remote MCP servers, mount tools as live context, and arrange nodes with the CLI, SDK, Studio, and Context as Code.
---

# Live context integration and arrangement

Live Context mounts a remote MCP tool as a Markdown node under `viking://resources/...`. An agent does not need to know the upstream address, credentials, or preset arguments. It reads the node like ordinary context; OpenViking calls the bound tool and transparently returns the upstream result.

This page is for developers who implement and maintain context arrangements. It explains how MCP registration, the Tool Catalog, live nodes, the Agent Gateway, Studio, and Context as Code (CaC) fit together. For MCP client connection settings and the complete built-in tool list, see [MCP tools and protocol](06-mcp-integration.md).

## Core model

The feature separates control-plane operations from data-plane execution:

| Layer | Responsibility | Main entry points |
| --- | --- | --- |
| MCP registration and sync | Store MCP server connection details and synchronize tool definitions | `ov mcps add/validate/sync/status`, REST, SDK |
| Tool Catalog | Make definitions searchable and readable; retain definition and input revisions | `viking://~/tools/<server>/<tool>.json` or `viking://agent/tools/<server>/<tool>.json` |
| Live Context | Bind one tool, preset arguments, and a destination URI | `ov mcps mount/unmount/mounts`, REST, SDK |
| Agent Gateway | Discover infrequent tools by intent and proxy their execution | MCP `find_mcp_tools`, `invoke_mcp_tool` |
| Context arrangement | Declare desired nodes, calculate a diff, and converge state | Studio Arranger, Python `ContextArranger` |

The normal flow is:

```text
Remote MCP Server
        |
        | validate / add / sync
        v
viking://~/tools/<server>/<tool>.json
or viking://agent/tools/<server>/<tool>.json
        |
        | mount + preset arguments
        v
viking://resources/<project>/<node>.md
        |
        | complete read
        v
OpenViking resolves credentials -> calls upstream Tool -> returns native Tool result
```

Pre-mount frequent scenarios whose parameters are stable. For infrequent requests whose parameters depend on the current question, let the agent discover and call a tool with `find_mcp_tools` and `invoke_mcp_tool`.

## Requirements and security boundaries

- Only Streamable HTTP MCP servers are supported.
- URLs may use HTTP or HTTPS. HTTP does not encrypt transport; users are responsible for assessing exposure risks for credentials and tool data.
- An MCP server name may contain only letters, numbers, `_`, and `-`, up to 64 characters.
- Tool names must be valid OpenViking filenames.
- Registration connects to the upstream and performs tool discovery. A connection failure or invalid definition does not write registry state.
- OpenViking stores headers on the server and uses them for upstream calls. Build CLI configuration from environment variables; never commit real credentials.
- `invoke` and a live-node read may have external side effects. OpenViking does not automatically retry a tool call whose outcome is unknown.
- Mount arguments are validated against the tool's `inputSchema`.
- The user `viking://~/tools` and account-shared `viking://agent/tools` roots are managed directories. Do not mutate them with ordinary `mkdir`, `rm`, `cp`, or `mv`.

## Use `ov mcps`

### Validate and register a server

Validate connectivity and tool discovery without persisting configuration:

```bash
MCP_TOKEN='replace-me'
ov mcps validate "$(jq -cn \
  --arg token "$MCP_TOKEN" \
  '{mcpServers:{weather:{url:"https://mcp.example.com/mcp",headers:{Authorization:("Bearer " + $token)}}}}')"
```

Register it after validation. `add` defaults to the current user's `viking://~/tools`, writes the discovered Tool Catalog immediately, and lets summaries and indexing continue in the background:

```bash
ov mcps add "$(jq -cn \
  --arg token "$MCP_TOKEN" \
  '{mcpServers:{weather:{url:"https://mcp.example.com/mcp",headers:{Authorization:("Bearer " + $token)}}}}')"
```

Select the account-shared space explicitly when every account user should see it:

```bash
ov mcps add -p viking://agent/tools "$(jq -cn \
  --arg token "$MCP_TOKEN" \
  '{mcpServers:{weather:{url:"https://mcp.example.com/mcp",headers:{Authorization:("Bearer " + $token)}}}}')"
```

Add `-w` or `--wait` when the command must return only after tool summaries and indexing finish:

```bash
ov mcps add -p viking://agent/tools --wait "$(jq -cn \
  --arg token "$MCP_TOKEN" \
  '{mcpServers:{weather:{url:"https://mcp.example.com/mcp",headers:{Authorization:("Bearer " + $token)}}}}')"
```

Inspect the registry and definitions:

```bash
ov mcps list
ov mcps show -p viking://agent/tools weather
ov mcps status -p viking://agent/tools weather
```

The management commands are:

| Command | Purpose |
| --- | --- |
| `ov mcps validate <json>` | Check connectivity and discovery without persisting |
| `ov mcps add <json> [-p <tools-root>] [-w\|--wait]` | Register servers; `-p` defaults to `viking://~/tools` |
| `ov mcps list [-p <tools-root>]` | Merge user and shared servers by default; `-p` restricts the space |
| `ov mcps show <name> [-p <tools-root>]` | Show a server; name-based operations default to the user space |
| `ov mcps status <name> [-p <tools-root>]` | Check upstream status; name-based operations default to the user space |
| `ov mcps remove <name> [-p <tools-root>]` | Remove the server from the selected space |

### Find and invoke a tool

The Tool Catalog supports semantic discovery:

```bash
ov mcps find "weather in Beijing" --limit 5
```

Without `-p`, `list` and `find` merge the user and shared spaces; other name-based management commands use the user space. The same server name may exist in both spaces. Use the returned `root_uri` and `-p` to disambiguate.

Use the returned URI and schema to invoke it:

```bash
ov mcps invoke \
  viking://agent/tools/weather/current.json \
  --args '{"city":"Beijing"}'
```

The server resolves the MCP address and headers. It preserves the upstream tool's native `content`, `structuredContent`, and `isError`.

### Mount live context

Bind preset arguments to a Markdown URI:

```bash
ov mcps mount \
  viking://agent/tools/weather/current.json \
  viking://resources/arrange/beijing-weather.md \
  --title "Beijing Weather" \
  --description "Current weather for the Beijing region." \
  --args '{"city":"Beijing"}' \
  --timeout-seconds 60 \
  --wait
```

Reading the node executes the upstream tool:

```bash
ov read viking://resources/arrange/beijing-weather.md
```

List and remove mounts:

```bash
ov mcps mounts -p viking://agent/tools --mcp weather
ov mcps mounts -p viking://agent/tools --mcp weather --tool current
ov mcps unmount viking://resources/arrange/beijing-weather.md --wait
```

`mount` converges on `node_uri`: repeating it updates the tool, arguments, title, and description while retaining the initial `created_at`. Remove a node with `ov mcps unmount` so the descriptor and mount record are deleted together.

### Synchronize the Tool Catalog

After an upstream definition changes, run:

```bash
ov mcps sync -p viking://agent/tools weather
```

Summaries and indexing continue in the background by default. Wait for all Tool Catalog creates, updates, removals, and index refreshes with:

```bash
ov mcps sync -p viking://agent/tools weather --wait
```

The result classifies tools as `added`, `changed`, `unchanged`, or `removed`, and reports affected mounts:

| Mount status | Meaning | Recommended action |
| --- | --- | --- |
| `REVIEW` | The tool input revision changed, but the existing arguments still satisfy the new schema | Review semantic changes, then remount to confirm |
| `INVALID` | The input revision changed and the existing arguments no longer satisfy the schema | Fix arguments and remount |
| `REMOVED` | The upstream no longer provides the tool | Bind a replacement or explicitly unmount |

`sync` does not automatically delete affected live nodes. This prevents a temporary upstream change from damaging an arranged resource tree. Operators or CaC `apply --prune` remain responsible for cleanup.

The current tool `revision` covers the complete upstream Tool definition. `input_revision` is calculated from the tool name and `inputSchema` to detect argument-contract changes. Changes only to descriptions, annotations, or the output schema refresh the Tool Catalog through `revision` without changing `input_revision` or producing a mount review.

## Live-node data model

A mount creates a Markdown descriptor with YAML frontmatter. This example shows the equivalent structure:

```markdown
---
type: OpenViking Live Context
title: Beijing Weather
description: Current weather for the Beijing region.
x-openviking:
  schema_version: v1
  type: mcp
  definition: viking://agent/tools/weather/current.json
  input_revision: <tool-input-revision>
  args:
    city: Beijing
  timeout_seconds: 60
  retry:
    max_attempts: 3
    backoff_seconds: 1
    backoff_multiplier: 2
    max_backoff_seconds: 5
---
```

Do not maintain this as ordinary Markdown. The account-level `live_context_nodes.dat` database also records the mount for listing, input revision tracking, and safe unmounting. Editing only the file makes the descriptor and mount record inconsistent.

`retry` is reserved policy metadata in the node format. The current execution path does not automatically retry tool calls from this block, avoiding duplicate external side effects when an outcome is unknown.

The catalogs, their space-specific registries, and the account mount database are managed state:

```text
viking://~/tools/<server>/<tool>.json
viking://agent/tools/<server>/<tool>.json
<account>/_system/mcps/<user-id>.json
<account>/_system/mcps.json
<account>/_system/live_context_nodes.dat
```

The latter two are internal storage paths, not user-facing Viking URIs.

## Read semantics

Live execution occurs only on a complete read:

- REST/CLI: `offset=0` and `limit=-1`.
- MCP `read`: the default complete read.
- The response is the upstream MCP tool result, without descriptor frontmatter or an extra label.
- Tools without annotations are treated as read-only. A tool with `readOnlyHint: false` or `destructiveHint: true` is not executed by `read`; the error returns the mounted URI and arguments for explicit invocation through the current integration.
- A paginated read returns the stored descriptor and does not call the upstream.
- `find`, `search`, `list`, and `tree` never execute a live node implicitly.
- REST `read?raw=true` returns the raw descriptor without executing the tool.

These rules keep retrieval and browsing free of side effects while still allowing write-capable tools to be mounted as agent guidance. An agent should retrieve a node first and call `read` for a live value; write-capable tools require the separately permissioned `invoke_mcp_tool`.

## Dynamic agent discovery

The OpenViking MCP endpoint exposes two additional data-plane tools:

1. `find_mcp_tools(query, limit=10)` semantically retrieves tools and returns `uri`, `score`, and the complete `tool` definition, including `inputSchema` and annotations.
2. `invoke_mcp_tool(tool_uri, arguments={})` proxies execution while the server resolves connection details and credentials.

The expected agent flow is:

```text
User intent
  -> find_mcp_tools
  -> fill arguments from tool.inputSchema
  -> invoke_mcp_tool
  -> consume the native MCP Tool result
```

These tools do not expose control-plane registration, synchronization, or mounting. Dynamic discovery is appropriate for low-frequency work. Mount frequent scenarios as semantically stable nodes with preset arguments to reduce agent decisions and trial calls.

## SDK arrangement

The Python, Go, and TypeScript SDKs expose MCP registration, discovery, invocation, mounting, and synchronization. Python also provides `ContextArranger` to declare desired state and converge live nodes in batches.

### Direct SDK use

```python
import os

from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(
    url=os.environ.get("OPENVIKING_URL", "http://127.0.0.1:1933"),
    api_key=os.environ.get("OPENVIKING_API_KEY"),
)
client.initialize()

client.mount_mcp_tool(
    tool_uri="viking://agent/tools/weather/current.json",
    node_uri="viking://resources/arrange/beijing-weather.md",
    title="Beijing Weather",
    arguments={"city": "Beijing"},
    tags=["project=weather"],
    wait=True,
)
```

Use `batch_mount_mcp_tools` and `batch_unmount_mcp_tools` for batches. Batch mounting validates the complete declaration first, then groups writes by resource root to reduce round trips. A batch cannot declare the same `node_uri` twice.

### Context as Code

```python
import os

from openviking_sdk import ContextArranger, SyncHTTPClient

client = SyncHTTPClient(
    url=os.environ.get("OPENVIKING_URL", "http://127.0.0.1:1933"),
    api_key=os.environ.get("OPENVIKING_API_KEY"),
)
client.initialize()

arranger = ContextArranger(
    client,
    name="weather-live-context",
    root_uri="viking://resources/arrange",
)

arranger.live_context(
    "beijing-weather.md",
    tool_uri="viking://agent/tools/weather/current.json",
    title="Beijing Weather",
    arguments={"city": "Beijing"},
    tags=["domain=weather"],
)

raise SystemExit(arranger.main())
```

The script includes its CLI:

```bash
python arrange.py plan
python arrange.py plan --output json
python arrange.py apply --dry-run
python arrange.py apply --wait
python arrange.py apply --prune --wait
```

`ContextArranger` behavior:

- `plan` always reports `CREATE`, `UPDATE`, `DELETE`, `UNCHANGED`, and `CONFLICT`.
- `apply` creates and updates by default; deletions appear as `PENDING DELETE`.
- Only `apply --prune` deletes nodes owned by this arrangement but absent from the declaration.
- `--dry-run` does not mutate remote state; `--wait` waits for processing.
- Output defaults to a human-readable table; `--output json` is for automation.
- Ownership and drift use `ov-cac-owner`, `ov-cac-kind`, and specification fingerprint tags.
- An existing target not managed by this arrangement is a conflict and is not overwritten.
- `root_uri` may be below the account-shared `viking://resources` root or a canonical
  user-private `viking://user/<user_id>/resources` root. The authenticated user must have
  access to the selected root.

One `ContextArranger` manages only its owner-tagged live nodes below `root_uri`. Do not use the same arrangement name with overlapping roots and different declarations.

## Studio Arranger

The **Arranger** page in OpenViking Studio provides a visual interface to the same model:

- Browse and semantically discover synchronized MCP tools.
- Configure preset arguments from `inputSchema`.
- Create, update, and remove live context nodes.
- Inspect tool-to-node relationships in a context graph.
- Preflight target paths and existing live nodes before mutation.

Studio, CLI, REST, and SDK share the same service APIs and mount database, so they can be combined. For a long-lived node set, choose one declarative source as its source of truth.

## REST API

The SDK and Studio use these endpoints:

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/mcps` | Register an MCP server |
| `DELETE` | `/api/v1/mcps/{name}` | Remove an MCP server |
| `GET` | `/api/v1/mcps` | List servers |
| `GET` | `/api/v1/mcps/{name}` | Show a server and its tools |
| `POST` | `/api/v1/mcps/validate` | Validate connectivity |
| `POST` | `/api/v1/mcps/{name}/sync` | Synchronize the Tool Catalog |
| `GET` | `/api/v1/mcps/status?name=...` | Check upstream status |
| `POST` | `/api/v1/mcps/find` | Semantically find tools |
| `POST` | `/api/v1/mcps/invoke` | Invoke a tool |
| `POST` | `/api/v1/mcps/mount` | Mount one live node |
| `POST` | `/api/v1/mcps/batch-mount` | Mount a batch |
| `DELETE` | `/api/v1/mcps/mount?node_uri=...` | Unmount one node |
| `POST` | `/api/v1/mcps/batch-unmount` | Unmount a batch |
| `GET` | `/api/v1/mcps/mounts?mcp=...&tool=...` | List mounts |

Requests use the current OpenViking identity. User-space server registrations and catalogs are user-scoped; the shared space and mount database are account-scoped. Destination resource URIs still follow normal identity and ACL checks. Registration, removal, inspect, sync, status, and mount-list operations accept `target_uri`; list and search merge both spaces when it is omitted.

## Operational guidance

- Run `validate` before registration. After registration, run `status` and one side-effect-free tool call.
- Run `sync` after upstream upgrades and prioritize `INVALID` and `REMOVED` mounts.
- Use JSON output in automation and retain `plan` as a change audit record.
- In production CaC, run `plan` or `apply --dry-run` before `apply --prune`.
- Use `--wait` when the node must be searchable immediately. Allow asynchronous processing when publishing batches that do not require immediate indexing.
- List and resolve mounts before removing a server. Removing the server deletes its Tool Catalog but does not replace application-level arrangement decisions.
- Back up OpenViking account data regularly. MCP registry and mount records persist with other account data.

## Troubleshooting

| Symptom | Common cause | Action |
| --- | --- | --- |
| Registration or validation fails | Invalid URL, network failure, wrong headers, or a non-Streamable HTTP server | Verify the URL and headers, then retry `ov mcps validate` |
| `find` returns no result | Catalog not published, indexing incomplete, or query mismatch | Confirm with `ov mcps show`, run `sync`, and wait for indexing if needed |
| Mount rejects arguments | Arguments do not satisfy the current `inputSchema` | Rebuild them from `show` or `find` output |
| `read` returns the descriptor | The read is paginated or uses `raw=true` | Use the default complete read |
| `read` fails and outcome is unknown | The upstream timed out or disconnected during execution | Verify the upstream outcome first; OpenViking does not retry a potentially side-effecting call |
| `sync` reports `REVIEW` | Tool input revision changed but arguments remain valid | Review Tool semantics and remount |
| `sync` reports `INVALID` | The new schema rejects preset arguments | Fix arguments and remount |
| `sync` reports `REMOVED` | The tool disappeared upstream | Bind a replacement or explicitly unmount |
| CaC reports `CONFLICT` | The target exists but is not owned by this arrangement | Choose another URI or deliberately migrate the existing node |
| Normal `apply` does not delete | Deletion is protected by default | Review `PENDING DELETE`, then run `apply --prune` |

## Design trade-offs

| Design | Reason | Cost |
| --- | --- | --- |
| Publish tool definitions in VikingFS | Tools become searchable, readable, and versioned with the context system | Upstream changes require explicit `sync` |
| Bind arguments at mount time | Frequent calls are stable and agents do not repeatedly infer fixed values | Argument changes require remounting |
| Execute only on a complete `read` | Retrieval, listing, and paginated browsing remain side-effect free | Inspecting descriptors requires a paginated or raw read |
| Do not delete nodes during sync | Upstream changes cannot immediately damage application resource trees | Operators must resolve `REVIEW/INVALID/REMOVED` |
| Do not prune by default in CaC | An omitted declaration cannot cause accidental deletion | Cleanup requires explicit `--prune` |
| Do not retry tool execution automatically | Avoid duplicate side effects when the outcome is unknown | Callers must decide whether to retry transient failures |
