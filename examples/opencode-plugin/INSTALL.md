# Install the Unified OpenViking OpenCode Plugin

This plugin adds one unified OpenViking plugin for OpenCode:

- OpenViking MCP tools for memory, resources, and code context
- Long-term memory, session synchronization, lifecycle commit, and automatic recall

This is the only OpenCode plugin example maintained in this repository. It does not install `skills/openviking/SKILL.md`, and it does not require the agent to use the `ov` command. Model tools are provided by the same stdio MCP proxy used by the Claude Code and Codex memory plugins.

## Prerequisites

Prepare the following first:

- OpenCode
- OpenViking HTTP Server
- Node.js 18+
- A valid OpenViking API key if authentication is enabled on the server

Start OpenViking first:

```bash
openviking-server --config ~/.openviking/ov.conf
```

Check the service:

```bash
curl http://localhost:1933/health
```

## Installation Method 1: Published Package

Normal users are recommended to enable it through OpenCode's package plugin mechanism:

```json
{
  "plugin": ["@openviking/opencode-plugin"]
}
```

## Installation Method 2: Source Install

Use this method for development, debugging, or PR testing. OpenCode's recommended plugin directory is:

```bash
~/.config/opencode/plugins
```

Run the following commands from the repository root:

```bash
mkdir -p ~/.config/opencode/plugins/openviking
cp examples/opencode-plugin/wrappers/openviking.js ~/.config/opencode/plugins/openviking.js
cp examples/opencode-plugin/index.mjs examples/opencode-plugin/package.json ~/.config/opencode/plugins/openviking/
cp -r examples/opencode-plugin/lib ~/.config/opencode/plugins/openviking/
cp -r examples/opencode-plugin/servers ~/.config/opencode/plugins/openviking/
```

After installation, the layout should look like this:

```text
~/.config/opencode/plugins/
├── openviking.js
└── openviking/
    ├── index.mjs
    ├── package.json
    ├── lib/
    └── servers/
```

The top-level `openviking.js` forwards the first-level `.js` entry that OpenCode can discover to the actual plugin directory:

```js
export { OpenVikingPlugin, default } from "./openviking/index.mjs"
```

This wrapper is only for source installs with the directory layout shown above. npm package installs load `index.mjs` directly through `package.json`.
Use the `.js` wrapper for source installs; OpenCode's local plugin scanner discovers JavaScript/TypeScript plugin files.

If you install through an npm package, you can also use `examples/opencode-plugin` as a normal OpenCode plugin package.

## Configuration

Create the user-level configuration file:

```bash
~/.config/opencode/openviking-config.json
```

Example configuration:

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

It is recommended to provide the API key through an environment variable instead of writing it into the configuration file:

```bash
export OPENVIKING_API_KEY="your-api-key-here"
```

API keys are resolved from environment variables or `~/.openviking/ovcli.conf` and sent as `Authorization: Bearer ...` by both hooks and the MCP proxy. `account` and `user` are trusted-mode identity headers sent as `X-OpenViking-Account` and `X-OpenViking-User`; leave them empty when using API-key mode with user/admin API keys. `peerId` is sent as `X-OpenViking-Actor-Peer` on data-plane memory/resource requests; captured session messages store it as body `peer_id`.

`OPENVIKING_API_KEY`, `OPENVIKING_ACCOUNT`, `OPENVIKING_USER`, and `OPENVIKING_PEER_ID` take precedence over the corresponding values in `openviking-config.json`.

For advanced setups, use `OPENVIKING_PLUGIN_CONFIG` to point to another configuration file path.

## Verify

Restart OpenCode after changing plugin or OpenViking configuration.

In a new OpenCode session, ask the agent to browse OpenViking memory or search for a known indexed resource. The plugin should expose the OpenViking MCP server, with tools namespaced by OpenCode as `openviking_*`:

- `openviking_recall`, `openviking_search`, `openviking_find`
- `openviking_read`, `openviking_list`, `openviking_grep`, `openviking_glob`
- `openviking_remember`, `openviking_add_resource`, `openviking_forget`, `openviking_health`
- `openviking_list_watches`, `openviking_cancel_watch`
- `openviking_code_search`, `openviking_code_outline`, `openviking_code_expand`

If anything looks wrong, check the runtime files:

```bash
ls ~/.config/opencode/openviking/
tail -n 100 ~/.config/opencode/openviking/openviking-memory.log
```

For a local server, also confirm OpenViking is reachable:

```bash
curl http://localhost:1933/health
```

## Available MCP Tools

The plugin registers OpenViking's stdio MCP proxy through OpenCode config. The server's real `tools/list` response is the source of truth; current OpenViking servers expose:

- `openviking_recall`: balanced current-task recall.
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

Usage guidance:

- Use `openviking_search` for conceptual questions.
- Use `openviking_grep` for exact symbols, function names, class names, or error strings.
- Use `openviking_glob` to enumerate files.
- Use `openviking_read` to read content.
- Use `openviking_list` to explore directory structure.
- Before deleting anything, obtain explicit user confirmation first; then call `openviking_forget`.
- If an agent tries to use OpenCode's local `read`, `glob`, or `grep` tools on a `viking://` URI, the plugin blocks that call and points it to the MCP tools.

## Local Files with `openviking_add_resource`

`openviking_add_resource` supports three input types:

- Remote `http(s)` URL: directly calls `/api/v1/resources`
- Local file path: first calls `/api/v1/resources/temp_upload`, then adds the resource using the returned `temp_file_id`
- `file://` URL: handled as a local file

Relative paths are resolved against the current OpenCode project directory. Examples:

```text
openviking_add_resource(path="https://example.com/spec.md", to="viking://resources/spec")
openviking_add_resource(path="./docs/notes.md", to="viking://resources/notes.md")
openviking_add_resource(path="file:///home/alice/project/notes.md", description="project notes")
```

Automatic zip upload for local directories is not supported yet. Passing a directory will return a clear error.

## Runtime Files

By default, the plugin writes runtime files to:

```bash
~/.config/opencode/openviking/
```

Possible files include:

- `openviking-memory.log`
- `openviking-session-state.json`

You can change this directory with `runtime.dataDir` in the configuration.

These are local runtime files and should not be committed to the repository.

## Troubleshooting

| Issue | What to check |
|-------|---------------|
| Plugin does not load | For package installs, confirm `~/.config/opencode/opencode.json` contains `@openviking/opencode-plugin`; for source installs, confirm `~/.config/opencode/plugins/openviking.js` exists |
| MCP tools call the wrong server | Check `~/.openviking/ovcli.conf`, or set `OPENVIKING_*` env vars / `OPENVIKING_PLUGIN_CONFIG` to the intended config path |
| 401 / 403 from OpenViking | Verify `OPENVIKING_API_KEY`; for trusted-mode deployments, also verify `OPENVIKING_ACCOUNT` and `OPENVIKING_USER` |
| Recall is empty | Confirm OpenViking has indexed memories/resources and `autoRecall.enabled` is `true` |
| Local `openviking_add_resource` fails | Pass a file path, not a directory; local directories are not uploaded automatically yet |
