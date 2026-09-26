# OpenViking OpenCode Plugin

A unified OpenCode plugin for OpenViking repository retrieval and long-term memory.

> **Requires an OpenViking server with `viking://~` home-alias support.** Recall targets the
> caller's own context space through `viking://~/memories` and `viking://~/skills`; the uid-less
> `viking://user/memories` shorthand is rejected by newer servers.

This is the only OpenCode plugin example maintained in this repository. It supersedes the former split examples for indexed repository prompt injection and long-term memory.

The plugin uses OpenCode hooks for lifecycle behavior and registers OpenViking's standard stdio MCP proxy for model tools. It does not install or require an OpenCode skill, and agents do not need to run `ov` shell commands.

## What It Does

- Injects indexed `viking://resources/` repositories into the system prompt.
- On the first message of each session, injects your profile, memory indexes, and an `<available-skills>` catalog of your own and account-shared OpenViking skills.
- Exposes the same OpenViking MCP tools used by the Claude Code and Codex memory plugins.
- Maps each OpenCode session to an OpenViking session.
- Captures user and assistant text messages into OpenViking.
- On OpenCode v2, reads the structured session context at execution boundaries so tool calls and results are captured too.
- Commits sessions at lifecycle boundaries for memory extraction.
- Automatically recalls relevant memories and injects them as hidden synthetic context for the current user message.
- Blocks accidental local filesystem reads of `viking://` URIs and points the agent back to `openviking_read`, `openviking_glob`, or `openviking_search`. Shell commands that carry a `viking://` URI still run, with a notice appended to their output.

## Files

```text
examples/opencode-plugin/
├── index.mjs
├── package.json
├── README.md
├── INSTALL-ZH.md
├── lib/
│   ├── config.mjs
│   ├── mcp-config.mjs
│   ├── runtime.mjs
│   ├── repo-context.mjs
│   ├── memory-session.mjs
│   ├── memory-recall.mjs
│   ├── session-inject.mjs
│   ├── viking-uri-guard.mjs
│   └── utils.mjs
├── servers/
│   └── mcp-proxy.mjs
├── tests/
└── wrappers/
    └── openviking.js
```

There is intentionally no `skills/openviking/SKILL.md`. The tool surface comes from OpenViking's MCP endpoint.

## Requirements

- OpenCode 1.15.7+ or OpenCode 2.0.15+
- OpenViking HTTP server
- Node.js 18+
- An OpenViking API key if your server requires authentication

The same package serves both plugin APIs. OpenCode 1 calls `server()` (or the named `OpenVikingPlugin` export), while OpenCode 2 calls `setup()`. The v2 entrypoint does not import `@opencode/plugin`, so a source install on OpenCode 1 still loads. OpenCode 2 normalizes the installer's `"plugin"` key to `"plugins"`; keep `"plugin"` in shared v1/v2 configuration. On v2, the bundled MCP server uses `codemode: false`, preserving the documented `openviking_*` tool names.

Start OpenViking first:

```bash
openviking-server --config ~/.openviking/ov.conf
```

## Installation

### Published Package

Normal users should enable it through OpenCode's package plugin mechanism:

The published npm package is `@openviking/opencode-plugin`; verify availability with:

```bash
npm view @openviking/opencode-plugin version
```

```json
{
  "plugin": ["@openviking/opencode-plugin"]
}
```

### Source Install

For development or PR testing, copy the package into OpenCode's plugin directory with a top-level wrapper:

```bash
node examples/memory-plugin-shared/sync.mjs
mkdir -p ~/.config/opencode/plugins/openviking
cp examples/opencode-plugin/wrappers/openviking.js ~/.config/opencode/plugins/openviking.js
cp examples/opencode-plugin/index.mjs examples/opencode-plugin/package.json ~/.config/opencode/plugins/openviking/
cp -r examples/opencode-plugin/lib ~/.config/opencode/plugins/openviking/
cp -r examples/opencode-plugin/servers ~/.config/opencode/plugins/openviking/
```

`sync.mjs` generates `lib/shared/`, the shared modules the plugin and its MCP proxy import. That directory is not in git, so run it before copying, and again after every `git pull`.

This creates a stable OpenCode plugin layout:

```text
~/.config/opencode/plugins/
├── openviking.js
└── openviking/
    ├── index.mjs
    ├── package.json
    ├── lib/
    └── servers/
```

The top-level `openviking.js` is only a wrapper:

```js
export { OpenVikingPlugin, default } from "./openviking/index.mjs"
```

