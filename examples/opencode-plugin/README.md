# OpenViking OpenCode Plugin

A unified OpenCode plugin for OpenViking repository retrieval and long-term memory.

This is the only OpenCode plugin example maintained in this repository. It supersedes the former split examples for indexed repository prompt injection and long-term memory.

The plugin uses OpenCode hooks for lifecycle behavior and registers OpenViking's standard stdio MCP proxy for model tools. It does not install or require an OpenCode skill, and agents do not need to run `ov` shell commands.

## What It Does

- Injects indexed `viking://resources/` repositories into the system prompt.
- Exposes the same OpenViking MCP tools used by the Claude Code and Codex memory plugins.
- Maps each OpenCode session to an OpenViking session.
- Captures user and assistant text messages into OpenViking.
- Commits sessions at lifecycle boundaries for memory extraction.
- Automatically recalls relevant memories and injects them as hidden synthetic context for the current user message.
- Blocks accidental local filesystem reads of `viking://` URIs and points the agent back to `openviking_read`, `openviking_glob`, or `openviking_search`.

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

- OpenCode
- OpenViking HTTP server
- Node.js 18+
- An OpenViking API key if your server requires authentication

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
mkdir -p ~/.config/opencode/plugins/openviking
cp examples/opencode-plugin/wrappers/openviking.js ~/.config/opencode/plugins/openviking.js
cp examples/opencode-plugin/index.mjs examples/opencode-plugin/package.json ~/.config/opencode/plugins/openviking/
cp -r examples/opencode-plugin/lib ~/.config/opencode/plugins/openviking/
cp -r examples/opencode-plugin/servers ~/.config/opencode/plugins/openviking/
```

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

Create `~/.config/opencode/openviking-config.json`:

```json
{
  "enabled": true,
  "timeoutMs": 30000,
  "repoContext": { "enabled": true, "cacheTtlMs": 60000 },
  "autoRecall": {
    "enabled": true,
    "limit": 6,
    "scoreThreshold": 0.35,
    "maxContentChars": 500,
    "preferAbstract": true,
    "tokenBudget": 2000,
    "minQueryLength": 3
  },
  "commitTokenThreshold": 20000,
  "commitKeepRecentCount": 10,
  "profileTokenBudget": 10000,
  "resumeContextBudget": 32000
}
```

API keys are resolved from environment variables or `~/.openviking/ovcli.conf` and sent as `Authorization: Bearer ...` by both hooks and the MCP proxy. `account` and `user` are trusted-mode identity
headers sent as `X-OpenViking-Account` and `X-OpenViking-User`; leave them empty
when using API-key mode with user/admin API keys.
By default the plugin derives a peer from the project directory using Claude's
project-directory naming rule: every non-letter-or-digit character becomes `-`,
with no path normalization. For example, `/Users/x/Dev/OpenViking` becomes
`-Users-x-Dev-OpenViking`. Data-plane memory/resource requests send the
effective peer as `X-OpenViking-Actor-Peer`; captured session messages store it
as body `peer_id`. Configure `peerId` or `OPENVIKING_PEER_ID` to override the
workspace-derived peer, or set `workspacePeer=false` /
`OPENVIKING_WORKSPACE_PEER=0` to turn workspace-derived peers off.

Recall defaults to the broad mode: global memory, the current workspace, and
other workspace memories can all be recalled, with other workspaces penalized
and rendered later. Set `recallPeerScope="actor"` or
`OPENVIKING_RECALL_PEER_SCOPE=actor` for the isolation mode, which only sees
global memory plus the current workspace. In deployments where one bot serves
multiple real people, such as zouk, vikingbot, or AstrBot, use the isolation mode
with an explicit actor peer so one person's memories are not recalled into
another person's session.

`OPENVIKING_API_KEY`, `OPENVIKING_ACCOUNT`, `OPENVIKING_USER`,
and `OPENVIKING_PEER_ID` take precedence over values in this file.

For advanced setups, `OPENVIKING_PLUGIN_CONFIG` can point to another config file path.

OpenCode's local `read`, `glob`, and `grep` tools cannot read `viking://` URIs.
When the agent accidentally tries that, the plugin blocks the filesystem tool
call and points it to the OpenViking MCP tools.

## MCP Tools

OpenCode sees the OpenViking MCP server as `openviking`, so tool names are namespaced with `openviking_`.

- `openviking_recall`: balanced current-task recall using OpenViking's `/recall` endpoint.
- `openviking_search`: deep semantic retrieval across memories, resources, and skills.
- `openviking_find`: fast semantic retrieval.
- `openviking_remember`: store important facts or decisions for memory extraction.
- `openviking_read`: read one or more `viking://` files.
- `openviking_list`: list a `viking://` directory.
- `openviking_grep`: exact text or regex search.
- `openviking_glob`: glob file matching.
- `openviking_add_resource`: add a URL, local file, sitemap, or feed.
- `openviking_forget`: delete a `viking://` URI after explicit user confirmation.
- `openviking_list_watches` / `openviking_cancel_watch`: inspect or cancel resource watches.
- `openviking_code_search`, `openviking_code_outline`, `openviking_code_expand`: inspect indexed code symbols.
- `openviking_health`: check OpenViking server health.

The proxy forwards the server's real `tools/list` response; the plugin does not maintain a separate native tool list.

## Runtime Files

The plugin writes runtime files to `~/.config/opencode/openviking/` by default:

- `openviking-memory.log`
- `openviking-session-state.json`

Set `runtime.dataDir` in config to override this directory.
