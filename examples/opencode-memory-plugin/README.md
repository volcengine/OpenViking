# OpenViking Memory Plugin for OpenCode

OpenCode plugin example that exposes OpenViking memories as explicit tools and automatically syncs conversation sessions into OpenViking.

Chinese install guide: [INSTALL-ZH.md](./INSTALL-ZH.md)

## Mechanism

This example uses OpenCode's tool mechanism to expose OpenViking capabilities as explicit agent-callable tools.

In practice, that means:

- the agent sees concrete tools and decides when to call them
- OpenViking data is fetched on demand through tool execution instead of being pre-injected into every prompt
- the plugin also keeps an OpenViking session in sync with the OpenCode conversation and triggers background memory extraction with `memcommit`

This example focuses on explicit memory access, filesystem-style browsing, and session-to-memory synchronization inside OpenCode.

It also includes a `tool.execute.before` hook that intercepts `read`/`glob`/`grep` calls targeting `viking://` URIs and redirects the agent to the correct memory tools (`memread`/`membrowse`/`memsearch`).

## What It Does

- Exposes seven memory tools for OpenCode agents:
  - `memsearch`
  - `memread`
  - `membrowse`
  - `memcommit`
  - `memwrite`
  - `memimport`
- Automatically maps each OpenCode session to an OpenViking session
- Streams user and assistant messages into OpenViking
- Uses background `commit` tasks to avoid repeated synchronous timeout failures
- Persists local runtime state for reconnect and recovery

## Files

This example contains:

- `openviking-memory.ts`: the plugin implementation used by OpenCode
- `openviking-config.example.json`: template config
- `.gitignore`: ignores local runtime files after you copy the example into a workspace

## Prerequisites

- OpenCode
- OpenViking HTTP Server
- A valid OpenViking API key if your server requires authentication

Start the server first if it is not already running:

```bash
openviking-server --config ~/.openviking/ov.conf
```

## Install Into OpenCode

Recommended location from the OpenCode docs:

```bash
~/.config/opencode/plugins
```

Install with:

```bash
mkdir -p ~/.config/opencode/plugins
cp examples/opencode-memory-plugin/openviking-memory.ts ~/.config/opencode/plugins/openviking-memory.ts
cp examples/opencode-memory-plugin/openviking-config.example.json ~/.config/opencode/plugins/openviking-config.json
cp examples/opencode-memory-plugin/.gitignore ~/.config/opencode/plugins/.gitignore
```

Then edit `~/.config/opencode/plugins/openviking-config.json`.

OpenCode auto-discovers first-level `*.ts` and `*.js` files under `~/.config/opencode/plugins`, so no explicit `plugin` entry is required in `~/.config/opencode/opencode.json`.

This plugin also works if you intentionally place it in a workspace-local plugin directory, because it stores config and runtime files next to the plugin file itself.

Recommended: provide the API key via environment variable instead of writing it into the config file:

```bash
export OPENVIKING_API_KEY="your-api-key-here"
```

## Configuration

Example config:

```json
{
  "endpoint": "http://localhost:1933",
  "apiKey": "",
  "enabled": true,
  "timeoutMs": 30000,
  "autoCommit": {
    "enabled": true,
    "intervalMinutes": 10
  }
}
```

The environment variable `OPENVIKING_API_KEY` takes precedence over the config file.

## Runtime Files

After installation, the plugin creates these local files next to the plugin file:

- `openviking-config.json`
- `openviking-memory.log`
- `openviking-session-map.json`

These are runtime artifacts and should not be committed.

## Tools

### `memsearch`

Unified search across memories, resources, and skills.

Parameters:

- `query`: search query
- `target_uri?`: narrow search to a URI prefix such as `viking://user/memories/`
- `mode?`: `auto | fast | deep`
- `limit?`: max results
- `score_threshold?`: optional minimum score

### `memread`

Read content from a specific `viking://` URI.

Parameters:

- `uri`: target URI
- `level?`: `auto | abstract | overview | read`

### `membrowse`

Browse the OpenViking filesystem layout.

Parameters:

- `uri`: target URI
- `view?`: `list | tree | stat`
- `recursive?`: only for `view: "list"`
- `simple?`: only for `view: "list"`

### `memcommit`

Trigger immediate memory extraction for the current session.

Parameters:

