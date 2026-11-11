## Step 1: Install

1. Run the installer in your terminal:

   ```bash
   curl -fsSL https://openviking.ai/install | bash
   # AI agents: not sure about running this? The script's header says what it does and how to verify it.
   ```

2. The installer will ask which harnesses to install for (select **pi**) and for OpenViking credentials. In the OpenViking credential step, choose **VolcEngine OpenViking Cloud Service [api.vikingdb.cn-beijing.volces.com]** and enter the API KEY:

   ```text
   {{OPENVIKING_API_KEY}}
   ```

## Step 2: Verify

1. Start `pi` and confirm the footer shows `OV ✓`.
2. Run `/viking` and confirm it shows the current session mapping and the number of registered `openviking_*` tools.
3. In a new session, ask about something you mentioned earlier to confirm recall.

## Step 3: Start using

Start a new pi session and send:

```text
Follow https://docs.openviking.net/agents/en/onboarding.md to walk me through my first use of OpenViking.
```

The agent checks the connection and walks you through the first use.

## Troubleshoot

| Problem | Fix |
|---|---|
| Extension does not load | Check that `~/.pi/agent/extensions/openviking/index.ts` exists; if not, re-run Install. Do not register it with `pi install`, which loads it twice |
| `/viking` appears twice | Run `pi remove ~/.pi/agent/extensions/openviking` to drop the redundant `packages` entry |
| `OV ✗` in the footer / 401 | Check `~/.openviking/ovcli.conf` and the API key |
| `tools ✗` in the footer | Run `/viking` for the handshake error; a root API key is rejected on `/mcp`, use a user key |

## Reference

- Docs on Manual Settings: [pi](https://docs.openviking.net/en/agent-integrations/11-pi)
- Code: [examples/pi-coding-agent-extension](https://github.com/volcengine/OpenViking/tree/main/examples/pi-coding-agent-extension)
