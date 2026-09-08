## Step 1: Install

Because Claude Code may block installation scripts from unknown sources, automatic setup may not complete. We recommend running the manual terminal steps below.

1. Run the installer in your terminal:

   ```bash
   bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness claude --dist tos
   ```

2. The installer will ask for language (English / Chinese), OpenViking credentials, and whether to enable the Statusline.
3. In the OpenViking credential step, choose **VolcEngine OpenViking Cloud Service [api.vikingdb.cn-beijing.volces.com]** and enter the API KEY:

   ```text
   {{OPENVIKING_API_KEY}}
   ```

4. OpenViking StatusLine is the status strip under the input box. It shows the memory plugin runtime status in real time. Enable or skip it based on your preference. Example:

   ```text
   OV ✓ │ Fable 5 · ctx 42% │ ↪ 6 mem (0.92) · 50ms │ ✎ 573/20k · 2 arch
   ```

## Step 2: Verify

1. Restart Claude Code.
2. Run `/plugins` and confirm the installed list shows `openviking-memory`, and the `openviking` MCP is connected:

   ```text
   User
     ❯ openviking-memory Plugin · openviking · ✔ enabled
       └ openviking MCP · ✔ connected
   ```

3. Run `/mcp` and confirm it shows:

   ```text
   Built-in MCPs (always available)
     ❯ plugin:openviking-memory:openviking · ✔ connected · 10 tools
   ```

4. Run `/openviking-memory:ov` and confirm the service status is healthy:

   ```text
   OpenViking Memory Status
     ✅ Status: OpenViking server is healthy and running
   ```

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
