# @openviking/sdk

OpenViking 的轻量级 JavaScript/TypeScript HTTP SDK，面向 Node.js 18+，没有运行时依赖。

```bash
npm install @openviking/sdk
```

```ts
import { OpenVikingClient } from "@openviking/sdk";

const client = new OpenVikingClient({
  baseUrl: "http://127.0.0.1:1933",
  apiKey: "your-key",
});

const results = await client.search("部署文档", {
  targetUri: "viking://resources",
  limit: 10,
});
```

SDK 与 Python `openviking-sdk`、Go SDK 使用相同的 HTTP API、身份请求头、响应信封和错误码，覆盖资源与技能、文件系统与内容、资源关系、检索、会话、OVPack、快照、任务、Watch、Observer 状态和租户管理接口。

Node.js 中存在的本地文件路径会自动上传，目录会先压缩后上传；其他字符串会作为 URL 或服务端路径发送。

如果只希望入库并生成向量、不走 VLM 语义理解，可以给 `addResource` 传 `processingMode: "vectors_only"`。该模式会写入/同步资源树并向量化当前文件，但不会生成或刷新 `.abstract.md` / `.overview.md`。

```ts
await client.addResource("./docs/guide.md", {
  to: "viking://resources/guide",
  processingMode: "vectors_only",
  wait: true,
});
```

事件记忆 tags 可设置为 session 默认值、后续更新，也可在单次 commit 时覆盖。向 `commitSession` 传 `[]` 表示本次显式跳过 session 默认 tags。

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

使用共享临时存储的部署可设置 `uploadMode: "shared"`；服务端也接受 `"local"`（默认值）。

OVPack 导出和备份与 Python、Go SDK 契约一致：内容会流式写入 Node.js 本地文件，并返回最终文件路径。

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

## 发布

推送 `typescript-sdk@0.1.0` 格式的 tag 会自动发布对应版本，也可以从 GitHub Actions 手动触发同一 workflow。首次发布使用具备 `@openviking` scope 权限的仓库 `NPM_TOKEN`；包创建后，需要在 npm 为仓库 `volcengine/OpenViking` 和 workflow `typescript-sdk-release.yml` 配置 Trusted Publisher，后续发布即可像 `@openviking/cli` 一样使用 OIDC。
