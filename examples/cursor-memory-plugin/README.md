# OpenViking Memory Integration for Cursor

One command installs lifecycle hooks, an always-on rule, a memory skill, and the OpenViking MCP server. No marketplace listing or separate MCP setup is required. See the [Cursor integration guide](../../docs/en/agent-integrations/12-cursor.md) for installation and verification.

The hooks inject baseline context on session start and prompt-specific memory before each request, capture transcript deltas on stop, and commit before compaction or session end. They also prevent `viking://` virtual paths from being passed to local file or shell tools.
