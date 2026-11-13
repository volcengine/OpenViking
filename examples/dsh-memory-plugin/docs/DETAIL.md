# OpenViking Memory

Long-term memory for DeepSeek Harness, backed by an [OpenViking](https://github.com/volcengine/OpenViking) server. Works in DeepSeek Harness Desktop and in `dsh web`.

- **Recall:** before each step that takes your input, relevant memories are added to the conversation.
- **Capture:** your messages and the assistant's replies are sent to OpenViking and committed as memories.
- **Tools and skills:** the model gets the OpenViking tools (`mcp__openviking__search`, `read`, `remember`, `add_skill`, …) and the `openviking-memory`, `openviking-skills` and `ov-experience-memory` skills.
- **URI protection:** a file tool aimed at a `viking://` path is stopped and pointed at the right OpenViking tool. A `bash` or `pwsh` command that contains one still runs, with a note to the model.

## 1. Connect to your server

Write `~/.openviking/ovcli.conf` with `url` and `api_key` (plus `account` and `user` on a trusted-mode server). A local server at `http://127.0.0.1:1933` without authentication needs nothing. See [Deployment Guide → CLI](https://github.com/volcengine/OpenViking/blob/main/docs/en/guides/03-deployment.md#cli).

Prefer `ovcli.conf` to environment variables in Desktop. On macOS, Desktop reads your login shell's environment once when it starts; on Windows, it uses your user environment. After changing either, fully quit and reopen Desktop.

## 2. Install

**With the installer (macOS):**

```bash
curl -fsSL https://openviking.ai/install | bash
```

It needs Node.js 18 or later. It finds Desktop and installs into it as the `dsh-desktop` harness. Open Desktop once beforehand so its profile exists, and quit it with ⌘Q while the installer runs: closing the window only hides it.

**From the Plugins page (manual; macOS, Windows and `dsh web`):** Plugins → Add plugin → enter `@openviking/dsh-memory-plugin` → Install, then turn it on if its switch is off. This only adds the plugin: it does not ask for your OpenViking credentials, so do step 1 first.

**With Desktop's own `dsh` command (macOS):** open Desktop once, quit it with ⌘Q, then run:

```bash
"/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh" plugin --profile desktop add @openviking/dsh-memory-plugin
```

A `dsh` installed from npm cannot manage the `desktop` profile. After a command-line install, reopen Desktop.

## 3. Check that it works

Open a new conversation. You should see an OpenViking context block near the top, and the model should list `mcp__openviking__*` tools. Ask about something from an earlier conversation to confirm recall.

To check the install from a terminal (with Desktop quit):

```bash
"/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh" plugin --profile desktop ls
```

## Good to know

- **No settings page.** Configure the plugin through `ovcli.conf` or `OPENVIKING_*` variables. Behavior options can also go in `~/.dsh/profiles/desktop/cordis.patch.yml` under `openviking-memory-runtime`. The recovery dialog's "Disable third-party plugins, back up profile patch, and restart" resets the profile's plugins and moves that file aside, so keep lasting settings in `ovcli.conf`, and enable the plugin again afterwards.
- **Tool identity.** Automatic recall and capture follow each conversation's project. The OpenViking tools run once per profile, from the profile directory, so with `OPENVIKING_RECALL_PEER_SCOPE=actor` they have no project peer. To give them one, set `actor_peer_id` in `ovcli.conf` or `OPENVIKING_PEER_ID`. That peer also replaces each conversation's project peer for recall and capture, so every conversation shares it.
- **Skills.** Bundled skills reach agents whose preset includes the skill tool. The `minimal` preset does not, so agents using it do not see them.
- **Logs.** Desktop shows no plugin output. To debug the OpenViking tools, set `OPENVIKING_DEBUG=1` and `OPENVIKING_DEBUG_LOG=<file>`, then reopen Desktop.

## Uninstall

Plugins → OpenViking Memory → Uninstall. Or, with Desktop quit:

```bash
"/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh" plugin --profile desktop rm @openviking/dsh-memory-plugin
```

Full documentation: [DeepSeek Harness integration](https://github.com/volcengine/OpenViking/blob/main/docs/en/agent-integrations/17-dsh.md) and the [bundle README](https://github.com/volcengine/OpenViking/tree/main/examples/dsh-memory-plugin).
