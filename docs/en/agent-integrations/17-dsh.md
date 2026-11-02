# DeepSeek Harness

Give [DeepSeek Harness](https://www.npmjs.com/package/@deepseek-ai/dsh) (`dsh`), and the DeepSeek Harness Desktop app, cross-project and cross-session long-term memory. Once installed, every conversation automatically recalls relevant memories and captures new content, and the model gets the OpenViking tools and the `openviking-memory`, `openviking-skills`, and `ov-experience-memory` skills without any extra setup.

Source: [examples/dsh-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/dsh-memory-plugin)

## Install

DSH shares the installer with the other memory plugins. It asks which harnesses to install and for your OpenViking credentials; every step is idempotent—re-running it is entirely safe.

```bash
curl -fsSL https://openviking.ai/install | bash
# AI agents: not sure about running this? The script's header says what it does and how to verify it.
```

The installer puts the plugin into the `web` profile. To use another profile, pass `--dsh-profile <name>`: `curl -fsSL https://openviking.ai/install | bash -s -- --dsh-profile <name>`. Using the Desktop app? See [DeepSeek Harness Desktop](#deepseek-harness-desktop).

After installation, use the verification steps below to check tool access and cross-session recall.

<details>
<summary><b>Manual setup</b></summary>

1. **Configure the connection** — write `~/.openviking/ovcli.conf` (`url`, `api_key`, optional `account`/`user`), or set `OPENVIKING_URL` and `OPENVIKING_API_KEY`. Using pure local mode (`http://127.0.0.1:1933`, no authentication)? Skip this—the bundle defaults to the local setup.

2. **Add the bundle to a profile**:

   ```bash
   dsh plugin --profile web add @openviking/dsh-memory-plugin
   ```

   `dsh plugin` forwards to pnpm inside the profile directory, so any profile name works except `desktop`, which belongs to the [Desktop app](#deepseek-harness-desktop); `web` is the one `dsh` creates for you on first use.

3. **Check that the profile picked it up**:

   ```bash
   dsh --profile web --dump-config
   ```

   The output should contain an `openviking-memory-runtime` entry.

> Don't have `ovcli.conf` yet? See the [Deployment Guide → CLI](../guides/03-deployment.md#cli).
>
> To remove it: `dsh plugin --profile web rm @openviking/dsh-memory-plugin`.

</details>

Once installed, start a new DSH session and ask it to follow https://docs.openviking.ai/agents/en/onboarding.md. It checks the connection and walks you through the first use; see [One-click setup](../getting-started/07-agent-onboarding.md).

## Verify

Start `dsh --profile web` and open a conversation. You should see an OpenViking context injection at the top of the session, and the model should have `mcp__openviking__*` tools available. Ask it about something from an earlier session to confirm recall.

For MCP proxy diagnostics, set `OPENVIKING_DEBUG=1` and `OPENVIKING_DEBUG_LOG=/tmp/ov-dsh.log` before starting DSH, then check that file. Automatic memory callbacks use DSH's own logger. The legacy `OV_DEBUG_LOG=/tmp/ov-dsh.log` alias is still supported and takes precedence over both canonical settings.

## DeepSeek Harness Desktop {#deepseek-harness-desktop}

The bundle also runs inside the DeepSeek Harness Desktop app on macOS and Windows; Desktop is not released for Linux. Desktop keeps its plugins in its own `desktop` profile at `~/.dsh/profiles/desktop`, separate from the CLI's `web` profile, so a bundle installed for `dsh web` does not show up in Desktop or the other way round. Both read the same `~/.openviking/ovcli.conf`.

### Install with the installer (macOS) {#desktop-installer}

On macOS the installer detects Desktop and offers it as a harness of its own, **DeepSeek Harness Desktop** (`dsh-desktop`), selected by default and installable alongside the `dsh` CLI. Desktop's profile should only be changed while the app is not running:

1. Open Desktop once so it creates its profile, then quit it with ⌘Q. Closing the window only hides it; the app keeps running.
2. Run the installer. `bash -s -- --harness dsh-desktop` in place of `bash` installs Desktop alone; `--dsh-profile desktop` (or `OPENVIKING_DSH_PROFILE=desktop`) selects the same harness.
3. Reopen Desktop and start a new conversation.

If Desktop is running, the installer asks you to quit it (with `--yes` it warns and continues). If it cannot find Desktop's `dsh` command, the profile does not exist yet, or Desktop is still running after the prompt, it prints the Plugins page steps instead of failing. The installer itself needs Node.js 18 or later, which Desktop does not provide; on a Mac without Node.js it prints the same steps and stops.

The installer uses Desktop's own `dsh` command, not the one from npm, which refuses the `desktop` profile. It looks for, in order: `OPENVIKING_DSH_DESKTOP_CLI`; `/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh`; the same path under `~/Applications`; and a `dsh` on PATH that resolves inside `DeepSeek Harness.app`, which is what the app menu's **Manage dsh Command…** installs.

### Manual setup {#desktop-plugins-page}

Use this on Windows, where the installer does not run, or on macOS if you would rather not run the installer. The Plugins page and Desktop's `dsh` command only add the bundle; neither asks for your OpenViking credentials, so set those up first.

1. **Configure the connection.** Write `~/.openviking/ovcli.conf` (`%USERPROFILE%\.openviking\ovcli.conf` on Windows):

   ```json
   { "url": "https://<your-openviking-server>", "api_key": "<your-api-key>" }
   ```

   Add `account` and `user` for a trusted-mode server, or skip this step for a local server at `http://127.0.0.1:1933` without authentication. See [Deployment Guide → CLI](../guides/03-deployment.md#cli).

2. **Add the bundle**, either way:
   - **Plugins page** (macOS and Windows): in Desktop, open **Plugins** → **Add plugin**, enter `@openviking/dsh-memory-plugin`, and click **Install**. The **Install source** menu picks the npm registry; a mirror may take a while to pick up a new release. If the plugin's switch is off afterwards, turn it on.
   - **Desktop's `dsh` command** (macOS): quit Desktop with ⌘Q, then run:

     ```bash
     DSH="/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh"
     "$DSH" plugin --profile desktop add @openviking/dsh-memory-plugin
     ```

     On Windows the same command is `resources\runtime\cli\bin\dsh.cmd` in Desktop's install directory.

3. Open a new conversation. After the `dsh` command, reopen Desktop first.

### Verify in Desktop {#verify-in-desktop}

No `dsh` can start the `desktop` profile, so `--dump-config` does not work for it. Check the install with `"$DSH" plugin --profile desktop ls`, or look for `@openviking/dsh-memory-plugin` under `dsh.profile.bundles` in `~/.dsh/profiles/desktop/package.json`. Then open a new conversation in Desktop and check for the context injection and the `mcp__openviking__*` tools, as in [Verify](#verify).

### Configure Desktop {#configure-desktop}

- Prefer `~/.openviking/ovcli.conf` for the connection and for behavior knobs (under `plugin.dsh`). The patch block from **Configuration** below goes in `~/.dsh/profiles/desktop/cordis.patch.yml`, but when Desktop fails to start, its recovery dialog (**Disable third-party plugins, back up profile patch, and restart**) moves that file aside to `cordis.patch.yml.bak-<timestamp>`.
- Desktop does not see variables exported in a terminal. On macOS it reads your login shell's environment (`~/.zprofile`, `~/.zshrc`) once when the app starts, so quit and reopen it after changing an `OPENVIKING_*` variable; `DSH_*` variables are not taken from the shell. On Windows it reads your user environment variables: set them with `setx`, then quit and reopen Desktop.
- Desktop does not show DSH's log output, which is where automatic memory callbacks log. To debug the MCP proxy, set `OPENVIKING_DEBUG=1` and `OPENVIKING_DEBUG_LOG=<file>` as the previous item describes, then read that file.
- Desktop starts the MCP proxy from its profile directory, so the proxy has no workspace peer; automatic recall and capture still use each conversation's workspace. With `OPENVIKING_RECALL_PEER_SCOPE=actor`, set `actor_peer_id` in `ovcli.conf` or `OPENVIKING_PEER_ID` to scope the `mcp__openviking__*` tools, which otherwise search every peer. An explicit peer also replaces each conversation's workspace peer for automatic recall and capture, so every Desktop conversation shares it.

To remove the bundle, use **Uninstall** on its Plugins page entry, or quit Desktop and run `"$DSH" plugin --profile desktop rm @openviking/dsh-memory-plugin`.

## How it works

The bundle runs inside DSH as a Cordis plugin rather than as external hooks, so it follows the session in-process. At session start it injects your OpenViking profile block, an index of available memories, and an `<available-skills>` catalog of your OpenViking skills. When a model step takes new user input, it searches OpenViking with the text the user typed and appends what it finds to that step as a durable message, so the injection replays with the session and is visible to compaction. Context that DSH or other plugins inject (for example `time-context` or job notices) and tool results neither trigger recall nor enter the query. It captures user, assistant, and (optionally) tool-result messages straight from DSH's event stream, skipping injected context, and commits to OpenViking once pending tokens cross the threshold or when DSH begins a compaction, archiving every captured message. Writes that fail land in a pending queue and replay at the next session start.

Each DSH session maps to `dsh-<session-id>` in OpenViking, and every subagent gets its own session.

The model-facing surface is the OpenViking MCP tool set, reached through the same stdio proxy the other memory integrations use and published under an `mcp__openviking__` prefix. Because that proxy runs once per profile, `mcp__openviking__remember` stores into a short-lived server-side session rather than the current one—automatic capture still records the conversation itself. With `OPENVIKING_RECALL_PEER_SCOPE=actor`, a fixed `OPENVIKING_PEER_ID` attributes that profile’s tool calls to one peer; the default `all` scope omits the actor-peer header. Use separate profiles/processes when workspaces need different tool identities. The bundle also ships three shared skills: `openviking-memory`, so the model knows when to search, read, and write; `openviking-skills`, which covers finding, using, creating, sharing, and migrating skills stored in OpenViking; and `ov-experience-memory`, which retrieves and applies prior task Experience before executable work. Tool results are captured by default (`captureToolResults: true`), which lets the server link the skill's reads back to the Experience they used; with `false` the skill only retrieves and applies Experience.

A filesystem tool call whose path is a `viking://` URI is blocked with a hint pointing at the right OpenViking tool. For a write or edit under a skill directory such as `viking://~/skills/<name>/`, that tool is `mcp__openviking__add_skill`, which creates or replaces a whole skill from its `SKILL.md` text. A shell command that carries a `viking://` URI still runs, and the model gets a notice suggesting the OpenViking tools, which it can ignore when the URI is intentional data.

<details>
<summary><b>Configuration</b></summary>

Credentials resolve from `OPENVIKING_*` environment variables, then `~/.openviking/ovcli.conf`, then `~/.openviking/ov.conf` — the same chain the Claude Code, Codex, OpenCode, and pi integrations use. The bundle reloads them when those files change.

| Env Var | Default | Description |
|---------|---------|-------------|
| `OPENVIKING_URL` / `OPENVIKING_BASE_URL` | `http://127.0.0.1:1933` | Server endpoint |
| `OPENVIKING_API_KEY` / `OPENVIKING_BEARER_TOKEN` | — | API key (sent as `Authorization: Bearer`) |
| `OPENVIKING_ACCOUNT` / `OPENVIKING_USER` | — | Trusted-mode account and user |
| `OPENVIKING_PEER_ID` | — | Explicit actor peer |
| `OPENVIKING_WORKSPACE_PEER` | `true` | Derive a peer from each session's workspace; `0` sends no peer |
| `OPENVIKING_RECALL_PEER_SCOPE` | `all` | `actor` isolates recall to the current workspace |
| `OPENVIKING_DEBUG` | `false` | Enable MCP proxy debug logging; also requires a log path |
| `OPENVIKING_DEBUG_LOG` | `""` | File path for MCP proxy debug logs |
| `OV_DEBUG_LOG` | — | Legacy alias: enables MCP proxy logging and overrides the canonical debug flag and log path |

Behavior knobs live in the profile's Cordis patch entry:

```yaml
- id: openviking-memory-runtime
  config:
    recallTokenBudget: 2000
    scoreThreshold: 0.35
    captureToolResults: true
    commitTokenThreshold: 20000
```

`syncTurns: false` in that block disables automatic capture, commit, and replay of pending writes. Profile injection and recall continue; queued writes remain for a later writing session. This does not revoke the model’s MCP write tools or change server permissions.

`peerSource`, in that same `config` block, decides how the workspace peer is derived. The default `"git"` uses the repository's normalized `origin` URL (`git@github.com:volcengine/OpenViking.git` becomes `github.com-volcengine-openviking`), falling back to the repository root path, so every clone, worktree, and subdirectory of one repository shares a single peer; outside a repository no peer is sent at all, and what is remembered there goes to your user-level space at `viking://user/<you>/memories`. `"cwd"` restores the earlier behavior — the working directory with every non-alphanumeric character replaced by `-` — and `"none"` sends no peer at all. To give a directory outside a repository its own memory, set `OPENVIKING_PEER_ID` for it ([Give a Directory Its Own Peer](../configuration/02-client.md#give-a-directory-its-own-peer)).

`skillCatalog` and `skillCatalogTokenBudget`, in that same block, control the skill catalog injected at session start. It lists your own skills before those shared with your account under `viking://agent/skills`, each description cut to about 40 tokens, within its own budget (default `1200` tokens, separate from the profile budget); when not every description fits, it lists names only. `skillCatalog: false` or a budget of `0` turns it off; the environment spellings are `OPENVIKING_SKILL_CATALOG` and `OPENVIKING_SKILL_CATALOG_TOKEN_BUDGET`.

Credentials given in the patch win over the environment. Behavior knobs resolve highest priority first: `OPENVIKING_*` environment variables, the workspace's `.openviking/config.json` and `config.local.json`, `ovcli.conf`'s `plugin.dsh`, `ovcli.conf`'s `plugin`, then this patch block. The full list is documented in the [bundle README](https://github.com/volcengine/OpenViking/tree/main/examples/dsh-memory-plugin).

</details>

## Troubleshooting

| Issue | What to check |
|-------|---------------|
| Nothing injected, no OpenViking tools | `dsh --profile web --dump-config` should list `openviking-memory-runtime`; re-run the installer or `dsh plugin --profile web add …`. For Desktop, see [Verify in Desktop](#verify-in-desktop) |
| Installed into the wrong profile | The installer defaults to `web`; re-run it with `--dsh-profile <name>` |
| DSH reports the bundle is incompatible with this dsh version | The bundle accepts every `@deepseek-ai/dsh` 0.x release from `0.1.0-rc.6` on (peer range `>=0.1.0-rc.6 <1.0.0-0`), so this means DSH 1.0 or later; update the bundle, or accept the risk with `dsh plugin allow-version`. Keep all `@deepseek-ai/dsh-*` host packages on the same version. |
| Bundle fails to start on a new DSH release | Releases verified so far: `0.1.0-rc.6`, `0.1.5-rc.1`, `0.1.5-rc.2`, `0.1.7-rc.2`, `0.2.0-rc.2`, `0.2.1-alpha.1`, `0.2.1-rc.2`, `0.2.1-rc.3`. Newer 0.x releases are admitted without prior verification; pin DSH to a verified release and open an issue. |
| Install says the package is "not in the npm registry" | The profile may have a pnpm minimum release age of 24 hours; check its configuration (`minimumReleaseAge`). Wait it out, or add the exact version to `minimumReleaseAgeExclude` in the profile's `pnpm-workspace.yaml` |
| Recall is empty | `curl "<your OpenViking URL>/health"`; check the endpoint and that the prompt is longer than the minimum query length (3 characters) |
| 401 / 403 from OpenViking | Verify `OPENVIKING_API_KEY`; for trusted-mode deployments also verify `OPENVIKING_ACCOUNT` and `OPENVIKING_USER` |
| Memories from other projects leak in | Set `OPENVIKING_RECALL_PEER_SCOPE=actor` to limit peer memories to the active peer; user-level memories remain shared |
| Nothing committed after a crash | Commit runs on a token threshold, at compaction start, and at teardown; queued writes replay at the next session start |
| `profile "desktop" is managed exclusively by the Electron application` | The `desktop` profile cannot be started or dumped, and only Desktop's own `dsh` can change its plugins. Use [Desktop's `dsh` command](#desktop-plugins-page) and [verify without `--dump-config`](#verify-in-desktop) |
| `Open DeepSeek Harness Desktop once to initialize its profile…` | Desktop has not created its profile yet. Open it once, quit it with ⌘Q, then re-run the installer |
| Installed for Desktop but nothing appears | Quit Desktop with ⌘Q (closing the window only hides it) and reopen it; in **Plugins**, check that the bundle is enabled |
| Gone from Desktop after its recovery dialog | Recovery disables third-party plugins and backs up `cordis.patch.yml`; enable the bundle again in **Plugins**, and move settings you need from the `.bak-<timestamp>` file into `ovcli.conf` |
| Skills missing in **Minimal mode** | The Web and Desktop **Minimal mode** agent preset has no skill tool, so the bundled skills are unavailable there; automatic recall and capture still work |

## See also

- [Capability Reference](./16-capability-reference.md)
