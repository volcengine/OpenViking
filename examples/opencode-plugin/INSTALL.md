# Install the Unified OpenViking OpenCode Plugin

This plugin adds one unified OpenViking plugin for OpenCode:

- Semantic retrieval for external repositories
- Long-term memory, session synchronization, lifecycle commit, and automatic recall

The older split examples remain available for now and will be deprecated in a future update. This plugin does not install `skills/openviking/SKILL.md`, and it does not require the agent to use the `ov` command. The capabilities from the former skill are exposed as OpenCode tools here.

## Prerequisites

Prepare the following first:

- OpenCode
- OpenViking HTTP Server
- Node.js / npm, used to install plugin dependencies
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
  "plugin": ["openviking-opencode-plugin"]
}
```

Use the final published package name if it changes before release.

## Installation Method 2: Source Install

Use this method for development, debugging, or PR testing. OpenCode's recommended plugin directory is:

```bash
~/.config/opencode/plugins
```

Run the following commands from the repository root:

```bash
mkdir -p ~/.config/opencode/plugins/openviking
cp examples/opencode-plugin/wrappers/openviking.mjs ~/.config/opencode/plugins/openviking.mjs
cp examples/opencode-plugin/index.mjs examples/opencode-plugin/package.json ~/.config/opencode/plugins/openviking/
cp -r examples/opencode-plugin/lib ~/.config/opencode/plugins/openviking/
cd ~/.config/opencode/plugins/openviking
npm install
```

After installation, the layout should look like this:

```text
~/.config/opencode/plugins/
├── openviking.mjs
└── openviking/
    ├── index.mjs
    ├── package.json
    ├── lib/
    └── node_modules/
```

The top-level `openviking.mjs` forwards the first-level `.mjs` entry that OpenCode can discover to the actual plugin directory:

```js
export { OpenVikingPlugin, default } from "./openviking/index.mjs"
```

This wrapper is only for source installs with the directory layout shown above. npm package installs load `index.mjs` directly through `package.json`.

If you install through an npm package, you can also use `examples/opencode-plugin` as a normal OpenCode plugin package.

## Configuration

Create the user-level configuration file:

```bash
~/.config/opencode/openviking-config.json
```

Example configuration:

```json
{
  "endpoint": "http://localhost:1933",
  "apiKey": "",
  "account": "",
  "user": "",
  "agentId": "",
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

It is recommended to provide the API key through an environment variable instead of writing it into the configuration file:

```bash
export OPENVIKING_API_KEY="your-api-key-here"
```

`apiKey` is sent as `X-API-Key`. `account`, `user`, and `agentId` are sent as `X-OpenViking-Account`, `X-OpenViking-User`, and `X-OpenViking-Agent`, respectively. If multi-tenant authentication is enabled on the OpenViking server, tenant-scoped APIs usually require `account` and `user` to be configured.

`OPENVIKING_API_KEY`, `OPENVIKING_ACCOUNT`, `OPENVIKING_USER`, and `OPENVIKING_AGENT_ID` take precedence over the corresponding values in `openviking-config.json`.

For advanced setups, use `OPENVIKING_PLUGIN_CONFIG` to point to another configuration file path.

## Available Tools

The plugin exposes the following tools through the OpenCode `tool` hook:

- `memsearch`: semantic retrieval across memories, resources, and skills
- `memread`: read a specific `viking://` URI
- `membrowse`: browse the OpenViking filesystem
- `memcommit`: commit the current session and trigger memory extraction
- `memgrep`: exact text or pattern search, replacing the former `ov grep` use case
- `memglob`: file glob enumeration, replacing the former `ov glob` use case
- `memadd`: add a remote URL or local file resource, replacing common `ov add-resource` scenarios
- `memremove`: remove resources, replacing `ov rm`
- `memqueue`: inspect the processing queue, replacing `ov observer queue`

Usage guidance:

- Use `memsearch` for conceptual questions.
- Use `memgrep` for exact symbols, function names, class names, or error strings.
- Use `memglob` to enumerate files.
- Use `memread` to read content.
- Use `membrowse` to explore directory structure.
- Before deleting anything, obtain explicit user confirmation first; then call `memremove` with `confirm: true`.

## Local Files with `memadd`

`memadd` supports three input types:

- Remote `http(s)` URL: directly calls `/api/v1/resources`
- Local file path: first calls `/api/v1/resources/temp_upload`, then adds the resource using the returned `temp_file_id`
- `file://` URL: handled as a local file

Relative paths are resolved against the current OpenCode project directory. Examples:

```text
memadd path="https://example.com/spec.md" to="viking://resources/spec"
memadd path="./docs/notes.md" parent="viking://resources/"
memadd path="file:///home/alice/project/notes.md" reason="project notes"
```

Automatic zip upload for local directories is not supported yet. Passing a directory will return a clear error.

## Runtime Files

By default, the plugin writes runtime files to:

```bash
~/.config/opencode/openviking/
```

Possible files include:

- `openviking-memory.log`
- `openviking-session-map.json`

You can change this directory with `runtime.dataDir` in the configuration.

These are local runtime files and should not be committed to the repository.
