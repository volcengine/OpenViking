# Client

English / [中文](README_CN.md)

OpenViking request client, exported from `@openviking/react/client`. It ships a `fetch`-based transport and the React Provider that hands configuration to the modules.

## Using it

A host configures the Provider once at the application root. It passes values, never headers:

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

The open-source edition adds `apiKey`; the commercial edition authenticates with the browser cookie, so it omits `apiKey`. `credentials` defaults to `include` and can be omitted for cross-origin calls too. Server CORS and browser cookie policies still apply.

Mounting the Provider sends no request and reads no credential stored in the browser.

## Configuration fields

Every field is a value. The Client assembles the headers, so no caller writes `X-API-Key`, `X-OpenViking-Account`, `X-OpenViking-User` or `V-Resource-Id` by hand.

| Field         | Header                 | Notes                                                                                                     |
| ------------- | ---------------------- | --------------------------------------------------------------------------------------------------------- |
| `baseURL`     | —                      | Service address with an optional gateway prefix, excluding `/api/v1`. Empty means same origin.            |
| `apiKey`      | `X-API-Key`            | Open-source edition. A function is re-evaluated per request, so it can read a rotated key.                |
| `accountId`   | `X-OpenViking-Account` | OpenViking account, which corresponds to the data space.                                                  |
| `userId`      | `X-OpenViking-User`    | User within the account.                                                                                  |
| `resourceId`  | `V-Resource-Id`        | Current library. Data-plane requests need it.                                                             |
| `credentials` | —                      | `RequestCredentials` for `fetch`. Defaults to `include`; override with `same-origin` or `omit` as needed. |
| `signal`      | —                      | The caller's `AbortSignal`.                                                                               |
| `fetch`       | —                      | Transport override, for tests or platforms without a global `fetch`.                                      |

An omitted or empty field sends no header. The commercial edition's control-plane requests need only the cookie, so it passes no `apiKey` and no identity headers there.

## Layering

A module or a single request may override individual fields. Resolution is field by field — the request's own value, then the Provider's, then the default — so a caller states only what changes:

```ts
await client.request({
  method: 'GET',
  path: '/api/v1/fs/ls',
  query: { uri },
  options: { resourceId: otherLibraryId },
});
```

`baseURL: ''` is an explicit "same origin", distinct from omitting the field.

## Calling it directly

`createOvClient` builds a client without React, for a host that has no Provider:

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

`request` returns the native response's `result`. `baseURL` is the effective address, for isolating caches and subscriptions.

## Failures

`OvClientError` is the single failure type. It separates an HTTP failure from a native `status: 'error'` response and keeps the server error code, status, details and request identifier. `code` is the server's when it sent one, and `HTTP_<status>` or `NETWORK_ERROR` when it did not. `isOvClientError` narrows an unknown value to it.

## Replacing the transport

`OvClient` is the interface modules depend on. Implement it to reuse the host's existing axios instance or the OpenAPI-generated SDK:

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

Whatever the transport, it owns three things and modules must not redo them: joining the service address with the path, turning identity fields into headers, and turning a response into either a result or an `OvClientError`.

## Development

From `frontend/`:

```bash
npm run build:lib
npm run typecheck
npm test
```

The client has no runtime dependencies and reaches for `fetch` only when a request is made, so importing it is safe during server rendering.
