# Connect your agent with one prompt

Paste one prompt into the coding agent you already use. The agent installs the OpenViking memory plugin into the agents it finds on your machine, checks the connection, offers to bring in your recent work, and tells you how to restart. After the restart, your first question shows OpenViking at work.

The agent follows a short guide published as plain Markdown, so any agent can fetch it:

- English: `https://docs.openviking.ai/agents/en/onboarding.md`
- Chinese: `https://docs.openviking.ai/agents/zh/onboarding.md`

Both are also served from `docs.openviking.net`, which is usually faster from mainland China.

## The prompt

Copy the prompt and replace `<API_KEY>` with your key from the [OpenViking console](https://console.volcengine.com/vikingdb/openviking/region:openviking+cn-beijing?utm_source=opensource_docs&utm_medium=referral&utm_campaign=getting-started_07-agent-onboarding), under **User Management → API Key**, before you send it.

<AgentPrompt>

```text
Install the OpenViking memory plugin and help me start using it. OpenViking is the open-source context database for AI agents from Volcengine (https://github.com/volcengine/OpenViking, AGPL-3.0); the command and link below are from its official docs.

1. Run the official installer. It needs network access and writes to my home directory; if your sandbox blocks that, ask me for approval:
   curl -fsSL https://openviking.ai/install | bash -s -- --yes --lang en --url https://api.vikingdb.cn-beijing.volces.com/openviking --api-key <API_KEY>
2. Once it's installed, read the OpenViking onboarding guide and use it to walk me through setup. The guide has you ask me before choices like importing history or adding a working rule. docs.openviking.ai and docs.openviking.net are the same docs site; use whichever loads:
   https://docs.openviking.ai/agents/en/onboarding.md

The API key is one I created for myself in the OpenViking console; the installer stores it in ~/.openviking/ovcli.conf on this machine. Use it in the command as it is; there's no need to show it in your replies, and don't write it into any other file or rule. Talk to me in English.
```

</AgentPrompt>

The installer sets up the plugin in every supported agent it detects on the machine, so other agents you have installed get it too; each one needs its own restart before the plugin works there. It also installs the `ov` command, which the agent uses to check the connection and add files. `--lang` sets the installer's language, and with it the language of the guide link it prints at the end. To limit the install to some agents, add `--harness` with a comma-separated list, for example `--harness claude,cli`; see [Unified installer](../agent-integrations/16-capability-reference.md#_3-1-2-unified-installer).

**Self-hosted server.** Replace the URL with your server's address. Use a user key; a root key can't read or write memories. For a local server without authentication, pass `--api-key ''`.

**About the key.** The installer accepts the key only as a command-line argument, so the key appears in this conversation and in your agent's local session history and logs. The guide tells the agent never to repeat it or write it anywhere. The plugins don't yet remove the key from the conversations they save, and the first save after the restart includes the install conversation: in Codex this happens whether you resume it or start a new session, so this install conversation, key included, can be saved to your own OpenViking account. In Claude Code a new session avoids it, which is why the agent asks you not to resume the install conversation there. If you'd rather not paste the key into a chat, run `curl -fsSL https://openviking.ai/install | bash` in a terminal yourself, then send your agent the prompt under [Already installed](#already-installed).

## Already installed

If the plugin is already installed, start a new session in your agent and send:

```text
Follow https://docs.openviking.ai/agents/en/onboarding.md to walk me through my first use of OpenViking.
```

## What the agent does

1. **Checks the install.** It reads the installer's output and runs `ov health`, then tells you which agents were set up and which server they are connected to.
2. **Explains what runs automatically.** Before each prompt, the plugin recalls relevant memories. After each turn, it saves the conversation, and the server extracts long-term memories in the background. You don't need to approve this separately; asking for the install is the approval.
3. **Asks whether to bring in earlier work.** The default is the current project's sessions from the last 14 days. You can also describe one kind of work, such as "the discussions about the payment refactor": the agent lists the candidate sessions with a preview of each, picks the ones that match, and shows you its picks before anything is sent. The import script reads Claude Code and Codex history itself; other agents first write a small converter that turns their own sessions into JSONL, then hand that to the same script. It shows you the number of sessions and messages first and imports only after you confirm. Only user and assistant text is sent; tool calls and their output are dropped and API keys are removed. If Claude Code or Codex history exists on the machine, other agents offer that too. You can also name files, such as a design doc, for the agent to add with `ov add-resource` to this project's folder in your personal resources.
4. **Offers a lasting collaboration rule.** The agent offers to add a rule to your user-level rules file (for example `~/.claude/CLAUDE.md` for Claude Code, `~/.codex/AGENTS.md` for Codex), so it applies to all your projects: use the context OpenViking recalls and search only when that isn't enough; keep notes on important decisions, progress, intermediate outputs and deliverables in a notes folder of your OpenViking personal resources, one per project (`resources/notes/<project>/`, apart from the files you add); look at those notes first when continuing a project; and say where it saved something. The rule sits between a pair of `<!-- openviking:start -->` / `<!-- openviking:end -->` markers and the rest of the file is left alone. It is written only if you agree, and read back afterwards. On agents that keep personal rules only in their settings (TRAE or Cursor, for example), the agent asks you to paste it there. It never writes rules into project files.
5. **Tells you how to restart.** The plugin loads only when the agent starts again. The agent gives you the exact steps for the agent you are talking to. In Codex you quit, run `codex resume --last` to come back to the same conversation, choose **Trust all and continue**, and say "restarted". In Claude Code and the other agents you start a new session and send the first message it gives you. If the installer set up other agents too, it tells you they need a restart as well.
6. **Shows OpenViking at work.** After the restart, your first question gets an answer drawn from recalled context, and the agent points out where that shows up: source cards in Claude Code, the OV-Usage line in Codex, and so on.

The agent asks you only for decisions: what to bring in (with a confirmation after you see the counts) and whether to add the collaboration rule, each with what it gives you and what it sends or changes. Choices you have already made are not asked again.

## See also

- [Agent integrations overview](../agent-integrations/01-overview.md)
- [Agent integration capabilities](../agent-integrations/16-capability-reference.md)
- [Install and use the CLI](05-cli-setup.md)
