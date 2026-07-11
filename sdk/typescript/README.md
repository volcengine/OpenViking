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

The client follows the same HTTP API, identity headers, response envelope and error codes as `openviking-sdk` for Python and the Go SDK. It supports resources and skills, filesystem/content operations, retrieval, sessions, tasks, watches, observer status and tenant administration.

Existing local file paths are uploaded automatically, and local directories are zipped before upload. Other strings are sent to the server as URLs or server-side paths.

Deployments using shared temporary storage can set `uploadMode: "shared"`; the server also accepts `"local"` (the default).

## Release

Pushing a tag such as `typescript-sdk@0.1.0` publishes the matching package version automatically. The same workflow can be started manually from GitHub Actions. The first publish uses the repository `NPM_TOKEN` with `@openviking` scope access; after the package exists, configure npm Trusted Publishing for repository `volcengine/OpenViking` and workflow `typescript-sdk-release.yml` so subsequent publishes use OIDC like `@openviking/cli`.
