# OpenViking OpenCode Plugin

A unified OpenCode plugin for OpenViking repository retrieval and long-term memory.

This PR adds a unified plugin package alongside the older split examples. The older examples remain available for now and will be deprecated in a future update:

- `examples/opencode`: indexed repository prompt injection and CLI-oriented guidance
- `examples/opencode-memory-plugin`: long-term memory, session sync, commit, and recall

The new plugin exposes everything through OpenCode tool hooks and talks to OpenViking through HTTP APIs. It does not install or require an OpenCode skill, and agents do not need to run `ov` shell commands.

## What It Does

- Injects indexed `viking://resources/` repositories into the system prompt.
- Exposes repository search, grep, glob, read, browse, add, remove, and queue status as tools.
- Maps each OpenCode session to an OpenViking session.
- Captures user and assistant text messages into OpenViking.
- Runs automatic background session commits for memory extraction.
- Automatically recalls relevant memories and appends them to the latest user message.

## Files

```text
examples/opencode-plugin/
├── index.mjs
├── package.json
├── README.md
├── INSTALL-ZH.md
├── lib/
│   ├── runtime.mjs
│   ├── repo-context.mjs
│   ├── memory-session.mjs
│   ├── memadd-local.mjs
│   ├── memory-tools.mjs
│   ├── memory-recall.mjs
│   └── utils.mjs
└── wrappers/
    └── openviking.mjs
```

There is intentionally no `skills/openviking/SKILL.md`. The former skill behavior is implemented as tools.

## Requirements

- OpenCode
- OpenViking HTTP server
- Node.js / npm for installing the plugin dependency
- An OpenViking API key if your server requires authentication

Start OpenViking first:

```bash
openviking-server --config ~/.openviking/ov.conf
```

## Installation

### Published Package

Normal users should enable it through OpenCode's package plugin mechanism:

```json
{
  "plugin": ["openviking-opencode-plugin"]
}
```

Use the final published package name if it changes before release.

### Source Install

For development or PR testing, copy the package into OpenCode's plugin directory with a top-level wrapper:

```bash
mkdir -p ~/.config/opencode/plugins/openviking
cp examples/opencode-plugin/wrappers/openviking.mjs ~/.config/opencode/plugins/openviking.mjs
cp examples/opencode-plugin/index.mjs examples/opencode-plugin/package.json ~/.config/opencode/plugins/openviking/
cp -r examples/opencode-plugin/lib ~/.config/opencode/plugins/openviking/
cd ~/.config/opencode/plugins/openviking
npm install
```

This creates a stable OpenCode plugin layout:

```text
~/.config/opencode/plugins/
├── openviking.mjs
└── openviking/
    ├── index.mjs
    ├── package.json
    ├── lib/
    └── node_modules/
```

The top-level `openviking.mjs` is only a wrapper:

```js
export { OpenVikingPlugin, default } from "./openviking/index.mjs"
```

## Configuration

Create `~/.config/opencode/openviking-config.json`:

```json
{
  "endpoint": "http://localhost:1933",
  "apiKey": "",
  "account": "",
  "user": "",
  "agentId": "",
  "enabled": true,
  "timeoutMs": 30000,
  "runtime": { "autoStartServer": false },
  "repoContext": { "enabled": true, "cacheTtlMs": 60000 },
  "autoCommit": { "enabled": true, "intervalMinutes": 10 },
  "autoRecall": {
    "enabled": true,
    "limit": 6,
    "scoreThreshold": 0.15,
    "maxContentChars": 500,
    "preferAbstract": true,
    "tokenBudget": 2000
  }
}
```

`apiKey` is sent as `X-API-Key`. `account`, `user`, and `agentId` are sent as
`X-OpenViking-Account`, `X-OpenViking-User`, and `X-OpenViking-Agent`.
They are required by multi-tenant OpenViking servers for tenant-scoped APIs.

`OPENVIKING_API_KEY`, `OPENVIKING_ACCOUNT`, `OPENVIKING_USER`, and
`OPENVIKING_AGENT_ID` take precedence over values in this file.

For advanced setups, `OPENVIKING_PLUGIN_CONFIG` can point to another config file path.

## Tools

### `memsearch`

Semantic search across memories, resources, and skills.

Use for conceptual questions, repository internals, user preferences, and context-aware retrieval. Use `target_uri` to narrow scope, for example `viking://resources/fastapi/`.

### `memread`

Read a specific `viking://` URI using `abstract`, `overview`, `read`, or `auto`.

Use after `memsearch`, `memgrep`, `memglob`, or `membrowse` returns a URI.

### `membrowse`

Browse OpenViking filesystem structure with `list`, `tree`, or `stat`.

Use to discover exact URIs before reading content.

### `memcommit`

Commit the current OpenCode session to OpenViking and trigger memory extraction.

The plugin also runs automatic commits on a configurable interval.

### `memgrep`

Pattern search through OpenViking content.

Use for exact symbols, class names, function names, error strings, or known keywords.

### `memglob`

Glob file matching through OpenViking content.

Use to enumerate files such as `**/*.py`, `**/test_*.ts`, or `**/*.md`.

### `memadd`

Add a remote URL or local file resource to OpenViking.

Remote `http(s)` URLs go directly through `POST /api/v1/resources`.
Local files use the safer two-step server flow: upload the file to
`POST /api/v1/resources/temp_upload`, then add it through
`POST /api/v1/resources` with the returned `temp_file_id`.

Local paths may be absolute, relative to the OpenCode project directory, or
`file://` URLs. Local directory upload is not supported yet.

Examples:

```text
memadd path="https://example.com/spec.md" to="viking://resources/spec"
memadd path="./docs/notes.md" parent="viking://resources/"
memadd path="file:///home/alice/project/notes.md" reason="project notes"
```

After adding a resource, the tool also returns `GET /api/v1/observer/queue` status.

### `memremove`

Remove a `viking://` URI through `DELETE /api/v1/fs`.

This tool requires `confirm: true`. The user must explicitly confirm deletion before the agent calls it.

### `memqueue`

Return OpenViking observer queue status for embedding and semantic processing.

## Runtime Files

The plugin writes runtime files to `~/.config/opencode/openviking/` by default:

- `openviking-memory.log`
- `openviking-session-map.json`
- `openviking-server.log` when `runtime.autoStartServer` is enabled

Set `runtime.dataDir` in config to override this directory.
