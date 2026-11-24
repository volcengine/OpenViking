# Client

[English](README.md) / 中文

OpenViking 请求客户端，从 `@openviking/react/client` 导出：基于 `fetch` 的原生 OpenViking API 客户端，以及把配置下发给区块的 `OpenVikingProvider`。

## 怎么用

在应用根部配置一次 Provider。传的是字段值，不是请求头：

```tsx
import { OpenVikingProvider } from '@openviking/react/client';
import { FileBrowser } from '@openviking/react/modules/file-browser';

<OpenVikingProvider
  baseURL="https://ov.example.com/gateway/openviking"
  accountId={accountId}
  userId={userId}
  resourceId={libraryId}
>
  <FileBrowser />
</OpenVikingProvider>;
```

开源版额外传 `apiKey`；商业版由浏览器 Cookie 鉴权，不传 `apiKey`。`credentials` 默认 `include`，跨源请求也一样，仍遵循服务端 CORS 与浏览器 Cookie 策略。

Provider 挂载本身不发请求，也不读取浏览器中保存的凭证。它的 `locale` 设置子树的界面语言，见[国际化](../../../../docs/docs/zh/guide/i18n.mdx)。

## 配置字段

每个字段都是值。请求头由 Client 组装，调用方不需要手写 `X-API-Key`、`X-OpenViking-Account`、`X-OpenViking-User` 或 `V-Resource-Id`。

| 字段          | 请求头                 | 说明                                                                                                                     |
| ------------- | ---------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `baseURL`     | -                      | 服务地址及可选网关前缀，不含 `/api/v1`。空字符串表示同源。                                                               |
| `apiKey`      | `X-API-Key`            | 开源版的凭证。                                                                                                           |
| `accountId`   | `X-OpenViking-Account` | OpenViking 账号 ID，对应数据空间。                                                                                       |
| `userId`      | `X-OpenViking-User`    | 账号内的用户 ID。                                                                                                        |
| `resourceId`  | `V-Resource-Id`        | 当前库 ID。数据面接口需要它。                                                                                            |
| `signal`      | -                      | 中止请求的 `AbortSignal`。                                                                                               |
| `credentials` | -                      | 传给 `fetch` 的 `RequestCredentials`。默认 `include`，可覆盖为 `same-origin` 或 `omit`。只能在 Provider 或客户端上设置。 |
| `fetch`       | -                      | 替换全局 `fetch`，用于测试、链路追踪或没有全局 `fetch` 的平台。只能在 Provider 或客户端上设置。                          |

四个身份字段也可以传函数，返回字符串或字符串的 Promise。函数每次请求求值一次，因此可以读取轮换后的凭证。身份字段省略或为空字符串时不发送对应请求头。

## 分层覆盖

区块或单次请求可以覆盖个别字段（`baseURL`、身份字段和 `signal`）。解析逐字段进行：本次请求的值，然后是最近的 Provider 的值，最后是默认值。嵌套的 Provider 继承它省略的字段。调用方只写要改的部分：

```ts
await client.request({
  method: 'GET',
  path: OV_PATHS.fsLs,
  query: { uri },
  options: { resourceId: otherLibraryId },
});
```

`baseURL: ''` 明确表示「同源」，与省略该字段不同。身份字段传空字符串，则清除从上一层继承的值。

## 直接调用

`createOvClient` 不依赖 `OpenVikingProvider`，用于 React 树之外的代码：

```ts
import { createOvClient, OV_PATHS } from '@openviking/react/client';

const client = createOvClient({
  baseURL: 'https://ov.example.com/gateway/openviking',
  apiKey,
  accountId,
  userId,
  resourceId,
});

const rows = await client.request<{ uri: string; isDir?: boolean }[]>({
  method: 'GET',
  path: OV_PATHS.fsLs,
  query: { uri, output: 'agent' },
});
```

`request` 返回原生响应的 `result`。`client.baseURL` 是创建客户端时的服务地址，适合作为缓存或订阅的键。

列表接口把 `has_more` 放在 `result` 之外。`requestEnvelope` 把两者一起返回为 `{ result, hasMore }`，只有响应设置了 `has_more: true` 时 `hasMore` 才为 `true`：

```ts
const { result, hasMore } = await client.requestEnvelope<{ uri: string }[]>({
  method: 'GET',
  path: OV_PATHS.fsLs,
  query: { uri, node_limit: 200, offset: 0 },
});
```

## 二进制响应

`responseType: 'blob'` 让 `request` 跳过外壳解析，直接返回原始字节。预览图片、下载文件都走这条路：

