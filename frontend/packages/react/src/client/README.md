# Client

English / [中文](README_CN.md)

The OpenViking request client, exported from `@openviking/react/client`: a `fetch`-based client for the native OpenViking API, and `OpenVikingProvider`, which hands its configuration to the blocks.

## Using it

Configure the Provider once at the application root. You pass values, never headers:

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

The open-source edition also passes `apiKey`; the commercial edition authenticates with the browser cookie and omits it. `credentials` defaults to `include`, including for cross-origin requests, and server CORS and browser cookie policies still apply.

Mounting the Provider sends no request and reads no credential stored in the browser. Its `locale` sets the interface language of the subtree, see [Internationalization](../../../../docs/docs/en/guide/i18n.mdx).

## Configuration fields

Every field is a value. The Client assembles the headers, so you never write `X-API-Key`, `X-OpenViking-Account`, `X-OpenViking-User` or `V-Resource-Id` by hand.

| Field         | Header                 | Description                                                                                                                        |
| ------------- | ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `baseURL`     | -                      | Service address with an optional gateway prefix, excluding `/api/v1`. Empty means same origin.                                     |
| `apiKey`      | `X-API-Key`            | Credential of the open-source edition.                                                                                             |
| `accountId`   | `X-OpenViking-Account` | OpenViking account ID, which corresponds to the data space.                                                                        |
| `userId`      | `X-OpenViking-User`    | User ID within the account.                                                                                                        |
| `resourceId`  | `V-Resource-Id`        | Current library ID. Data-plane requests need it.                                                                                   |
| `signal`      | -                      | `AbortSignal` that aborts the requests.                                                                                            |
| `credentials` | -                      | `RequestCredentials` for `fetch`. Defaults to `include`; override with `same-origin` or `omit`. Set on the Provider or the client. |
| `fetch`       | -                      | Replacement for the global `fetch`, for tests, tracing or platforms without one. Set on the Provider or the client.                |

The four identity fields also accept a function that returns a string or a Promise of one. It is evaluated once per request, so it can read a rotated credential. An omitted or empty identity field sends no header.

## Layering

A block or a single request can override individual fields (`baseURL`, the identity fields and `signal`). Resolution is field by field: the request's own value, then the nearest Provider's, then the default. A nested Provider inherits the fields it omits. You state only what changes:

```ts
await client.request({
  method: 'GET',
  path: OV_PATHS.fsLs,
  query: { uri },
  options: { resourceId: otherLibraryId },
});
```

`baseURL: ''` means "same origin" explicitly, which differs from omitting the field. An empty string in an identity field clears the value inherited from the layer above.

## Calling it directly

`createOvClient` builds a client without `OpenVikingProvider`, for code outside the React tree:

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

`request` returns the native response's `result`. `client.baseURL` is the address the client was created with, which suits a cache or subscription key.

A listing puts `has_more` outside `result`. `requestEnvelope` returns both as `{ result, hasMore }`, and `hasMore` is `true` only when the response sets `has_more: true`:

```ts
const { result, hasMore } = await client.requestEnvelope<{ uri: string }[]>({
  method: 'GET',
  path: OV_PATHS.fsLs,
  query: { uri, node_limit: 200, offset: 0 },
});
```

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

A failed response is still a JSON envelope, so its error code stays on `OvClientError.code`. A download request carries no `body`, so no `content-type: application/json` is sent. `requestEnvelope` does not accept `responseType: 'blob'`.

## Event streams

`stream` opens an SSE connection and yields events to a `for await` loop:

```ts
const stream = await client.stream({
  method: 'POST',
  path: OV_PATHS.botChatStream,
  body: { message, session_id },
});

for await (const event of stream) {
  // event.data is raw text; the event type lives inside the JSON
}

stream.abort();
```

- `accept: text/event-stream` is sent by default; `headers` adds or overrides, while identity headers are still assembled by the Client and win on a name clash.
- `body` accepts any fields and is sent as given, so server-defined options pass through.
- `data` stays verbatim and is not JSON-parsed: OpenViking puts the event type and event id inside the `data` JSON rather than in the SSE `event` / `id` fields.
- A comment-only heartbeat block (`: keepalive`) produces no event; a trailing block without its blank line is still dispatched.
- `stream` resolves as soon as the response arrives. An HTTP failure, a non-event-stream `content-type`, and a `status: 'error'` envelope in a non-stream response all throw `OvClientError` before it resolves.
- After `abort()` or a cancellation through `signal`, the iteration ends quietly instead of throwing `AbortError`: cancellation is the caller's intent, not a failure.
- A stream can be iterated once.

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

`OV_PATHS` is an `as const` object, not an `enum`, so consumers that enable `isolatedModules` or `verbatimModuleSyntax` can still compile against it.

## Failures

`OvClientError` is the single failure type of `request`, `requestEnvelope` and `stream`. It tells an HTTP failure apart from a native `status: 'error'` response and keeps the server's error code, `statusCode`, `details`, `requestId` and `responseBody`. `isOvClientError` narrows an unknown value to it.

`code` is the server's when it sent one. Otherwise it is one of:

- `HTTP_<status>`: an HTTP failure.
- `UNKNOWN`: a `status: 'error'` response without a code.
- `NETWORK_ERROR`: the transport failed.
- `UNEXPECTED_CONTENT_TYPE`: `stream` received something other than an event stream.
- `INVALID_REQUEST`: `requestEnvelope` was asked for a `blob`.

Cancelling a request through `signal` is not a client failure: `request` rejects with the original `AbortError`.

## Replacing the transport

The default transport calls the global `fetch`. To send requests through your own function, pass `fetch` to `OpenVikingProvider` or `createOvClient`, for example to add tracing or to return fixed responses in tests:

```tsx
const fixedFetch: typeof fetch = async () =>
  new Response(JSON.stringify({ result: [] }), {
    headers: { 'content-type': 'application/json' },
  });

<OpenVikingProvider fetch={fixedFetch}>
  <FileBrowser />
</OpenVikingProvider>;
```

`createOvClient` returns an `OvClient` with `request`, `requestEnvelope`, `stream` and `baseURL`, the interface blocks depend on. Blocks always use the client created by their nearest Provider, and the Provider takes no client of your own, so `fetch` is how you change the transport for them. Whatever the transport, the Client owns three things and blocks do not repeat them: joining the service address with the path, turning identity fields into headers, and turning a response into a result or an `OvClientError`.

## Browser runtime

The client runs in the browser. `createOvClient` needs a `fetch`, either the global one or the one you pass, and throws without one.

The client tests are in `packages/react/test/client*.test.mjs`; run them with `npm test` from `frontend/` (see the [workspace README](../../../../README.md)).
