---
name: opencode
description: Monitor and manage your OpenCode tasks using helper scripts.
metadata: {"vikingbot":{"emoji":"💻","requires":{"bins":["python3"]}}}
---

# OpenCode Skill

Use helper scripts to manage your OpenCode instances.

## Helper Scripts

All scripts are in the workspace skills directory. Run them with:
```bash
uv run python skills/opencode/script_name.py
```

Note: Do not kill the opencode process.

## Scripts

### `list_sessions.py`

List all OpenCode sessions updated in the past day, including their status and new messages.

Example:

```bash
uv run python skills/opencode/list_sessions.py
```

## Session Status Types

- **🟢 WAITING**: Last message was from user - agent is waiting for input
- **🔴 WORKING**: Last message was from assistant - agent recently finished or may be working
- **🟡 UNKNOWN**: Cannot determine status

