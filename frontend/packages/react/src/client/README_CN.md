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
  <FileBrowser />
</OpenVikingProvider>;
```

开源版额外传 `apiKey`；商业版由浏览器 Cookie 鉴权，因此不传 `apiKey`，跨源调用时把 `credentials` 设为 `include`。

Provider 挂载本身不发请求，也不读取浏览器中保存的凭证。

## 配置字段

每个字段都是值。请求头由 Client 组装，调用方不需要手写 `X-API-Key`、`X-OpenViking-Account`、`X-OpenViking-User` 或 `V-Resource-Id`。

| 字段          | 请求头                 | 说明                                                                           |
| ------------- | ---------------------- | ------------------------------------------------------------------------------ |
| `baseURL`     | —                      | 服务地址及可选网关前缀，不含 `/api/v1`。空字符串表示同源。                     |
| `apiKey`      | `X-API-Key`            | 开源版使用。函数形式每次请求重新求值，可读取轮换后的凭证。                     |
| `accountId`   | `X-OpenViking-Account` | OpenViking 账号，对应数据空间。                                                |
| `userId`      | `X-OpenViking-User`    | 账号内的用户。                                                                 |
| `resourceId`  | `V-Resource-Id`        | 当前库。数据面接口需要它。                                                     |
| `credentials` | —                      | 传给 `fetch` 的 `RequestCredentials`。默认 `same-origin`；商业版需 `include`。 |
| `signal`      | —                      | 调用方的 `AbortSignal`。                                                       |
| `fetch`       | —                      | 传输层覆盖，用于测试或没有全局 `fetch` 的平台。                                |

字段省略或为空字符串时不发送对应请求头。商业版的控制面接口只需 Cookie，因此不传 `apiKey`，也不传身份头。

## 分层覆盖

模块或单次请求可以覆盖个别字段。解析逐字段进行——本次请求的值，然后是 Provider 的值，最后是默认值——调用方只写要改的部分：

```ts
await client.request({
  method: 'GET',
  path: '/api/v1/fs/ls',
  query: { uri },
  options: { resourceId: otherLibraryId },
});
```

`baseURL: ''` 是明确的「同源」，与省略该字段不同。

## 直接调用

`createOvClient` 不依赖 React，用于没有 Provider 的宿主：

```ts
import { createOvClient } from '@openviking/react/client';

const client = createOvClient({
  baseURL: 'https://ov.example.com/gateway/openviking',
  apiKey: 'sk-...',
  accountId,
  userId,
  resourceId,
});

const rows = await client.request<FsEntry[]>({
  method: 'GET',
  path: '/api/v1/fs/ls',
  query: { uri, output: 'agent' },
});
```

`request` 返回原生响应的 `result`。`baseURL` 是实际生效的地址，用于隔离缓存与订阅。

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

不管用哪种传输层，这三件事都由它负责，模块不再重复：拼接服务地址与路径、把身份字段转成请求头、把响应变成结果或 `OvClientError`。

## 开发

在 `frontend/` 下：

```bash
npm run build:lib
npm run typecheck
npm test
```

客户端没有运行时依赖，只在真正发请求时才用 `fetch`，因此在服务端渲染阶段导入是安全的。
