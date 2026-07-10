# OpenViking Memory Integration for Cursor

One command installs lifecycle hooks, an always-on rule, a memory skill, and the OpenViking MCP server. No marketplace listing or separate MCP setup is required. See the [Cursor integration guide](../../docs/en/agent-integrations/12-cursor.md) for installation and verification.

The hooks automatically inject baseline context on session start, prefetch prompt-specific memory, inject it after the first tool result, capture transcript deltas on stop, and commit before compaction or session end.
