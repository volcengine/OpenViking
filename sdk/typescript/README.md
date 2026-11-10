# @openviking/sdk

Lightweight JavaScript and TypeScript HTTP client for an existing OpenViking server. It targets Node.js 18+ and has no runtime dependencies.

```bash
npm install @openviking/sdk
```

```ts
import { OpenVikingClient } from "@openviking/sdk";

const client = new OpenVikingClient({
  baseUrl: "http://127.0.0.1:1933",
  apiKey: "your-key",
});

const results = await client.search("deployment guide", {
  targetUri: "viking://resources",
  limit: 10,
});
```

The client follows the same HTTP API, identity headers, response envelope and error codes as `openviking-sdk` for Python and the Go SDK. It supports resources and skills, MCP servers and live context, filesystem/content operations, retrieval, sessions, OVPack files, snapshots, tasks, watches, observer status and tenant administration.

Existing local file paths are uploaded automatically, and local directories are zipped before upload. Other strings are sent to the server as URLs or server-side paths.

To ingest content without VLM semantic understanding, pass `processingMode: "vectors_only"` to `addResource`. This writes or syncs the resource tree and vectorizes current files, but does not generate or refresh `.abstract.md` / `.overview.md`.

```ts
const task = await client.addResource("./docs/guide.md", {
  to: "viking://resources/guide",
  processingMode: "vectors_only",
});
console.log(task.task_id);
```

Query `client.getTask(task.task_id as string)` for import status and search the imported content after the task reaches `completed`.

Event-memory tags can be configured as session defaults, updated later, or overridden per commit. Passing `[]` to `commitSession` explicitly skips the session defaults for that commit.

```ts
await client.createSession({
  sessionId: "s1",
  memoryExtractionConfig: {
    events: { tags: ["team=search", "channel=web"] },
  },
});
await client.createSession({ sessionId: "manual", autoCommitPolicy: null });
await client.updateSessionConfig("s1", {
  autoCommitPolicy: { message_count_threshold: 25 },
  memoryExtractionConfig: {
    events: { tags: ["team=search", "channel=app"] },
  },
});
await client.updateSessionConfig("s1", { autoCommitPolicy: null });
await client.commitSession("s1", {
  keepRecentCount: 0,
  eventTags: ["team=search", "channel=web"],
});
await client.commitSession("s1", { keepRecentCount: 0, eventTags: [] });
```

Deployments using shared temporary storage can set `uploadMode: "shared"`; the server also accepts `"local"` (the default).

## MCP Servers and Live Context

```ts
await client.addMCP(
  "weather",
  "https://mcp.example.com/api",
  { Authorization: "Bearer token" },
  { targetUri: "viking://agent/tools" },
);
// Tool summaries and indexing continue in the background by default.
// Pass { wait: true } as the fourth argument to wait.
const tools = await client.findMCPTools("current weather", 5);
const mounted = await client.mountMCPTool(
  "viking://agent/tools/weather/current.json",
  "viking://resources/arranger/beijing-weather.md",
  "Beijing Weather",
  {
    description: "Current weather for the Beijing region.",
    timeoutSeconds: 60,
    arguments: { city: "Beijing" },
    tags: ["project=weather", "kind=live-context"],
    wait: true,
  },
);
const batchMounted = await client.batchMountMCPTools(
  [
    {
      toolUri: "viking://agent/tools/weather/current.json",
      nodeUri: "viking://resources/arranger/shanghai-weather.md",
      title: "Shanghai Weather",
      arguments: { city: "Shanghai" },
    },
  ],
  { wait: true },
);
await client.batchUnmountMCPTools(
  ["viking://resources/arranger/shanghai-weather.md"],
  { wait: true },
);
const syncResult = await client.syncMCP("weather", {
  targetUri: "viking://agent/tools",
});
console.log(syncResult.changes, syncResult.affected_mounts);
```

The client also exposes `removeMCP`, `listMCPs`, `validateMCP`, `getMCP`,
`getMCPStatus`, `invokeMCPTool`, `batchMountMCPTools`, `unmountMCPTool`,
`batchUnmountMCPTools`, and `listMCPMounts`.

Registration defaults to `viking://~/tools`. Set `targetUri` to
`viking://agent/tools` for account-shared servers. `listMCPs` and
`findMCPTools` merge both spaces when it is omitted; name-based operations
default to the user space.

OVPack exports and backups follow the Python and Go SDK contract: they are streamed to a Node.js local file and return its final path.

```ts
const packPath = await client.exportOVPack(
  "viking://resources/docs",
  "./backups",
);
await client.importOVPack(packPath, "viking://resources", {
  onConflict: "overwrite",
  vectorMode: "auto",
});
```

## Release

Pushing a tag such as `typescript-sdk@0.1.0` publishes the matching package version automatically. The same workflow can be started manually from GitHub Actions. The first publish uses the repository `NPM_TOKEN` with `@openviking` scope access; after the package exists, configure npm Trusted Publishing for repository `volcengine/OpenViking` and workflow `typescript-sdk-release.yml` so subsequent publishes use OIDC like `@openviking/cli`.