This wrapper is only for source installs with the directory layout shown above. npm package installs load `index.mjs` directly through `package.json`.
Use the `.js` wrapper for source installs; OpenCode's local plugin scanner discovers JavaScript/TypeScript plugin files.

## Configuration

Behaviour knobs live in `~/.openviking/ovcli.conf` beside the connection fields, in the shared `plugin` section or in the `plugin.opencode` override:

```json
{
  "url": "http://127.0.0.1:1933",
  "api_key": "your-api-key-here",
  "plugin": {
    "recallLimit": 6,
    "opencode": {
      "enabled": true,
      "mcpEnabled": true,
      "timeoutMs": 30000,
      "repoContext": true,
      "repoContextCacheTtlMs": 60000,
      "autoRecall": true,
      "scoreThreshold": 0.35,
      "recallMaxContentChars": 500,
      "recallPreferAbstract": true,
      "recallTokenBudget": 2000,
      "minQueryLength": 3,
      "commitTokenThreshold": 20000,
      "commitKeepRecentCount": 10,
      "profileTokenBudget": 10000,
      "skillCatalog": true,
      "skillCatalogTokenBudget": 1200,
      "resumeContextBudget": 32000
    }
  }
}
```

Keys in `plugin` apply to every harness; keys in `plugin.opencode` apply to this one and override them. Resolution is `OPENVIKING_*` environment variables → the workspace's `.openviking/config.json`, `.openviking/config.local.json` and machine registry entry → `plugin.opencode` → `plugin` → built-in defaults. Every knob, with its type, default, range, environment variable and accepted older spellings, is declared in [`examples/memory-plugin-shared/lib/config-schema.mjs`](../memory-plugin-shared/lib/config-schema.mjs).

`recallLimit` is a legacy quota-scaling input, not a final result cap.
Explicit values from 1 through 5 produce an effective total quota of 6 because
each coding category keeps one retrieval slot. Use Context `quotas` directly
when exact category ceilings are required.

`profileTokenBudget` covers the profile and memory indexes in the session-start block. The `<available-skills>` catalog comes from one `GET /api/v1/skills` and has its own budget, `skillCatalogTokenBudget` (default `1200`, `OPENVIKING_SKILL_CATALOG_TOKEN_BUDGET`). Your own skills are listed first, then the ones shared under `viking://agent/skills`, leaving out a shared skill that has the same name as one of yours; each description is cut to about 40 tokens. When the descriptions do not fit, the catalog lists names only (with a `... +N more` tail if even the names do not all fit), and when not even one name fits, a one-line count. The agent reads a skill's `SKILL.md` with `openviking_read` before following it. `skillCatalog: false` (`OPENVIKING_SKILL_CATALOG=0`) or a budget of `0` turns the catalog off; with no skills, or on a server without `GET /api/v1/skills`, it is left out.

API keys are resolved from environment variables or `~/.openviking/ovcli.conf` and sent as `Authorization: Bearer ...` by both hooks and the MCP proxy. Recall goes through the server-side context face (`POST /api/v1/search/search` with `mode="context"`), falling back to the deprecated `/api/v1/search/recall` on older deployments. `account` and `user` are trusted-mode identity
headers sent as `X-OpenViking-Account` and `X-OpenViking-User`; an `api_key`
server reads both out of the key, so the plugin withholds them there.
By default the plugin derives a peer from the git identity of the project directory: the normalized `origin` URL, else the repository root path. Outside a git repository no peer is sent at all, and what is remembered there goes to the user-level space `viking://user/<you>/memories`. `git@github.com:volcengine/OpenViking.git` becomes `github.com-volcengine-openviking`; the path fallback keeps the older naming rule where every non-letter-or-digit character becomes `-`, so `/Users/x/Dev/OpenViking` becomes `-Users-x-Dev-OpenViking`. One repository therefore keeps one peer across subdirectories, worktrees, clones and machines, while a fork's different origin keeps it separate. Derivation reads `.git` directly, so no `git` binary is needed. The plugin reads the workspace's `.openviking/config.json`, so a `peer.id` or `peer.source` written there applies. Data-plane memory/resource requests send the effective peer as `X-OpenViking-Actor-Peer`; captured session messages store it as body `peer_id`. Configure `peerId` in the `plugin` section or `OPENVIKING_PEER_ID` to override the derived peer, or set `workspacePeer` to `false` / `OPENVIKING_WORKSPACE_PEER=0` to send no peer at all. Memories written under the older directory-derived peer stay reachable: the default broad recall sweeps every peer under the user, and `recallPeerScope="actor"` asks that previous peer separately.

