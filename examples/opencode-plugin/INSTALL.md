# Install the Unified OpenViking OpenCode Plugin

This plugin adds one unified OpenViking plugin for OpenCode:

- OpenViking MCP tools for memory, resources, and code context
- Long-term memory, session synchronization, lifecycle commit, and automatic recall

This is the only OpenCode plugin example maintained in this repository. It does not require the agent to use the `ov` command. Model tools are provided by the same stdio MCP proxy used by the other OpenViking memory plugins.

User guide: [OpenCode](../../docs/en/agent-integrations/10-opencode.md). Shared behaviour (capture and commits, recall, session-start context, offline retries, subagents, compaction): [capability reference](../../docs/en/agent-integrations/16-capability-reference.md). OpenCode 1.x is not supported: the plugin writes one line saying so to its log and does nothing.

## Prerequisites

Prepare the following first:

- OpenCode 2.0.15+
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

## Installation Method 1: One-line Installer (Recommended)

OpenCode shares the unified installer with the other memory plugins. It asks which harnesses to install and for your OpenViking credentials:

```bash
curl -fsSL https://openviking.ai/install | bash
```

The installer copies the plugin into `~/.config/opencode/plugins/` as a local file plugin, writes the `openviking` MCP server entry into `~/.config/opencode/opencode.json`, and configures `~/.openviking/ovcli.conf`.

## Installation Method 2: Published Package

To enable it through OpenCode's package plugin mechanism instead:

```json
{
  "plugin": ["@openviking/opencode-plugin"]
}
```

OpenCode v2 normalizes it to the native `plugins` form at load time, so no manual config migration is required.

## Installation Method 3: Source Install

Use this method for development, debugging, or PR testing. OpenCode's recommended plugin directory is:

```bash
~/.config/opencode/plugins
```

Run the following commands from the repository root:

```bash
node examples/memory-plugin-shared/sync.mjs
mkdir -p ~/.config/opencode/plugins/openviking
cp examples/opencode-plugin/wrappers/openviking.js ~/.config/opencode/plugins/openviking.js
cp examples/opencode-plugin/index.mjs examples/opencode-plugin/package.json ~/.config/opencode/plugins/openviking/
cp -r examples/opencode-plugin/lib ~/.config/opencode/plugins/openviking/
cp -r examples/opencode-plugin/servers ~/.config/opencode/plugins/openviking/
```

`sync.mjs` generates `lib/shared/`, the shared modules the plugin and its MCP proxy import. That directory is not in git, so run it before copying, and again after every `git pull`.

After installation, the layout should look like this:

```text
~/.config/opencode/plugins/
├── openviking.js
└── openviking/
    ├── index.mjs
    ├── host.mjs
    ├── package.json
    ├── lib/
    └── servers/
```

The top-level `openviking.js` forwards the first-level `.js` entry that OpenCode can discover to the actual plugin directory:

```js
export { OpenVikingPlugin, default } from "./openviking/index.mjs"
```

This wrapper is only for source installs with the directory layout shown above. npm package installs load `index.mjs` directly through `package.json`. Use the `.js` wrapper for source installs; OpenCode's local plugin scanner discovers JavaScript/TypeScript plugin files.

If you install through an npm package, you can also use `examples/opencode-plugin` as a normal OpenCode plugin package.

## Configuration

Settings live in the shared client configuration file `~/.openviking/ovcli.conf`, in the shared `plugin` section or in the `plugin.opencode` override (which wins for OpenCode):

```json
{
  "url": "http://127.0.0.1:1933",
  "api_key": "your-api-key-here",
  "plugin": {
    "opencode": {
      "enabled": true,
      "mcpEnabled": true,
      "timeoutMs": 30000,
      "repoContext": true,
      "repoContextCacheTtlMs": 60000
    }
  }
}
```

`repoContext` injects indexed `viking://resources/` repositories into the system prompt; `dataDir` moves the runtime files. Every other setting, the resolution order, identity headers and peers are described in [client configuration](../../docs/en/configuration/02-client.md).

