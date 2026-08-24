## Step 1: Install

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness claude --dist tos
```

Select **Volcengine OpenViking Cloud**. Paste the API key:

## Step 2: Verify

Restart Claude Code, then:

- `/plugins` → **openviking-memory** is installed, **openviking** MCP is connected
- `/mcp` → shows the cloud URL and valid auth
- `/openviking-memory:ov` → server is healthy

## Troubleshoot

| Problem | Fix |
|---|---|
| Plugin is not active | Re-run Install, or check `~/.openviking/ovcli.conf` |
| Recall is empty | `curl "$(jq -r '.url' ~/.openviking/ovcli.conf)/health"` |
| 401 / 403 | Paste the API key again |
| Need logs | `OPENVIKING_DEBUG=1` and `~/.openviking/logs/cc-hooks.log` |

## Reference

- Docs on Manual Settings: [Claude Code](https://docs.openviking.net/en/agent-integrations/02-claude-code)
- Blog about how it works: [OpenViking for coding agents](https://blog.openviking.ai/post/openviking-coding-agent/)
- Code: [examples/claude-code-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/claude-code-memory-plugin)
