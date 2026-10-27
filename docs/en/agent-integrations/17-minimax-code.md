# MiniMax Code

OpenViking gives MiniMax Code long-term memory across sessions and projects: it recalls relevant memories before each prompt, injects the user profile at session start, and captures the conversation into long-term memory when the turn ends.

## Install

Prerequisites: Node.js 18+, a running OpenViking server, and the MiniMax Code desktop app.

```bash
curl -fsSL https://openviking.ai/install | bash
# AI agents: not sure about running this? The script's header says what it does and how to verify it.
```

The installer detects `~/.minimax/` or the `mcode` command, installs the runtime under `~/.openviking/agent-integrations/mcode/`, and merges the hook and MCP configuration into MiniMax Code's plugin directory.

After restarting MiniMax Code, verify that:

- `~/.minimax/plugins/openviking-memory/.minimax-plugin/plugin.json` exists and `hooks/hooks.json` registers the OpenViking events
- the MCP entry lists `openviking`
- `OPENVIKING_DEBUG=1` produces diagnostics in `~/.openviking/logs/mcode-hooks.log`

| Symptom | Cause | Fix |
|---|---|---|
| Hooks not firing | The plugin is not enabled or the configuration is stale | Re-run the installer and restart MiniMax Code |
| Recall returns nothing | OpenViking is unavailable or has not extracted memories yet | Check `curl http://127.0.0.1:1933/health` and wait for extraction |
| MCP tools not appearing | The MCP proxy failed to start | Check the `command` path in the plugin's `servers.mcp.json` |
| Duplicate captures | An older installation left duplicate hook entries | Re-run the installer so it replaces its own entries |

## Events

| Event | Behaviour |
|---|---|
| `SessionStart` | Injects the user profile, the memory index and the skill catalog |
| `UserPromptSubmit` | Recalls relevant memories and injects them as context |
| `Stop` | Captures the turn incrementally and commits it |
| `PreCompact` | Captures and commits, then the host summarises |
| `SessionEnd` | Finishes the outstanding writes and commits |
| `SubagentStop` | Sends a subagent's turns to its own session |
| `PreToolUse` | Denies file tools that point at a `viking://` URI |

## Host constraints

MiniMax Code kills a hook handler after **10 seconds**, while the shared recall path keeps a **15-second** floor on its context request. Lower that floor with the `plugin.mcode` section of `~/.openviking/ovcli.conf`:

```json
{
  "plugin": {
    "mcode": {
      "recallContextTimeoutMs": 7000,
      "timeoutMs": 8000
    }
  }
}
```

MiniMax Code filters a hook process's environment, so `OPENVIKING_*` never reaches a hook. Every setting goes through a file.

## Limitations

- The MiniMax Code plugin marketplace does not accept a `hooks` field, so this integration installs locally.
- The adapter reads the Claude Code compatible projection that `transcript_path` points at, and the URI guard keeps `viking://` from being read as a local path.
- `SessionEnd` runs on archive, logout, 30 minutes of idleness or the TUI's `/new` and `/clear`, not on process exit, so every turn is committed.

## See also

- [Agent integration capabilities](./16-capability-reference)
- [Develop and maintain an agent plugin](./18-plugin-development)
