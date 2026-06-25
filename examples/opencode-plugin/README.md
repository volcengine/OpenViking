# OpenViking OpenCode Plugin

A unified OpenCode plugin for OpenViking repository retrieval and long-term memory.

This is the only OpenCode plugin example maintained in this repository. It supersedes the former split examples for indexed repository prompt injection and long-term memory.

The new plugin exposes everything through OpenCode tool hooks and talks to OpenViking through HTTP APIs. It does not install or require an OpenCode skill, and agents do not need to run `ov` shell commands.

## What It Does

- Injects indexed `viking://resources/` repositories into the system prompt.
- Exposes repository search, grep, glob, read, browse, add, write, remove, and queue status as tools.
- Maps each OpenCode session to an OpenViking session.
- Captures user and assistant text messages into OpenViking.
- Commits sessions at lifecycle boundaries for memory extraction.
- Automatically recalls relevant memories and injects them as hidden synthetic context for the current user message.
- Blocks accidental local filesystem reads of `viking://` URIs and points the agent back to `memread`, `membrowse`, or `memsearch`.

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
│   ├── viking-uri-guard.mjs
│   └── utils.mjs
├── tests/
└── wrappers/
    └── openviking.js
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
cd ~/.config/opencode/plugins/openviking
npm install
```

This creates a stable OpenCode plugin layout:

```text
~/.config/opencode/plugins/
├── openviking.js
└── openviking/
    ├── index.mjs
    ├── package.json
    ├── lib/
    └── node_modules/
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
  "endpoint": "http://localhost:1933",
  "apiKey": "",
  "account": "",
  "user": "",
  "peerId": "",
  "enabled": true,
  "timeoutMs": 30000,
  "repoContext": { "enabled": true, "cacheTtlMs": 60000 },
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

`apiKey` is sent as `X-API-Key`. `account` and `user` are trusted-mode identity
headers sent as `X-OpenViking-Account` and `X-OpenViking-User`; leave them empty
when using API-key mode with user/admin API keys.
`peerId` is sent as `X-OpenViking-Actor-Peer` on data-plane memory/resource
requests; captured session messages store it as body `peer_id`. Configure
`peerId` explicitly when peer-scoped memory routing is needed.

`OPENVIKING_API_KEY`, `OPENVIKING_ACCOUNT`, `OPENVIKING_USER`,
and `OPENVIKING_PEER_ID` take
precedence over values in this file.

For advanced setups, `OPENVIKING_PLUGIN_CONFIG` can point to another config file path.

OpenCode's local `read`, `glob`, and `grep` tools cannot read `viking://` URIs.
When the agent accidentally tries that, the plugin blocks the filesystem tool
call and points it to `memread`, `membrowse`, or `memsearch`.

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

The plugin also commits at session deletion, session error, compaction, and plugin shutdown boundaries.

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

### `memwrite`

Write text content to a `viking://` file through `POST /api/v1/content/write`.

Use this for durable notes, small project memory files, or resource text that
should be updated directly. The default mode is `create` to avoid accidental
overwrites. Use `append` to extend an existing file and `replace` only when the
user explicitly wants to overwrite the file.

Examples:

```text
memwrite uri="viking://user/memories/project-notes.md" content="# Decision\n\nUse PostgreSQL." wait=true
memwrite uri="viking://resources/docs/api.md" content="\n\n## New endpoint" mode="append"
```

### `memremove`

Remove a `viking://` URI through `DELETE /api/v1/fs`.

This tool requires `confirm: true`. The user must explicitly confirm deletion before the agent calls it.

### `memqueue`

Return OpenViking observer queue status for embedding and semantic processing.

### `codesearch`

Search AST-supported symbol names across a confirmed ingested code repository or source subtree.

Use this after you have evidence that a `viking://` URI contains supported source files.

### `codeoutline`

Show symbol structure for a confirmed `viking://` source file.

Use this after `memglob`, `membrowse`, or `codesearch` finds an exact source file URI.

### `codeexpand`

Return the full source for one named symbol from a confirmed `viking://` source file.

Use this after `codeoutline` or other evidence shows the symbol exists in that file.

## Runtime Files

The plugin writes runtime files to `~/.config/opencode/openviking/` by default:

- `openviking-memory.log`
- `openviking-session-map.json`

Set `runtime.dataDir` in config to override this directory.
