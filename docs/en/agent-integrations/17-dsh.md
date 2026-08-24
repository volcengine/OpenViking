# DeepSeek Harness Memory Bundle

Give [DeepSeek Harness](https://www.npmjs.com/package/@deepseek-ai/dsh) (`dsh`) cross-project and cross-session long-term memory. Once installed, every conversation automatically recalls relevant memories and captures new content, and the model gets the OpenViking tools and the `openviking-memory` skill without any extra setup.

Source: [examples/dsh-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/dsh-memory-plugin)

## Install

DSH shares the installer with the other memory plugins. It asks for your language (English/中文), which harnesses to install, the download source, and your OpenViking credentials; every step is idempotent—re-running it is entirely safe.

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh)
```

In regions where GitHub is hard to reach, run the same installer from the Volcengine TOS mirror (or pick "TOS mirror" at the download-source prompt):

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh)
```

When DSH is selected, the installer asks which profile to install into and defaults to `web`. Pass `--dsh-profile <name>` to answer it up front.

After using it for a while, start a new conversation and ask about something you mentioned earlier—it will remember.

<details>
<summary><b>Manual setup</b></summary>

1. **Configure the connection** — write `~/.openviking/ovcli.conf` (`url`, `api_key`, optional `account`/`user`), or set `OPENVIKING_URL` and `OPENVIKING_API_KEY`. Using pure local mode (`http://127.0.0.1:1933`, no authentication)? Skip this—the bundle defaults to the local setup.

2. **Add the bundle to a profile**:

   ```bash
   dsh plugin --profile web add @openviking/dsh-memory-plugin
   ```

   `dsh plugin` forwards to pnpm inside the profile directory, so any profile name works; `web` is the one `dsh` creates for you on first use.

3. **Check that the profile picked it up**:

   ```bash
   dsh --profile web --dump-config
   ```

   The output should contain an `openviking-memory` plugin group.

> Don't have `ovcli.conf` yet? See the [Deployment Guide → CLI](../guides/03-deployment.md#cli).
>
> To remove it: `dsh plugin --profile web rm @openviking/dsh-memory-plugin`.

</details>

## Verify

Start `dsh --profile web` and open a conversation. You should see an OpenViking context injection at the top of the session, and the model should have `mcp__openviking__*` tools available. Ask it about something from an earlier session to confirm recall.

If nothing appears, set `OV_DEBUG_LOG=/tmp/ov-dsh.log` and check that file.

## How it works

The bundle runs inside DSH as a Cordis plugin rather than as external hooks, so it follows the session in-process. At session start it injects your OpenViking profile block and an index of available memories. Before every model step it searches OpenViking with the current input and appends what it finds to that step as a durable message, so the injection replays with the session and is visible to compaction. It captures user, assistant, and (optionally) tool-result messages straight from DSH's event stream, and commits to OpenViking once pending tokens cross the threshold, keeping the ten most recent messages live. Writes that fail land in a pending queue and replay at the next session start.

Each DSH session maps to `dsh-<session-id>` in OpenViking, and every subagent gets its own session.

The model-facing surface is the OpenViking MCP tool set, reached through the same stdio proxy the other memory integrations use and published under an `mcp__openviking__` prefix. Because that proxy runs once per profile, `mcp__openviking__remember` stores into a short-lived server-side session rather than the current one—automatic capture still records the conversation itself—and tool calls carry the actor peer resolved at startup. Set `OPENVIKING_PEER_ID` when one process serves several workspaces and tool calls need exact attribution. The bundle also ships the shared `openviking-memory` skill, so the model knows when to search, read, and write.

Accidental filesystem or shell calls on `viking://` URIs are blocked with a hint pointing at the right OpenViking tool.

<details>
<summary><b>Configuration</b></summary>

Credentials resolve from `OPENVIKING_*` environment variables, then `~/.openviking/ovcli.conf`, then `~/.openviking/ov.conf` — the same chain the Claude Code, Codex, OpenCode, and pi integrations use. The bundle reloads them when those files change.

| Env Var | Default | Description |
|---------|---------|-------------|
| `OPENVIKING_URL` / `OPENVIKING_BASE_URL` | `http://127.0.0.1:1933` | Server endpoint |
| `OPENVIKING_API_KEY` / `OPENVIKING_BEARER_TOKEN` | — | API key (sent as `Authorization: Bearer`) |
| `OPENVIKING_ACCOUNT` / `OPENVIKING_USER` | — | Trusted-mode account and user |
| `OPENVIKING_PEER_ID` | — | Explicit actor peer |
| `OPENVIKING_WORKSPACE_PEER` | `true` | Derive a peer from each session's workspace |
| `OPENVIKING_RECALL_PEER_SCOPE` | `all` | `actor` isolates recall to the current workspace |
| `OV_DEBUG_LOG` | — | Write debug logs to this path |

Behavior knobs live in the profile's Cordis patch entry:

```yaml
- insert:
    - id: openviking-memory
      name: '@deepseek-ai/cordis-plugin-group'
      group: true
      isolate:
        openvikingMemory: true
      config:
        - id: openviking-memory-runtime
          name: '@openviking/dsh-memory-plugin'
          config:
            recallTokenBudget: 2000
            scoreThreshold: 0.35
            captureToolResults: false
            commitTokenThreshold: 20000
```

Credentials given in the patch win over the environment; behavior toggles read the environment first. The full list is documented in the [bundle README](https://github.com/volcengine/OpenViking/tree/main/examples/dsh-memory-plugin).

</details>

## Troubleshooting

| Issue | What to check |
|-------|---------------|
| Nothing injected, no OpenViking tools | `dsh --profile web --dump-config` should list `openviking-memory`; re-run the installer or `dsh plugin --profile web add …` |
| Installed into the wrong profile | The installer defaults to `web`; re-run it with `--dsh-profile <name>` |
| `ERESOLVE` during install | The `@deepseek-ai/dsh-*` prerelease tags drift apart; install `@deepseek-ai/dsh@0.1.0-rc.6` exactly |
| Install says the package is "not in the npm registry" | pnpm refuses releases younger than 24 hours by default (`minimumReleaseAge`). Wait it out, or add the exact version to `minimumReleaseAgeExclude` in the profile's `pnpm-workspace.yaml` |
| Recall is empty | `curl http://localhost:1933/health`; check the endpoint and that the prompt is longer than the minimum query length (3 characters) |
| 401 / 403 from OpenViking | Verify `OPENVIKING_API_KEY`; for trusted-mode deployments also verify `OPENVIKING_ACCOUNT` and `OPENVIKING_USER` |
| Memories from other projects leak in | Set `OPENVIKING_RECALL_PEER_SCOPE=actor` |
| Nothing committed after a crash | Commit runs on a token threshold and at teardown; queued writes replay at the next session start |

## See also

- [Capability Reference](./16-capability-reference.md)
