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

## HTTP proxies

The SDK uses the runtime's global `fetch` by default. On Node.js 22.21+ or
24.0+, enable [Node.js environment proxy support](https://nodejs.org/learn/http/enterprise-network-configuration)
before the process starts:

```bash
HTTP_PROXY=http://proxy.example.com:8080 \
HTTPS_PROXY=http://proxy.example.com:8080 \
NO_PROXY=localhost,127.0.0.1 \
NODE_USE_ENV_PROXY=1 node app.js
```

For Node.js 18.17+ versions without built-in environment proxy support, configure
an [Undici `EnvHttpProxyAgent`](https://github.com/nodejs/undici/blob/v6.21.3/docs/docs/api/EnvHttpProxyAgent.md)
before creating the client:

```bash
npm install undici@^6.21.3
```

```ts
import { OpenVikingClient } from "@openviking/sdk";
import { EnvHttpProxyAgent, setGlobalDispatcher } from "undici";

const proxyDispatcher = new EnvHttpProxyAgent();
setGlobalDispatcher(proxyDispatcher);

const client = new OpenVikingClient({
  baseUrl: "https://openviking.example.com",
  apiKey: process.env.OPENVIKING_API_KEY,
});
```

Set `HTTP_PROXY`, `HTTPS_PROXY`, and `NO_PROXY` before constructing the
dispatcher. This changes the process-wide Undici dispatcher. Applications that
already own their transport policy can instead pass a WHATWG-compatible
proxy-aware function through `ClientConfig.fetch`. Do not store proxy
credentials in source code.

The client follows the same HTTP API, identity headers, response envelope and error codes as `openviking-sdk` for Python and the Go SDK. It supports resources and skills, filesystem/content operations, retrieval, sessions, OVPack files, snapshots, tasks, watches, observer status and tenant administration.

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
