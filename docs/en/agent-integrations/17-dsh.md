# DeepSeek Harness

Give [DeepSeek Harness](https://www.npmjs.com/package/@deepseek-ai/dsh) (`dsh`), and the DeepSeek Harness Desktop app, cross-project and cross-session long-term memory. The bundle runs inside DSH as a Cordis plugin: it recalls relevant memories for each prompt, captures each turn into an OpenViking session, and gives the model the OpenViking MCP tools and the bundled skills.

Source: [examples/dsh-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/dsh-memory-plugin)

## Install

Requirements: DSH 0.1.7-rc.2 or later.

> **Upgrading from DSH 0.1.5 or earlier:** current bundles require DSH 0.1.7-rc.2 or later; upgrade DSH.

For a managed connection, select **OpenViking Managed Service**, then:

- **Volcengine:** enter your service API key. The installer supplies the URL.
- **BytePlus:** enter your API key. See [BytePlus setup](../guides/27-byteplus-setup.md).

```bash
curl -fsSL https://openviking.ai/install | bash
# AI agents: not sure about running this? The script's header says what it does and how to verify it.
```

The installer puts the plugin into the `web` profile. To use another profile, pass `--dsh-profile <name>`: `curl -fsSL https://openviking.ai/install | bash -s -- --dsh-profile <name>`. Using the Desktop app? See [DeepSeek Harness Desktop](#deepseek-harness-desktop).

<details>
<summary><b>Manual setup</b></summary>

1. **Configure the connection** — write `~/.openviking/ovcli.conf` (`url`, `api_key`, optional `account`/`user`), or set `OPENVIKING_URL` and `OPENVIKING_API_KEY`. For a local server at `http://127.0.0.1:1933` without authentication, skip this.
2. **Add the bundle to a profile:** `dsh plugin --profile web add @openviking/dsh-memory-plugin`. Any profile name works except `desktop`, which belongs to the Desktop app.
3. **Check:** `dsh --profile web --dump-config` should contain an `openviking-memory-runtime` entry.

To remove it: `dsh plugin --profile web rm @openviking/dsh-memory-plugin`.

</details>

## Verify

Start `dsh --profile web` and open a conversation. You should see an OpenViking context injection at the top of the session, and the model should have `mcp__openviking__*` tools. Ask about something from an earlier session to confirm recall.

If something is wrong, ask the agent to run the `ov-memory-doctor` skill, or run `node ~/.dsh/profiles/web/node_modules/@openviking/dsh-memory-plugin/scripts/ov-memory-doctor.mjs` (replace `web` with your profile).

## DeepSeek Harness Desktop {#deepseek-harness-desktop}

The bundle also runs in the Desktop app on macOS and Windows. Desktop keeps its plugins in its own `desktop` profile at `~/.dsh/profiles/desktop`, separate from the CLI's `web` profile; both read the same `~/.openviking/ovcli.conf`.

### Install with the installer (macOS) {#desktop-installer}

1. Open Desktop once so it creates its profile, then quit it with ⌘Q (closing the window only hides it).
2. Run the installer and select **DeepSeek Harness Desktop** (`dsh-desktop`). `bash -s -- --harness dsh-desktop` in place of `bash` installs Desktop alone.
3. Reopen Desktop and start a new conversation.

The installer needs Node.js 18 or later, which Desktop does not provide; without it, or when it cannot find Desktop's `dsh` command, it prints the manual steps instead.

### Manual setup {#desktop-plugins-page}

1. **Configure the connection** in `~/.openviking/ovcli.conf` (`%USERPROFILE%\.openviking\ovcli.conf` on Windows): `{ "url": "https://<your-openviking-server>", "api_key": "<your-api-key>" }`.
2. **Add the bundle:** in Desktop, open **Plugins** → **Add plugin**, enter `@openviking/dsh-memory-plugin`, click **Install**, and make sure its switch is on. On macOS you can instead quit Desktop and run `"/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh" plugin --profile desktop add @openviking/dsh-memory-plugin`.
3. Open a new conversation.

### Verify in Desktop {#verify-in-desktop}

`--dump-config` does not work for the `desktop` profile. Look for `@openviking/dsh-memory-plugin` under `dsh.profile.bundles` in `~/.dsh/profiles/desktop/package.json`, then check a new conversation for the context injection and the `mcp__openviking__*` tools.

### Configure Desktop {#configure-desktop}

- Put settings in `~/.openviking/ovcli.conf` (behaviour keys under `plugin.dsh`). Desktop's recovery dialog moves `cordis.patch.yml` aside, so settings kept only there are lost after a recovery.
- Desktop does not see variables exported in a terminal. On macOS it reads your login shell's environment once at app start; on Windows it reads your user environment variables. Quit and reopen Desktop after changing an `OPENVIKING_*` variable.

## What is different on DSH

- **Versions:** DSH 0.1.7-rc.2 and later.
- **Cordis patch:** besides `ovcli.conf` (`plugin.dsh` overrides `plugin`), behaviour keys can go in the profile's `cordis.patch.yml` entry `openviking-memory-runtime` under `config`; `ovcli.conf` and environment variables take precedence over the patch.
- **Events:** recall at `agent/pre-step`, capture at `session/event`, commit at `compaction/start` and at `session/dispose`, which has a 3-second budget. When a compaction summarizes the session-start block away, the next step sends it again.
- **Subagents** are marked as child sessions; `skipSubagentSessions` skips them.
- **Tools** are published as `mcp__openviking__*` through the shared MCP proxy, which runs once per profile.
- **Client-side recall compression:** `recallCompress: "client"` (under `plugin.dsh`) digests recall with DSH's own configured model through one tool-free call instead of the server; `recallCompressModel` picks another model on the same provider and `recallCompressThinking` sets a reasoning effort. `auto` uses it when available and otherwise the server's rewrite. Compression is off by default.

```json
{"plugin":{"dsh":{"recallCompress":"client","recallCompressTimeoutMs":10000}}}
```

Recall, capture, commits, offline retries and the rest are shared with the other integrations: see the [Capability Reference](./16-capability-reference.md).

## See also

- [Capability Reference](./16-capability-reference.md)
- [Bundle README](https://github.com/volcengine/OpenViking/tree/main/examples/dsh-memory-plugin)