It is recommended to provide the API key through an environment variable instead of writing it into the configuration file:

```bash
export OPENVIKING_API_KEY="your-api-key-here"
```

`OPENVIKING_API_KEY`, `OPENVIKING_ACCOUNT`, `OPENVIKING_USER`, and `OPENVIKING_PEER_ID` take precedence over the corresponding values in `ovcli.conf`. For advanced setups, use `OPENVIKING_CLI_CONFIG_FILE` to point to an `ovcli.conf` at another path.

### Hook-only mode

If another MCP server already exposes OpenViking, set the bundled MCP registration to `false` while keeping this plugin's lifecycle hooks active:

```json
{
  "plugin": {
    "opencode": { "mcpEnabled": false }
  }
}
```

Repository context, automatic recall, message capture, and lifecycle commits remain enabled. This does not add or overwrite OpenCode's `mcp.openviking` entry.

## Verify

Restart OpenCode after changing plugin or OpenViking configuration.

In a new OpenCode session, ask the agent to browse OpenViking memory or search for a known indexed resource. The plugin should expose the OpenViking MCP server, with tools namespaced by OpenCode as `openviking_*`:

- `openviking_search`, `openviking_find`
- `openviking_read`, `openviking_download`, `openviking_list`, `openviking_tree`, `openviking_grep`, `openviking_glob`
- `openviking_remember`, `openviking_write`, `openviking_edit`, `openviking_add_resource`, `openviking_add_skill`
- `openviking_list_watches`, `openviking_cancel_watch`, `openviking_forget`, `openviking_health`

If anything looks wrong, run the doctor. It checks the plugin install, the client configuration, and the server connection, and lists a fix for every problem it finds; the npm package ships the same script under `scripts/`, and the `ov-memory-doctor` skill runs it from inside a session:

```bash
node ~/.config/opencode/plugins/openviking/scripts/ov-memory-doctor.mjs
```

The runtime files hold the plugin's log and session state:

```bash
ls ~/.config/opencode/openviking/
tail -n 100 ~/.config/opencode/openviking/openviking-memory.log
```

For a local server, also confirm OpenViking is reachable:

```bash
curl http://localhost:1933/health
```

## Available MCP Tools

The plugin registers OpenViking's stdio MCP proxy through OpenCode config. The server's real `tools/list` response is the source of truth; OpenCode namespaces the tools as `openviking_*`.

- If an agent tries to use OpenCode's local `read`, `glob`, or `grep` tools on a `viking://` URI, the plugin blocks that call and points it to the MCP tools.
- A `bash` command that contains a `viking://` URI still runs; the plugin appends a notice pointing to the MCP tools to its output, which the agent can ignore when the URI is intentional.

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

You can change this directory with `dataDir` in `plugin.opencode`.

These are local runtime files and should not be committed to the repository.

## Troubleshooting

| Issue | What to check |
|-------|---------------|
| Plugin does not load | For package installs, confirm `~/.config/opencode/opencode.json` contains `@openviking/opencode-plugin`; for source installs, confirm `~/.config/opencode/plugins/openviking.js` exists |
| Load fails with a missing `lib/shared/*.mjs` module | The source copy was made without running `sync.mjs` first. Run `node examples/memory-plugin-shared/sync.mjs` from the repository root and copy `lib/` again |
| MCP tools call the wrong server | Check `~/.openviking/ovcli.conf`, or set `OPENVIKING_*` env vars / `OPENVIKING_CLI_CONFIG_FILE` to the intended config path |
| 401 / 403 from OpenViking | Verify `OPENVIKING_API_KEY`; for trusted-mode deployments, also verify `OPENVIKING_ACCOUNT` and `OPENVIKING_USER` |
| Recall is empty | Confirm OpenViking has indexed memories/resources and `autoRecall` is `true` |
| Local `openviking_add_resource` fails | Pass a file path, not a directory; local directories are not uploaded automatically yet |
