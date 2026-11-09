# Shared Skills and MCP tools (opt-in preview)

Set `agents[].capabilitiesFile` to an absolute JSON path. Core reads it once;
restart that instance after changes. Unconfigured instances expose no extra
tools. Ark has no Core tool transport and cannot enable this option.
A standalone daemon can use `VIKA_CAPABILITIES_FILE`; managed instances always
use their own manifest entry, clearing any inherited setting.

```json
{
  "skills": {
    "review": { "directory": "/srv/skills/review", "description": "Review a patch" }
  },
  "mcpServers": {
    "docs": {
      "command": "/usr/bin/node",
      "args": ["/srv/mcp/docs-server.mjs"],
      "env": {},
      "tools": ["search_docs"]
    }
  }
}
```

On Windows use absolute Windows paths and an explicit executable, for example
`C:/Program Files/nodejs/node.exe` with the server script as a separate argument.
Core uses the same platform executable adapter as the runtimes.

- `list_capabilities` lists configured Skills and MCP server/tool names.
- `read_skill` reads `SKILL.md` or a relative text resource, returning its path,
  content and SHA-256. Files are limited to 128 KiB; traversal and symlink escape
  are refused. Keep the configured directories operator-owned and stable.
- `list_mcp_tools` lazily starts a server and returns allowlisted schemas.
- `call_mcp_tool` takes `server`, `tool` and `arguments`. It preserves the MCP
  result, including `isError`, in Core result data. Read the schema before calling.

Core reuses the existing Pi MCP client/stdio transport. It inherits only HOME,
PATH and platform system environment keys, plus explicit manifest `env` values.
Unrelated parent credentials are not inherited; keep the manifest private.
Initialization/catalogue requests and tool calls are bounded; tool calls time
out after 30 seconds. Stderr is not forwarded to shared logs. Failed connections
stay failed until restart, and uncertain calls are never automatically retried.
Shutdown closes clients even while initialization is pending.

The same Core tools reach Durable and MCP-based runtimes through their existing
transports. Runtime-native plugins, hooks and private Skill directories remain
independent; this feature neither imports nor replaces them. Shared Skill text
is guidance, not authority, and scripts are not automatically executed.

Servers run as trusted operator-supplied programs, not in a sandbox. Allowlists
are not per-call approval: expose only tools appropriate for the instance's
users, and do not bypass Core message delivery. HTTP/OAuth, resources/prompts,
sampling, elicitation, hot reload and native MCP image forwarding are not
included. This does not change Core's existing OpenViking tools.
