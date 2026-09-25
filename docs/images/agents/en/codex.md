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

1. Start Codex. The first launch shows a hook review prompt similar to this (exact layout varies by version); pick **Trust all and continue**:

   ```text
   Hooks need review
   6 hooks are new or changed.
   Hooks can run outside the sandbox after you trust them.

     1. Review hooks
   > 2. Trust all and continue
     3. Continue without trusting (hooks won't run)
   ```

   If you skipped it or picked option 3, open `/hooks`. Review the OpenViking commands, then trust and enable the hooks you intend to use. In `/plugins`, confirm that `openviking-memory` is enabled. New or changed hooks require another review.
2. Confirm that OpenViking MCP tools are available, then ask Codex to call `health` and `list`. This verifies tool access; automatic recall also requires `UserPromptSubmit`, and capture requires `Stop`.
3. In a workspace with saved memories, ask about previously stored information and check the hook output or debug log. Recalled context looks similar to this:

   ```text
   • UserPromptSubmit hook (completed)
     hook context: <openviking-context source="auto-recall" format="digest">
       OpenViking memory digest:
   ```

   An empty new account may have no profile or relevant memories to inject. See the full guide for lifecycle commits and a complete memory check.

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