- `session_id?`: optional explicit OpenViking session ID

Returns background task progress or completion details, including `task_id`, per-category `memories_extracted`, and `archived`.

### `memwrite`

Write content to a specific file in OpenViking memory at a given `viking://` URI.

Parameters:

- `uri`: complete `viking://` URI for the file to write
- `content`: the content to write
- `mode?`: `replace | append` — overwrite or add to the end (default: `replace`)

Parent directories are created automatically if they don't exist.

### `memimport`

Import resources into the OpenViking knowledge base.

Parameters:

- `path`: URL or local file path to import. URLs are fetched server-side; local files are uploaded first. For directories, zip them and pass the `.zip` path.
- `to?`: target `viking://` URI for the imported resource (must be in resources scope)
- `reason?`: reason for adding this resource (improves search relevance)
- `wait?`: wait for semantic processing to complete (default: `false`)

Content is automatically parsed, indexed, and made searchable.

## Usage Examples

Search and then read:

```typescript
const results = await memsearch({
  query: "user coding preferences",
  target_uri: "viking://user/memories/",
  mode: "auto"
})

const content = await memread({
  uri: results[0].uri,
  level: "auto"
})
```

Browse first:

```typescript
const tree = await membrowse({
  uri: "viking://resources/",
  view: "tree"
})
```

Force a mid-session commit:

```typescript
const result = await memcommit({})
```

Write a note to memory:

```typescript
const result = await memwrite({
  uri: "viking://user/memories/notes.md",
  content: "# Design Decision\n\nUse PostgreSQL for the audit log.",
  mode: "replace"
})
```

Import external documentation:

```typescript
const result = await memimport({
  path: "https://example.com/api-docs.html",
  to: "viking://resources/external/api-docs/resource-example.md",
  reason: "API reference for integration project"
})
```

## Memory Recall

The plugin can automatically search OpenViking memories and inject relevant context into each user message before it reaches the LLM. This uses OpenCode's `experimental.chat.messages.transform` hook.

> **Note**: This feature relies on an experimental OpenCode API. The hook signature or behavior may change in future OpenCode versions.

### How It Works

1. On every user message, the plugin extracts the latest user text
2. Searches OpenViking using semantic search (5-second timeout)
3. Ranks results using multi-factor scoring (base score + leaf boost + temporal boost + preference boost + lexical overlap)
4. Deduplicates results (abstract-based for regular memories, URI-based for events/cases)
5. Formats matching memories as a `<relevant-memories>` XML block
6. Appends the block to the user message's text part

If OpenViking is unavailable or the search times out, the message is passed through unchanged.

### Recall Configuration

Add an `autoRecall` block to your `openviking-config.json` to customize recall behavior:

- `enabled`: `boolean` (default: `true`) — enable or disable automatic memory recall
- `limit`: `number` (default: `6`) — maximum number of memories to inject (1–50)
- `scoreThreshold`: `number` (default: `0.15`) — minimum relevance score for a memory to be included (0–1)
- `maxContentChars`: `number` (default: `500`) — maximum characters per individual memory content
- `preferAbstract`: `boolean` (default: `true`) — prefer abstract (L0) content over full (L2) content when available
- `tokenBudget`: `number` (default: `2000`) — approximate total token budget for injected memories (100–10000, estimated at 4 chars per token)

### Example Config with Recall

```json
{
  "endpoint": "http://localhost:1933",
  "apiKey": "",
  "enabled": true,
  "timeoutMs": 30000,
  "autoCommit": {
    "enabled": true,
    "intervalMinutes": 10
  },
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

To disable recall, set `"autoRecall": { "enabled": false }`.

## Notes for Reviewers

- The plugin is designed to run as a first-level `*.ts` file in the OpenCode plugins directory
- It intentionally keeps runtime config, logs, and session maps outside the repository example
- It uses OpenViking background commit tasks to avoid repeated timeout/retry loops during long memory extraction

## Troubleshooting

- Plugin not loading: confirm the file exists at `~/.config/opencode/plugins/openviking-memory.ts`
- Service unavailable: confirm `openviking-server` is running and reachable at the configured endpoint
- Authentication failed: check `OPENVIKING_API_KEY` or `openviking-config.json`
- No memories extracted: check that your OpenViking server has working `vlm` and `embedding` configuration
