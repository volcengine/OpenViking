# Connect your agent with one prompt

Paste one prompt into the coding agent you already use. The agent installs the OpenViking memory plugin into the agents it finds on your machine, checks the connection, offers to bring in your recent work, and tells you how to restart. After the restart, your first question in a new session shows OpenViking at work.

The agent follows a short guide published as plain Markdown, so any agent can fetch it:

- English: `https://docs.openviking.ai/agents/en/onboarding.md`
- Chinese: `https://docs.openviking.ai/agents/zh/onboarding.md`

Both are also served from `docs.openviking.net`, which is usually faster from mainland China.

## The prompt

Copy the prompt and replace `<API_KEY>` with your key from the [OpenViking console](https://console.volcengine.com/vikingdb/openviking/region:openviking+cn-beijing?utm_source=opensource_docs&utm_medium=referral&utm_campaign=getting-started_07-agent-onboarding), under **User Management → API Key**, before you send it.

<AgentPrompt>

```text
Install the OpenViking memory plugin and help me start using it.

1. Run the official installer. It needs network access and writes to my home directory; if your sandbox blocks that, ask me for approval:
   curl -fsSL https://openviking.ai/install | bash -s -- --yes --lang en --url https://api.vikingdb.cn-beijing.volces.com/openviking --api-key <API_KEY>
2. Then continue with the official OpenViking onboarding guide (if docs.openviking.ai doesn't load, use docs.openviking.net):
   https://docs.openviking.ai/agents/en/onboarding.md

Use the API key in the command as it is, but don't repeat it in your replies or write it into any file or rule. Talk to me in English.
```

</AgentPrompt>

The installer sets up the plugin in every supported agent it detects on the machine, so other agents you have installed get it too; each one needs its own restart before the plugin works there. It also installs the `ov` command, which the agent uses to check the connection and add files. `--lang` sets the installer's language, and with it the language of the guide link it prints at the end. To limit the install to some agents, add `--harness` with a comma-separated list, for example `--harness claude,cli`; see [Unified installer](../agent-integrations/16-capability-reference.md#_3-1-2-unified-installer).

**Self-hosted server.** Replace the URL with your server's address. Use a user key; a root key can't read or write memories. For a local server without authentication, pass `--api-key ''`.

**About the key.** The installer accepts the key only as a command-line argument, so the key appears in this conversation and in your agent's local session history and logs. The guide tells the agent never to repeat it or write it anywhere, and to have you start a new session after the restart instead of resuming or reloading this one. The plugins don't yet remove the key from the conversations they save, so on some agents (Codex, for example, keeps the old session running after you quit) this install conversation, key included, can still be saved to your own OpenViking account. If you'd rather not paste the key into a chat, run `curl -fsSL https://openviking.ai/install | bash` in a terminal yourself, then send your agent the prompt under [Already installed](#already-installed).

## Already installed

If the plugin is already installed, start a new session in your agent and send:

```text
Follow https://docs.openviking.ai/agents/en/onboarding.md to walk me through my first use of OpenViking.
```

## What the agent does

1. **Checks the install.** It reads the installer's output and runs `ov health`, then tells you which agents were set up and which server they are connected to.
2. **Explains what runs automatically.** Before each prompt, the plugin recalls relevant memories. After each turn, it saves the conversation, and the server extracts long-term memories in the background. You don't need to approve this separately; asking for the install is the approval.
3. **Asks whether to import recent work.** The import reads Claude Code and Codex history; the default is the current project's sessions from the last 14 days. It shows you the number of sessions and messages first and imports only after you confirm. Only user and assistant text is sent; tool calls and their output are dropped and API keys are removed. On other agents it offers this only when Claude Code or Codex history exists on the machine; importing their own history isn't supported yet. You can also name files, such as a design doc, to add with `ov add-resource`.
4. **Offers one rule, on some agents.** TRAE, TRAE CN, ZCode and Kimi Code ship no OpenViking skill, so the agent offers a short rule for your personal rules that tells it when to search OpenViking. On Kimi Code it adds the rule to `~/.kimi-code/AGENTS.md`; on the others it asks you to paste it into the app's rules settings. It never writes rules into project files.
5. **Tells you how to restart.** Hooks load only in a new session. The agent gives you the exact step for the agent you are talking to (for example, **Trust all and continue** in Codex) and a first message to send after the restart. If the installer set up other agents too, it tells you they need a restart as well.
6. **Shows OpenViking at work.** In the new session, your first question gets an answer drawn from recalled context, and the agent points out where that shows up: source cards in Claude Code, the OV-Usage line in Codex, and so on.

The whole flow asks you at most two questions: what to import (with a confirmation after you see the counts) and, on some agents, whether to add the rule.

## See also

- [Agent integrations overview](../agent-integrations/01-overview.md)
- [Agent integration capabilities](../agent-integrations/16-capability-reference.md)
- [Install and use the CLI](05-cli-setup.md)
