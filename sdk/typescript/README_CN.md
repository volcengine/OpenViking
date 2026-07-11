# @openviking/sdk

OpenViking 的轻量级 JavaScript/TypeScript HTTP SDK，面向现代浏览器和 Node.js 18+，没有运行时依赖。

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

SDK 与 Python `openviking-sdk`、Go SDK 使用相同的 HTTP API、身份请求头、响应信封和错误码，覆盖资源与技能、文件系统与内容、检索、会话、任务、Watch、Observer 状态和租户管理接口。

浏览器本地文件可直接传入 `Blob` 或 `File`。Node.js 中存在的字符串路径会自动上传（目录会先压缩）；其他字符串会作为 URL 或服务端路径发送。浏览器无法读取任意本地路径，因此浏览器场景应传 URL 或文件对象。

## 发布

推送 `typescript-sdk@0.1.0` 格式的 tag 会自动发布对应版本，也可以从 GitHub Actions 手动触发同一 workflow。首次发布使用具备 `@openviking` scope 权限的仓库 `NPM_TOKEN`；包创建后，需要在 npm 为仓库 `volcengine/OpenViking` 和 workflow `typescript-sdk-release.yml` 配置 Trusted Publisher，后续发布即可像 `@openviking/cli` 一样使用 OIDC。