Recall defaults to the broad mode: global memory, the current workspace, and
other workspace memories can all be recalled, with other workspaces penalized
and rendered later. Set `recallPeerScope="actor"` or
`OPENVIKING_RECALL_PEER_SCOPE=actor` for the isolation mode, which only sees
global memory plus the current workspace. In deployments where one bot serves
multiple real people, such as zouk, vikingbot, or AstrBot, use the isolation mode
with an explicit actor peer so one person's memories are not recalled into
another person's session.

`OPENVIKING_API_KEY`, `OPENVIKING_ACCOUNT`, `OPENVIKING_USER`,
and `OPENVIKING_PEER_ID` take precedence over `ovcli.conf`. Below the
environment, the `plugin` section's `peerId` takes precedence over
`ovcli.conf`'s `actor_peer_id`: a peer written for this harness is the more
specific answer, and this is the order every memory plugin follows.

`OPENVIKING_CLI_CONFIG_FILE` points the plugin at an `ovcli.conf` somewhere other than `~/.openviking/ovcli.conf`.

### Hook-only mode

When OpenViking is already exposed through another MCP server, retain the lifecycle hooks while
skipping this plugin's bundled MCP registration:

```json
{
  "plugin": {
    "opencode": { "mcpEnabled": false }
  }
}
```

This leaves repository context, automatic recall, message capture, and lifecycle commits enabled.
It does not add or overwrite OpenCode's `mcp.openviking` entry.

OpenCode's local `read`, `glob`, and `grep` tools cannot read `viking://` URIs.
When the agent accidentally tries that, the plugin blocks the filesystem tool
call and points it to the OpenViking MCP tools. A `bash` command that contains a
`viking://` URI is not blocked, because the URI is often data (an `ov` argument,
an HTTP payload); the command runs and the plugin appends a notice naming the
MCP tools to its output.

OpenCode v2 has no plugin toast API. Service availability and other non-fatal notices are written to `openviking-memory.log`. The plugin captures the transcript before compaction discards it, checks the commit threshold after every execution (succeeded, failed, or interrupted), and forces a commit after completed compaction, session deletion, and cleanup. Each plugin instance handles only the sessions of its own location, and keeps its capture cursor in OpenCode plugin storage so a reloaded instance does not resend earlier turns.

## MCP Tools

OpenCode sees the OpenViking MCP server as `openviking`, so tool names are namespaced with `openviking_`.

- `openviking_search`: deep semantic retrieval across memories, resources, and skills; use `mode="context"` for balanced, injection-ready context.
- `openviking_find`: fast semantic retrieval.
- `openviking_remember`: store important facts or decisions for memory extraction.
- `openviking_read`: read one or more `viking://` files.
- `openviking_list`: list a `viking://` directory.
- `openviking_tree`: show a `viking://` directory tree.
- `openviking_grep`: exact text or regex search.
- `openviking_glob`: glob file matching.
- `openviking_write`: create, overwrite, or append to a `viking://` file.
- `openviking_edit`: exact string replacement in a `viking://` file.
- `openviking_add_resource`: add a URL, local file, sitemap, or feed.
- `openviking_add_skill`: create or replace a skill from its full `SKILL.md` text (`data`), or install one from a Git URL or a local `SKILL.md`, skill directory, or `.zip` (`path`); `target_uri="viking://agent/skills"` shares it with the account.
- `openviking_forget`: delete a `viking://` URI after explicit user confirmation.
- `openviking_list_watches` / `openviking_cancel_watch`: inspect or cancel resource watches.
- `openviking_health`: check OpenViking server health.

The proxy forwards the server's real `tools/list` response; the plugin does not maintain a separate native tool list.

## Runtime Files

The plugin writes runtime files to `~/.config/opencode/openviking/` by default:

- `openviking-memory.log`
- `openviking-session-state.json`

Set `dataDir` in `plugin.opencode` to override this directory.

## Automated npm releases

Changes merged into upstream `main` under `examples/opencode-plugin/` trigger
`.github/workflows/plugin-npm-release.yml`. Releases use the same calendar
version convention as the OpenClaw plugin: `YYYY.M.D`, then `YYYY.M.D-N` for
subsequent releases on that date (Asia/Shanghai). No manual source version bump
is required. The workflow changes the version only in the package being published;
it does not commit generated version changes back to the repository.

Releases are serialized. A queued run checks out current `main`, so several quick
merges may be included in one package. The npm manifest records
`openvikingSourceCommit`; rerunning a successfully published commit skips publication.
Registry failures stop the workflow instead of treating an unavailable registry as
an unused version. Only upstream `main` can publish the official npm `latest` tag.
Manual dispatch on `main` retries a failed release using the existing npm credentials
or Trusted Publishing configuration.
