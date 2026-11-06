# One global MCP connection, two project scopes

Use one OpenViking MCP registration for explicit knowledge retrieval in two local
workspaces. Each request selects a resource directory; no shared process variable
stores a “current project.” This example adds documentation and synthetic fixtures,
not a project registry, proxy, or new server API.

## Prerequisites

- Source baseline: OpenViking `main` at
  `5caada112a60783d4ec21234be90e95f0261fbd5` (checked 2026-11-06).
  Check the connected server's `tools/list` before using another version.
- A **disposable test service/account** with working embedding and semantic models,
  plus an `ov` CLI from the same source baseline. Follow the
  [quickstart](https://github.com/volcengine/OpenViking/blob/5caada112a60783d4ec21234be90e95f0261fbd5/docs/en/getting-started/02-quickstart.md).
  Do not point the setup commands at existing project data.
- A normal User key that can write the two test directories during setup and read
  them afterward. Use a private CLI connection file and the same identity in MCP;
  ROOT keys are for administration, not tenant data access in API-key mode.
- An MCP client supporting Streamable HTTP and a user/global server registration.
  Configuration names vary by client; see
  [MCP clients](https://github.com/volcengine/OpenViking/blob/5caada112a60783d4ec21234be90e95f0261fbd5/docs/en/agent-integrations/06-mcp-clients.md).

All documents here are invented. Setup writes two Markdown files and can invoke
configured models, incurring provider charges. The retrieval walkthrough uses
only `list`, `find`, and `read` on those stored documents. It installs no hooks and
does not call `remember`, capture messages, or commit sessions.

## 1. Register once

Merge the single `openviking` entry from [config/mcp.json](config/mcp.json) into
your client's **user/global** MCP configuration. Replace `<TEST_USER_API_KEY>`
in that private configuration using the client's secret facility where available.
The loopback URL is a test endpoint placeholder; replace it with your test service
URL (HTTPS for remote access). Never commit the filled configuration.

Clients supporting this `mcpServers` HTTP format can use the template directly.
Other clients must translate its transport, URL, and auth header fields into their
own format. A global registration is a client setting, not a server tenant.
Do not add a second registration in either project. Reconnect and confirm that
`tools/list` advertises `find(target_uri=...)`, `list(uri=...)`, and `read(uri=...)`.

## 2. Prepare the synthetic sources

Run from this example's directory. Replace the absolute connection-file placeholder
with a private file for the disposable test identity, as described in
[client configuration](https://github.com/volcengine/OpenViking/blob/5caada112a60783d4ec21234be90e95f0261fbd5/docs/en/configuration/02-client.md).
These are **operator setup writes**, separate from retrieval:

```bash
export OPENVIKING_CLI_CONFIG_FILE="/absolute/path/to/private-test-ovcli.conf"
ov write viking://resources/project-a/brief.md --from-file project-a/brief.md --wait
ov write viking://resources/project-b/brief.md --from-file project-b/brief.md --wait
ov read viking://resources/project-a/brief.md
ov read viking://resources/project-b/brief.md
```

`write` creates missing parent directories and replaces an existing file at the
same URI. Confirm both source bodies and successful indexing before testing
`find`; a completed file write alone does not prove search readiness. If a request
fails or times out, inspect its returned task/status and read back the URI before
retrying. Keep the CLI and MCP identity on this test account throughout.

## 3. Select project A, then project B

The two workspace folders contain short `AGENTS.md` instructions and existing
`.openviking/config.json` behavior settings:

| Workspace | Explicit resource scope | Optional memory peer |
| --- | --- | --- |
| `project-a/` | `viking://resources/project-a` | `project-a` |
| `project-b/` | `viking://resources/project-b` | `project-b` |

Open `project-a/` in a client that loads `AGENTS.md` (or supply its instructions
explicitly), and ask: **“What is this project's retry limit? Cite the source.”**
Use these MCP `tools/call` parameters through the same `openviking` registration:

```json
{"name":"list","arguments":{"uri":"viking://resources/project-a","limit":20}}
{"name":"find","arguments":{"query":"What is this project's retry limit?","target_uri":"viking://resources/project-a","context_type":"resource","limit":3}}
{"name":"read","arguments":{"uri":"viking://resources/project-a/brief.md"}}
```

Expect **3 attempts**, citing `viking://resources/project-a/brief.md`. Then open
`project-b/`, load its instructions, and ask the same question:

```json
{"name":"list","arguments":{"uri":"viking://resources/project-b","limit":20}}
{"name":"find","arguments":{"query":"What is this project's retry limit?","target_uri":"viking://resources/project-b","context_type":"resource","limit":3}}
{"name":"read","arguments":{"uri":"viking://resources/project-b/brief.md"}}
```

Expect **7 attempts**, citing `viking://resources/project-b/brief.md`. Switching
workspaces does not mutate the MCP connection. In a conversation spanning both
projects, state the project explicitly for every question; if ambiguous, select
the workspace before retrieval. Re-read a matching source before making a factual
claim; summaries/hits locate evidence and do not replace it. If search is not ready,
use the known URI to read the source and report search as unverified.

[requests.json](requests.json) contains complete JSON-RPC request examples for
A → B → A, plus two independent calls that can be sent concurrently. These are
messages to send **after your MCP client initializes the connection**, not a batch
HTTP body or a standalone `curl` script. Send the objects individually. Result IDs
associate replies with their requests; arrival order does not select a project.
Every search result must stay inside the requested directory or its descendants.
Check URI segments, not a raw prefix that would also accept `project-a-other`.

## Workspace and peer settings

The supplied workspace settings pin `peer.id`, disable automatic recall and
capture, and select `recall.peer_scope: "actor"` for a future opt-in memory setup.
They use the existing
[workspace schema](https://github.com/volcengine/OpenViking/blob/5caada112a60783d4ec21234be90e95f0261fbd5/docs/en/configuration/02-client.md#workspace-configuration).
They contain no connection, account, or credential fields.

**Direct HTTP MCP and the `ov` CLI do not load these workspace files.** Shared
memory-plugin integrations consume them; higher-priority environment, machine,
and local settings can override them. No memory integration is installed in this
walkthrough, so these files do not enable capture. If hooks are already installed,
verify their effective settings separately; copying this example does not disable
every existing collector.

A long-lived global proxy cannot infer each request's workspace from its launch
directory. Keep the explicit URI arguments above. For optional peer-memory access,
a trusted client may set `X-OpenViking-Actor-Peer` per request, or use client
instances with fixed peer contexts. Do not change a shared process environment or
global “active project” while concurrent requests are in flight. The static MCP
template here sets no actor-peer header and promises no automatic peer switching.

## Scope and authorization are different

| Layer | What it controls |
| --- | --- |
| Global MCP registration | Where the client connects and authenticates |
| Workspace instructions and `target_uri` | Which project evidence a request selects |
| Workspace `peer.id` / actor-peer filter | Memory grouping / the current user's peer collection view |
| Account, user, and effective resource ACL | What the server authorizes that identity to access |

`target_uri` scopes retrieval; `read` uses its own URI and does not inherit the
previous search's scope. Workspace instructions help avoid mistakes but are not
authorization. The same test user can deliberately read both projects. Shared
`viking://resources` belong to the account; different project directory names do
not create tenants. Actor-peer filtering leaves ordinary shared resources and
user-level content under their existing permission rules, and a full-key client
can change or omit that header.

For enforced access restrictions, use the existing account/user boundaries and,
within shared resources, effective ACL grants. Account-level `acl.enabled` is
disabled by default; enabling it alone still leaves inherited root grants unless
the administrator configures restrictions. A read grant for a normal user can
enforce read-only resource access; this walkthrough's write-capable setup key
does not. MCP `readOnlyHint` and a client's visible tool list are not server-side
permission controls. See
[multi-tenancy](https://github.com/volcengine/OpenViking/blob/5caada112a60783d4ec21234be90e95f0261fbd5/docs/en/concepts/11-multi-tenant.md)
and [resource ACL](https://github.com/volcengine/OpenViking/blob/5caada112a60783d4ec21234be90e95f0261fbd5/docs/en/concepts/15-acl.md).

## Validation status

This example has local source-contract and workspace-loader checks. The setup
commands, live indexing/search, MCP transport, concurrent service calls, client
workspace switching, and ACL enforcement have **not** been verified end to end.
Expected answers above are acceptance criteria, not captured service results.
