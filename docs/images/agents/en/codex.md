## Step 1: Install

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness codex --dist tos
```

Select **Volcengine OpenViking Cloud**. Paste the API key:

## Step 2: Verify

Launch `codex`. Approve hooks once with `/hooks`. The first prompt should load your profile.

## Troubleshoot

| Problem | Fix |
|---|---|
| Auth error | Check `api_key` in `~/.openviking/ovcli.conf`, restart Codex |
| Connection error | `curl "$(jq -r '.url' ~/.openviking/ovcli.conf)/health"` |
| `4 hooks need review` | `/hooks` and approve |
| Need logs | `OPENVIKING_DEBUG=1` and `~/.openviking/logs/codex-hooks.log` |

## Reference

- Docs on Manual Settings: [Codex](https://docs.openviking.net/en/agent-integrations/04-codex)
- Blog about how it works: [OpenViking for coding agents](https://blog.openviking.ai/post/openviking-coding-agent/)
- Code: [examples/codex-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/codex-memory-plugin)
