# ZCode Memory Integration

Adds cross-project, cross-session long-term memory to ZCode. After installation, OpenViking hooks automatically inject relevant context at session start, recall memories on each prompt, and capture conversation turns for the memory extractor. MCP tools enable manual search, read, and management of memories.

## Install

Prerequisites: Node.js 18+, an OpenViking server running locally or remotely, and ZCode installed on your machine.

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness zcode
```

For regions where GitHub is unreachable, use the TOS mirror:

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) \
  --harness zcode --dist tos
```

The installer detects ZCode via `~/.zcode/` or a `zcode` binary, merges hooks and MCP config into `~/.zcode/cli/config.json`, and writes OpenViking credentials to `~/.openviking/ovcli.conf`.

## Verification

After install, restart ZCode. Then:

- **Settings → Plugin Management** — not applicable (ZCode uses config-file hooks, not plugin marketplace for this integration).
- Check `~/.zcode/cli/config.json` for `hooks.events` entries containing `openviking-memory` and `mcp.servers.openviking`.
- Set `OPENVIKING_DEBUG=1` in the ZCode process environment (or `claude_code.debug: true` in `~/.openviking/ov.conf`) and check `~/.openviking/logs/zcode-hooks.log`.

## How it works

The plugin mounts into ZCode's lifecycle at four points:

- **SessionStart** — injects user profile and preferences/entities into context.
- **UserPromptSubmit** — searches OpenViking for relevant memories and injects them as `<openviking-context>`.
- **PreToolUse** (`Read|Glob|Grep`) — denies direct access to `viking://` URIs, redirecting to OpenViking MCP tools.
- **Stop** — captures incremental user/assistant turns and commits the OpenViking session.

ZCode does not support `PreCompact`/`SessionEnd`/`SubagentStart`/`SubagentStop`, so the commit-on-`Stop` strategy compensates for the absence of compact/end-of-session signals.

All data writes are asynchronous and do not block the conversation.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Hooks not firing | `hooks.enabled` not set in config.json | Re-run the installer, or manually set `"hooks": { "enabled": true }` in `~/.zcode/cli/config.json` |
| Recall returns nothing | OpenViking server not running or no memories extracted yet | Check `curl http://127.0.0.1:1933/health`; wait for memory extractor to process captured turns |
| MCP tools not appearing | MCP server failed to start | Check `~/.zcode/cli/config.json` → `mcp.servers.openviking` has correct absolute path to `mcp-proxy.mjs` |
| Duplicate captures | Re-installed without uninstalling | Run `install.sh --harness zcode --uninstall` first, then reinstall |

## See also

- [Plugin README](https://github.com/volcengine/OpenViking/tree/main/examples/zcode-memory-plugin)
- [DESIGN.md](https://github.com/volcengine/OpenViking/tree/main/examples/zcode-memory-plugin/DESIGN.md) — verified ZCode extension-surface facts
- [MCP clients](./06-mcp-clients.md)
- [Deployment guide → CLI](../guides/03-deployment.md#cli)
