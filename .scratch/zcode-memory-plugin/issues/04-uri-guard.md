# 04 — Implement URI guard

**What to build:** The `PreToolUse` safety boundary — denies direct `Read`/`Glob`/`Grep` of `viking://` URIs and redirects the agent to OpenViking MCP tools. Uses the shared `agent-uri-guard.mjs` runtime. Emits `permissionDecision: "deny"` with a reason message naming the correct MCP alternative.

**Blocked by:** 02 (plugin scaffold + vendor)

**Status:** ready-for-agent

- [ ] `uri-guard.mjs` detects `viking://` URIs in tool input and returns deny decision
- [ ] Deny reason includes the correct MCP tool invocation example (e.g. `read(uris="viking://...")`)
- [ ] Output uses `permissionDecision: "deny"` (ZCode vocabulary, not Claude's `"approve"`)
- [ ] Pass-through (non-viking URIs) produces empty output + exit 0
