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

The open-source edition adds `apiKey`; the commercial edition authenticates with the browser cookie, so it omits `apiKey` and sets `credentials: 'include'` for cross-origin calls.

Mounting the Provider sends no request and reads no credential stored in the browser.

## Configuration fields

Every field is a value. The Client assembles the headers, so no caller writes `X-API-Key`, `X-OpenViking-Account`, `X-OpenViking-User` or `V-Resource-Id` by hand.

| Field         | Header                 | Notes                                                                                                |
| ------------- | ---------------------- | ---------------------------------------------------------------------------------------------------- |
| `baseURL`     | —                      | Service address with an optional gateway prefix, excluding `/api/v1`. Empty means same origin.       |
| `apiKey`      | `X-API-Key`            | Open-source edition. A function is re-evaluated per request, so it can read a rotated key.           |
| `accountId`   | `X-OpenViking-Account` | OpenViking account, which corresponds to the data space.                                             |
| `userId`      | `X-OpenViking-User`    | User within the account.                                                                             |
| `resourceId`  | `V-Resource-Id`        | Current library. Data-plane requests need it.                                                        |
| `credentials` | —                      | `RequestCredentials` for `fetch`. Defaults to `same-origin`; the commercial edition needs `include`. |
| `signal`      | —                      | The caller's `AbortSignal`.                                                                          |
| `fetch`       | —                      | Transport override, for tests or platforms without a global `fetch`.                                 |

An omitted or empty field sends no header. The commercial edition's control-plane requests need only the cookie, so it passes no `apiKey` and no identity headers there.

## Layering

A module or a single request may override individual fields. Resolution is field by field — the request's own value, then the Provider's, then the default — so a caller states only what changes:

```ts
await client.request({
  method: 'GET',
  path: OV_PATHS.fsLs,
  query: { uri },
  options: { resourceId: otherLibraryId },
});
```

`baseURL: ''` is an explicit "same origin", distinct from omitting the field.

## Calling it directly

`createOvClient` builds a client without React, for a host that has no Provider:

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

`request` returns the native response's `result`. `baseURL` is the effective address, for isolating caches and subscriptions.

## Binary responses

`responseType: 'blob'` skips envelope parsing and returns the raw bytes. Previewing images and downloading files both take this path:

```ts
const blob = await client.request<Blob>({
  method: 'GET',
  path: OV_PATHS.contentDownload,
  query: { uri },
  responseType: 'blob',
});
```

A failed response is still a JSON envelope, so its error code stays on `OvClientError.code`. A binary response has no body, so no `content-type: application/json` is sent.

## Event streams

`stream` opens an SSE connection and yields events to a `for await` loop:

```ts
const stream = await client.stream({
  method: 'POST',
  path: '/bot/v1/chat/stream',
  body: { message, session_id },
});

for await (const event of stream) {
  // event.data is raw text; the event type lives inside the JSON
}

stream.abort();
```

- `accept: text/event-stream` is sent by default; `headers` adds or overrides, while identity headers are still assembled by the Client and win on a name clash.
- `body` accepts any fields, so a server-defined switch such as `Attach` — subscribe to an existing session only — is forwarded as-is.
- `data` stays verbatim and is not JSON-parsed: OpenViking puts the event type and event id inside the `data` JSON rather than in the SSE `event` / `id` fields.
- A comment-only heartbeat block (`: keepalive`) produces no event; a trailing block without its blank line is still dispatched.
- It resolves as soon as the response arrives. An HTTP failure, a non-event-stream `content-type`, and a `status: 'error'` envelope in a non-stream response all throw `OvClientError` before it resolves.
- After `abort()` or a caller-signal cancellation the iteration ends quietly instead of throwing `AbortError`: cancellation is the caller's intent, not a failure.

## API paths

`OV_PATHS` keeps the native endpoint paths in one place, so a rename is one edit. Endpoints with a path parameter go through `OV_PATH_BUILDERS`, which URL-encodes the value:

```ts
import { OV_PATHS, OV_PATH_BUILDERS } from '@openviking/react/client';

await client.request({ method: 'GET', path: OV_PATHS.fsLs, query: { uri } });
await client.request({
  method: 'POST',
  path: OV_PATH_BUILDERS.sessionMessages(sessionId),
  body: { role: 'user', content },
});
```

`OV_PATHS` is an `as const` object rather than an `enum`: a string enum is not inlined under `isolatedModules`, its emitted object counts as a side effect, and as a public export it is an ambient const enum that consumers with `isolatedModules` or `verbatimModuleSyntax` cannot compile against.

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

Whatever the transport, it owns three things and modules must not redo them: joining the service address with the path, turning identity fields into headers, and turning a response into either a result or an `OvClientError`. `OvClient` also has `stream`, which a custom transport must provide too; the event-stream framing follows the parser's approach, but the parser stays inside the package and is not exported on its own.

## Development

From `frontend/`:

```bash
npm run build:lib
npm run typecheck
npm test
```

The client has no runtime dependencies and reaches for `fetch` only when a request is made, so importing it is safe during server rendering.
