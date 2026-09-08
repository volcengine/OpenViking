## Step 1: Install

1. Run the installer in your terminal:

   ```bash
   bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness codex --dist tos
   ```

2. The installer will ask for language (English / Chinese) and OpenViking credentials. In the OpenViking credential step, choose **VolcEngine OpenViking Cloud Service [api.vikingdb.cn-beijing.volces.com]** and enter the API KEY:

   ```text
   {{OPENVIKING_API_KEY}}
   ```

## Step 2: Verify

1. Start Codex.
2. Approve Hooks: enter `/hooks`. Codex should show a prompt similar to `4 hooks need review`; approve them one by one. The four OpenViking hooks are:

   ```text
   SessionStart
   UserPromptSubmit
   Stop
   PreCompact
   ```

3. Verify Profile loading: after approval, submit your first Prompt. Any prompt is fine. The plugin should load your Profile automatically. If the beginning of the conversation contains recalled memory context, the integration is working:

   ```text
   • UserPromptSubmit hook (completed)
     hook context: <openviking-context source="auto-recall" format="digest">
       OpenViking memory digest:
   ```

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
