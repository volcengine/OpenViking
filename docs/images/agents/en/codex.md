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

1. Start Codex. The first launch stops on a hook trust prompt — pick **Trust all and continue**:

   ```text
   Hooks need review
   6 hooks are new or changed.
   Hooks can run outside the sandbox after you trust them.

     1. Review hooks
   > 2. Trust all and continue
     3. Continue without trusting (hooks won't run)
   ```

   The six hooks OpenViking registers are (an older Codex may show fewer):

   ```text
   SessionStart
   UserPromptSubmit
   PreToolUse
   Stop
   SessionEnd
   PreCompact
   ```

2. If you miss the prompt or pick the third option, the hooks never run. Enter `/hooks` to trust and enable the entries, and check `/plugins` shows `openviking-memory` as enabled — two independent switches, both have to be on. A plugin update that touches a hook asks for trust again.

3. Verify Profile loading: once trusted, submit your first Prompt. Any prompt is fine. The plugin should load your Profile automatically. If the beginning of the conversation contains recalled memory context, the integration is working:

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
| `6 hooks need review`, or no hook fires | Trust and enable in `/hooks`; confirm the plugin is enabled in `/plugins` |
| Need logs | `OPENVIKING_DEBUG=1` and `~/.openviking/logs/codex-hooks.log` |

## Reference

- Docs on Manual Settings: [Codex](https://docs.openviking.net/en/agent-integrations/04-codex)
- Blog about how it works: [OpenViking for coding agents](https://blog.openviking.ai/post/openviking-coding-agent/)
- Code: [examples/codex-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/codex-memory-plugin)
