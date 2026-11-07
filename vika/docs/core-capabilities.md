# Shared Core capabilities (opt-in preview)

Core can expose a local Skill catalogue and allowlisted stdio MCP tools to
runtimes using the existing Agent tool surface. This includes Durable, without
installing Pi extensions. It does not load runtime-specific hooks, replace
runtime-native plugins, or implement the OV tools proposed in #7079.

Set `agents[].capabilitiesFile` to an absolute JSON path in the deployment
manifest. Configuration is read once; restart that instance after changes.
Unconfigured instances expose no extra tools. Ark does not support this option.
A standalone daemon can use `VIKA_CAPABILITIES_FILE`; managed instances override
that environment setting with their own manifest entry.

```json
{
  "skills": {
    "review": {
      "directory": "/srv/skills/review",
      "description": "Review a patch using the team's checklist"
    }
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

The operator supplies the Skill descriptions explicitly; Core does not scan
personal runtime directories or parse an alternate Skill frontmatter dialect.
`list_capabilities` discovers entries; `read_skill` reads `SKILL.md` by default,
or a relative text resource. The result carries a source path and SHA-256.
Read Skill instructions before acting. Content and server descriptions are
untrusted context, not permissions. Files are limited to 128 KiB; traversal and
symlinks resolving outside the configured skill directory are rejected.
Directories must remain operator-managed and stable while being read; this is
not a sandbox against a process that can mutate the same filesystem.

`list_mcp_tools` starts the selected server lazily and returns only operator-
allowlisted tool schemas. `call_mcp_tool` takes `server`, `tool`, and `arguments`.
It returns the MCP result in data, including `isError` and content blocks.
This is a discovery/call bridge, not dynamic registration of individual tool
schemas. MCP images are currently returned as data, not native image blocks.
The first catalogue is fixed for that connection. Reconnect after failure and
tool-list changes require restarting the instance. Core never retries calls;
transport failures and oversized results may follow completed side effects.
The caller must not interpret them as evidence that nothing happened.

Servers use an absolute executable and argument array (no shell), the instance
workspace, MCP SDK's minimal default environment plus the explicit `env`, and
30-second tool request timeouts. Configure secrets locally with restricted file
permissions; never commit the real manifest or credentials. Child stderr is
not copied to shared logs. Servers are operator-trusted code running with the
Core user's OS privileges, not isolated sandboxes. The allowlist is admission,
not per-call approval; only configure tools appropriate to this instance's
users. External messaging tools must not bypass Core delivery controls.

No HTTP/OAuth transport, MCP resources/prompts, sampling, elicitation, or
runtime-native plugin hooks are provided in this increment. No automatic Skill
script execution or resource copying occurs. Local runtimes sharing the host
can use returned resource paths with their existing authorized execution tools;
remote runtimes require a separate resource-delivery design. No approval UI is
added. Model compliance with a Skill is not guaranteed by making it readable.

Validation covers real stdio MCP handshake/calls/close, allowlists, preserved
error results, stale runtime authority, Skill path containment, and Core tool
gating. Human review of the new configuration contract remains required.
