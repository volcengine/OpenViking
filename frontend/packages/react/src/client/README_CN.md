# Client

[English](README.md) / 中文

OpenViking 请求客户端，从 `@openviking/react/client` 导出。自带基于 `fetch` 的传输层，以及把配置下发给模块的 React Provider。

## 怎么用

宿主在应用根部配置一次 Provider。传的是字段值，不是请求头：

```tsx
import { OpenVikingProvider } from '@openviking/react/client';

<OpenVikingProvider
  baseURL="https://ov.example.com/gateway/openviking"
  accountId={accountId}
  userId={userId}
  resourceId={libraryId}
>
  <FileBrowser locale="zh-CN" />
</OpenVikingProvider>;
```

开源版额外传 `apiKey`；商业版由浏览器 Cookie 鉴权，因此不传 `apiKey`。`credentials` 默认 `include`，跨源调用也无需显式传入，仍遵循服务端 CORS 与浏览器 Cookie 策略。

Provider 挂载本身不发请求，也不读取浏览器中保存的凭证。

## 配置字段

每个字段都是值。请求头由 Client 组装，调用方不需要手写 `X-API-Key`、`X-OpenViking-Account`、`X-OpenViking-User` 或 `V-Resource-Id`。

| 字段          | 请求头                 | 说明                                                                                         |
| ------------- | ---------------------- | -------------------------------------------------------------------------------------------- |
| `baseURL`     | —                      | 服务地址及可选网关前缀，不含 `/api/v1`。空字符串表示同源。                                   |
| `apiKey`      | `X-API-Key`            | 开源版使用。函数形式每次请求重新求值，可读取轮换后的凭证。                                   |
| `accountId`   | `X-OpenViking-Account` | OpenViking 账号，对应数据空间。                                                              |
| `userId`      | `X-OpenViking-User`    | 账号内的用户。                                                                               |
| `resourceId`  | `V-Resource-Id`        | 当前库。数据面接口需要它。                                                                   |
| `credentials` | —                      | 传给 `fetch` 的 `RequestCredentials`。默认 `include`，可按需覆盖为 `same-origin` 或 `omit`。 |
| `signal`      | —                      | 调用方的 `AbortSignal`。                                                                     |
| `fetch`       | —                      | 传输层覆盖，用于测试或没有全局 `fetch` 的平台。                                              |

字段省略或为空字符串时不发送对应请求头。商业版的控制面接口只需 Cookie，因此不传 `apiKey`，也不传身份头。

## 分层覆盖

模块或单次请求可以覆盖个别字段。解析逐字段进行——本次请求的值，然后是 Provider 的值，最后是默认值——调用方只写要改的部分：

```ts
await client.request({
  method: 'GET',
  path: OV_PATHS.fsLs,
  query: { uri },
  options: { resourceId: otherLibraryId },
});
```

`baseURL: ''` 是明确的「同源」，与省略该字段不同。

## 直接调用

`createOvClient` 不依赖 React，用于没有 Provider 的宿主：

```ts
import { createOvClient, OV_PATHS } from '@openviking/react/client';

const client = createOvClient({
  baseURL: 'https://ov.example.com/gateway/openviking',
  apiKey: 'sk-...',
  accountId,
  userId,
  resourceId,
});

const rows = await client.request<FsEntry[]>({
  method: 'GET',
  path: OV_PATHS.fsLs,
  query: { uri, output: 'agent' },
});
```

`request` 返回原生响应的 `result`。`baseURL` 是实际生效的地址，用于隔离缓存与订阅。

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

失败响应仍是 JSON 外壳，错误码照常保留在 `OvClientError.code` 上。二进制响应没有 body，因此不会带上 `content-type: application/json`。

## 事件流

`stream` 建立 SSE 连接，返回的事件可以 `for await` 逐个消费：

```ts
const stream = await client.stream({
  method: 'POST',
  path: '/bot/v1/chat/stream',
  body: { message, session_id },
});

for await (const event of stream) {
  // event.data 是原始文本，事件类型在 JSON 里面
}

stream.abort();
```

- `accept: text/event-stream` 默认发送，`headers` 可追加或覆盖；身份字段仍由 Client 组装并在同名时胜出。
- `body` 允许任意字段，服务端约定的开关（例如只订阅已有会话的 `Attach`）原样转发。
- `data` 保持原文不做 JSON 解析：OpenViking 把事件类型和事件 id 放在 `data` 的 JSON 里，不占 SSE 的 `event` / `id` 字段。
- 只有注释的心跳块（`: keepalive`）不产生事件；末尾缺空行的块仍会派发。
- 响应到达即 resolve。HTTP 失败、`content-type` 不是事件流、以及非流响应里的 `status: 'error'` 都在 resolve 之前抛出 `OvClientError`。
- `abort()` 或调用方 signal 取消后，迭代安静结束而不抛 `AbortError`——取消是调用方的意图，不是失败。

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

`OV_PATHS` 用 `as const` 而不是 `enum`：字符串 enum 在 `isolatedModules` 下不会内联，产出的对象还会被当成副作用；作为公开导出时它是 ambient const enum，消费者一旦开启 `isolatedModules` 或 `verbatimModuleSyntax` 就会直接编译失败。

## 失败

`OvClientError` 是唯一的失败类型。它区分 HTTP 失败与原生响应里的 `status: 'error'`，并保留服务端错误码、状态、附加信息与请求标识。`code` 在服务端给出错误码时用服务端的，否则为 `HTTP_<status>` 或 `NETWORK_ERROR`。`isOvClientError` 用于把 `unknown` 收窄到该类型。

## 替换传输层

`OvClient` 是模块依赖的接口。要复用宿主已有的 axios 实例或 OpenAPI 生成的 SDK 时实现它：

```ts
class AxiosOvClient implements OvClient {
  constructor(
    private readonly instance: AxiosInstance,
    readonly baseURL: string,
  ) {}

  async request<TResult>(spec: OvRequestSpec): Promise<TResult> {
    const { data } = await this.instance.request({
      method: spec.method,
      url: spec.path,
      params: spec.query,
      data: spec.body,
      signal: spec.options?.signal,
    });
    if (data?.status === 'error') {
      throw new OvClientError({
        code: data.error?.code ?? 'UNKNOWN',
        message: data.error?.message ?? 'Request failed',
      });
    }
    return data?.result ?? data;
  }
}
```

不管用哪种传输层，这三件事都由它负责，模块不再重复：拼接服务地址与路径、把身份字段转成请求头、把响应变成结果或 `OvClientError`。`OvClient` 还有 `stream`，实现自定义传输层时也要一并提供；事件流的分帧可以直接复用导出的解析器思路，但解析器本身留在包内，不单独开放。

## 开发

在 `frontend/` 下：

```bash
npm run build:lib
npm run typecheck
npm test
```

客户端没有运行时依赖，只在真正发请求时才用 `fetch`，因此在服务端渲染阶段导入是安全的。
