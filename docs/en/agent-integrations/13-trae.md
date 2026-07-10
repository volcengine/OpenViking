# TRAE and TRAE CN Memory Integration

Add long-term memory across TRAE and TRAE CN projects and sessions. Once installed, the integration automatically recalls relevant memories, captures new conversations, and provides OpenViking memory tools.

## Install

```bash
# TRAE
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) --harness trae

# TRAE CN
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) --harness trae-cn

# Both
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) --harness trae,trae-cn
```

If GitHub is unavailable, use the Volcengine TOS mirror:

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) \
  --harness trae,trae-cn --dist tos
```

Restart TRAE after installation.

## Features

- Loads your profile and project memory when a new session starts.
- Automatically recalls relevant context for the current request.
- Saves new user and assistant messages after each conversation.
- Provides OpenViking tools for searching and managing memory.

## Verify

1. Restart TRAE and start a new Agent session.
2. Ask about a previous project or preference and confirm that TRAE can use existing memory.
3. Confirm that `openviking` is connected in the TRAE MCP settings.

## Upgrade and uninstall

Re-run the corresponding install command to upgrade.

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness trae-cn --uninstall --yes
```

Replace `trae-cn` with `trae` to uninstall the TRAE integration.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Automatic recall does not run after installation | Quit TRAE completely, restart it, and create a new Agent session. |
| Recall runs more than once | Re-run the install command and restart TRAE. |
| Connection or authentication fails | Check the server URL and API key in `~/.openviking/ovcli.conf`. |
