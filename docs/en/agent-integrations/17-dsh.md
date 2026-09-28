# DeepSeek Harness Memory Bundle

Give [DeepSeek Harness](https://www.npmjs.com/package/@deepseek-ai/dsh) (`dsh`) cross-project and cross-session long-term memory. Once installed, every conversation automatically recalls relevant memories and captures new content, and the model gets the OpenViking tools and the `openviking-memory` and `openviking-skills` skills without any extra setup.

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

   The output should contain an `openviking-memory-runtime` entry.

> Don't have `ovcli.conf` yet? See the [Deployment Guide → CLI](../guides/03-deployment.md#cli).
>
> To remove it: `dsh plugin --profile web rm @openviking/dsh-memory-plugin`.

</details>

## Verify

Start `dsh --profile web` and open a conversation. You should see an OpenViking context injection at the top of the session, and the model should have `mcp__openviking__*` tools available. Ask it about something from an earlier session to confirm recall.

If nothing appears, set `OV_DEBUG_LOG=/tmp/ov-dsh.log` and check that file.

## How it works

The bundle runs inside DSH as a Cordis plugin rather than as external hooks, so it follows the session in-process. At session start it injects your OpenViking profile block, an index of available memories, and an `<available-skills>` catalog of your OpenViking skills. Before every model step it searches OpenViking with the current input and appends what it finds to that step as a durable message, so the injection replays with the session and is visible to compaction. It captures user, assistant, and (optionally) tool-result messages straight from DSH's event stream, and commits to OpenViking once pending tokens cross the threshold, keeping the ten most recent messages live. Writes that fail land in a pending queue and replay at the next session start.

Each DSH session maps to `dsh-<session-id>` in OpenViking, and every subagent gets its own session.

The model-facing surface is the OpenViking MCP tool set, reached through the same stdio proxy the other memory integrations use and published under an `mcp__openviking__` prefix. Because that proxy runs once per profile, `mcp__openviking__remember` stores into a short-lived server-side session rather than the current one—automatic capture still records the conversation itself—and tool calls carry the actor peer resolved at startup. Set `OPENVIKING_PEER_ID` when one process serves several workspaces and tool calls need exact attribution. The bundle also ships two shared skills: `openviking-memory`, so the model knows when to search, read, and write, and `openviking-skills`, which covers finding, using, creating, sharing, and migrating skills stored in OpenViking.

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
| `OV_DEBUG_LOG` | — | Write debug logs to this path |

Behavior knobs live in the profile's Cordis patch entry:

```yaml
- id: openviking-memory-runtime
  config:
    recallTokenBudget: 2000
    scoreThreshold: 0.35
    captureToolResults: false
    commitTokenThreshold: 20000
```

`syncTurns: false`, in that same block, makes the integration read-only: it still injects your profile and recalls memories, but sends nothing back — no captured turns, no commits, and no replay of writes an earlier session queued, which stay on the queue until a session that still writes drains them.

`peerSource`, in that same `config` block, decides how the workspace peer is derived. The default `"git"` uses the repository's normalized `origin` URL (`git@github.com:volcengine/OpenViking.git` becomes `github.com-volcengine-openviking`), falling back to the repository root path, so every clone, worktree, and subdirectory of one repository shares a single peer; outside a repository no peer is sent at all, and what is remembered there goes to your user-level space at `viking://user/<you>/memories`. `"cwd"` restores the earlier behavior — the working directory with every non-alphanumeric character replaced by `-` — and `"none"` sends no peer at all. To give a directory outside a repository its own memory, set `OPENVIKING_PEER_ID` for it ([Give a Directory Its Own Peer](../configuration/02-client.md#give-a-directory-its-own-peer)).

`skillCatalog` and `skillCatalogTokenBudget`, in that same block, control the skill catalog injected at session start. It lists your own skills before those shared with your account under `viking://agent/skills`, each description cut to about 40 tokens, within its own budget (default `1200` tokens, separate from the profile budget); when not every description fits, it lists names only. `skillCatalog: false` or a budget of `0` turns it off; the environment spellings are `OPENVIKING_SKILL_CATALOG` and `OPENVIKING_SKILL_CATALOG_TOKEN_BUDGET`.

Credentials given in the patch win over the environment. Behavior knobs resolve highest priority first: `OPENVIKING_*` environment variables, the workspace's `.openviking/config.json` and `config.local.json`, `ovcli.conf`'s `plugin.dsh`, `ovcli.conf`'s `plugin`, then this patch block. The full list is documented in the [bundle README](https://github.com/volcengine/OpenViking/tree/main/examples/dsh-memory-plugin).

</details>

## Troubleshooting

| Issue | What to check |
|-------|---------------|
| Nothing injected, no OpenViking tools | `dsh --profile web --dump-config` should list `openviking-memory-runtime`; re-run the installer or `dsh plugin --profile web add …` |
| Installed into the wrong profile | The installer defaults to `web`; re-run it with `--dsh-profile <name>` |
| `ERESOLVE` during install | The `@deepseek-ai/dsh-*` prerelease tags drift apart; install `@deepseek-ai/dsh@0.1.0-rc.6` exactly |
| Install says the package is "not in the npm registry" | pnpm refuses releases younger than 24 hours by default (`minimumReleaseAge`). Wait it out, or add the exact version to `minimumReleaseAgeExclude` in the profile's `pnpm-workspace.yaml` |
| Recall is empty | `curl http://localhost:1933/health`; check the endpoint and that the prompt is longer than the minimum query length (3 characters) |
| 401 / 403 from OpenViking | Verify `OPENVIKING_API_KEY`; for trusted-mode deployments also verify `OPENVIKING_ACCOUNT` and `OPENVIKING_USER` |
| Memories from other projects leak in | Set `OPENVIKING_RECALL_PEER_SCOPE=actor` |
| Nothing committed after a crash | Commit runs on a token threshold and at teardown; queued writes replay at the next session start |

## See also

- [Capability Reference](./16-capability-reference.md)