```ts
const blob = await client.request<Blob>({
  method: 'GET',
  path: OV_PATHS.contentDownload,
  query: { uri },
  responseType: 'blob',
});
```

失败响应仍是 JSON 外壳，错误码照常保留在 `OvClientError.code` 上。下载请求没有 `body`，因此不会带上 `content-type: application/json`。`requestEnvelope` 不接受 `responseType: 'blob'`。

## 事件流

`stream` 建立 SSE 连接，返回的事件可以 `for await` 逐个消费：

```ts
const stream = await client.stream({
  method: 'POST',
  path: OV_PATHS.botChatStream,
  body: { message, session_id },
});

for await (const event of stream) {
  // event.data 是原始文本，事件类型在 JSON 里面
}

stream.abort();
```

- `accept: text/event-stream` 默认发送，`headers` 可追加或覆盖；身份字段仍由 Client 组装并在同名时胜出。
- `body` 允许任意字段，按原样发送，服务端约定的选项因此可以透传。
- `data` 保持原文，不做 JSON 解析：OpenViking 把事件类型和事件 id 放在 `data` 的 JSON 里，不占 SSE 的 `event` / `id` 字段。
- 只有注释的心跳块（`: keepalive`）不产生事件；末尾缺空行的块仍会派发。
- `stream` 在响应到达时 resolve。HTTP 失败、`content-type` 不是事件流，以及非流响应里的 `status: 'error'`，都在 resolve 之前抛出 `OvClientError`。
- `abort()` 或通过 `signal` 取消后，迭代安静结束而不抛 `AbortError`：取消是调用方的意图，不是失败。
- 一条流只能迭代一次。

## 接口路径

`OV_PATHS` 集中维护原生接口路径，接口改名时只改一处。带路径参数的接口用 `OV_PATH_BUILDERS`，参数会自动 URL 编码：

```ts
import { OV_PATHS, OV_PATH_BUILDERS } from '@openviking/react/client';

await client.request({ method: 'GET', path: OV_PATHS.fsLs, query: { uri } });
await client.request({
  method: 'POST',
  path: OV_PATH_BUILDERS.sessionMessages(sessionId),
  body: { role: 'user', content },
});
```

`OV_PATHS` 是 `as const` 对象，不是 `enum`，开启 `isolatedModules` 或 `verbatimModuleSyntax` 的消费者因此仍能正常编译。

## 失败

`OvClientError` 是 `request`、`requestEnvelope` 和 `stream` 唯一的失败类型。它区分 HTTP 失败与原生响应里的 `status: 'error'`，并保留服务端错误码、`statusCode`、`details`、`requestId` 与 `responseBody`。`isOvClientError` 用于把 `unknown` 收窄到该类型。

服务端给出错误码时，`code` 就是该错误码，否则取下列之一：

- `HTTP_<status>`：HTTP 失败。
- `UNKNOWN`：`status: 'error'` 的响应没有错误码。
- `NETWORK_ERROR`：传输层失败。
- `UNEXPECTED_CONTENT_TYPE`：`stream` 收到的不是事件流。
- `INVALID_REQUEST`：`requestEnvelope` 被要求返回 `blob`。

通过 `signal` 取消请求不算客户端失败：`request` 以原始的 `AbortError` 拒绝。

## 替换传输层

默认传输层调用全局 `fetch`。要让请求经过自己的函数，把 `fetch` 传给 `OpenVikingProvider` 或 `createOvClient`，例如添加链路追踪，或在测试里返回固定响应：

```tsx
const fixedFetch: typeof fetch = async () =>
  new Response(JSON.stringify({ result: [] }), {
    headers: { 'content-type': 'application/json' },
  });

<OpenVikingProvider fetch={fixedFetch}>
  <FileBrowser />
</OpenVikingProvider>;
```

`createOvClient` 返回的 `OvClient` 带有 `request`、`requestEnvelope`、`stream` 和 `baseURL`，是区块依赖的接口。区块始终使用最近的 Provider 创建的客户端，Provider 不接收自己实现的客户端，所以要改变区块的传输方式，就传 `fetch`。不管传输层怎么换，这三件事都由 Client 负责，区块不再重复：拼接服务地址与路径、把身份字段转成请求头、把响应变成结果或 `OvClientError`。

## 浏览器运行环境

Client 面向浏览器运行。`createOvClient` 需要一个 `fetch`，可以是全局的，也可以是你传入的，两者都没有就会抛错。

客户端的测试位于 `packages/react/test/client*.test.mjs`，在 `frontend/` 下运行 `npm test`（见[前端工作区 README](../../../../README.md)）。
