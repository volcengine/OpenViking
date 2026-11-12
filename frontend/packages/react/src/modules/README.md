# Blocks

English / [中文](README_CN.md)

`modules/` holds the blocks. Each one pairs internal Hooks, which load data, adapt it and hold state, with a composite component that wires the Hooks into views from `components/`. Host apps use the composite component and assemble full pages themselves.

## Blocks

| Block           | Component      | Public entry                              | Combines                                                                     |
| --------------- | -------------- | ----------------------------------------- | ---------------------------------------------------------------------------- |
| `file-browser`  | `FileBrowser`  | `@openviking/react/modules/file-browser`  | Directory tree, search and file preview around one current resource          |
| `console`       | `Console`      | `@openviking/react/modules/console`       | Conversation (`VikingBot`) and command (`CommandConsole`) modes in one panel |
| `scope-cascade` | `ScopeCascade` | `@openviking/react/modules/scope-cascade` | Library, space and user selectors that cascade                               |

Each block also has a stylesheet entry, `@openviking/react/modules/<block>/style.css`, which includes the styles of the components it composes. Usage, props and examples are in the [block docs](../../../../docs/docs/en/modules/index.mdx).

## Responsibilities

- `useFileBrowser` and `useConsole` are internal. They call the native OpenViking API through the shared Client, inherit the Provider's request configuration, and manage loading, empty and error states, paging, refresh, retry and the cleanup of requests still in flight. `ScopeCascade` sends no request itself: its three loader functions come from the host. `Console` sends only command requests; its conversation view takes data and callbacks from the host through the `conversation` prop.
- A composite component hands the Hook's data and operations to `components/` views and coordinates the interactions inside the block.
- Hosts use the composite component directly. No headless Hook or controller is exported.
- Standard integration needs only a service address and credentials, with no per-block data adapter. A host adapts a nonstandard gateway once, at its request boundary. Sign-in, account scope, routing, permission enforcement, page layout and product-specific flows stay with the host.
- Block granularity follows interactions that are completed together: directory, search and preview make up `file-browser`; conversation and commands make up `console`. A host composes blocks, not their internal views.

Dependencies run one way: `modules/` → `components/` → `shared/`, and `modules/` → `client/`. A component is a pure view that takes data, state and callbacks, so it fetches nothing and imports nothing from `modules/`. Hooks, types and utilities that one block uses stay in its directory; code that several blocks use moves to `src/shared/`.

## Request conventions and default configuration

The shared Client sends requests to the native OpenViking API by method, path, query and body, and parses the native response envelope. It lives in `src/client/` and is exported from `@openviking/react/client`: the `fetch`-based `createOvClient`, the `OvClient` interface, `OvRequestOptions`, `OvClientError` and the path tables `OV_PATHS` and `OV_PATH_BUILDERS`. Only `OpenVikingProvider` in that directory uses React. Block Hooks consume native data and derive the state and operations a view needs, so a host maintains no data protocol per block. The [Client README](../client/README.md) documents the client itself.

`OpenVikingProvider` distributes common configuration through Context and keeps one stable Client per Provider instance. A host passes values (`baseURL`, `apiKey`, `accountId`, `userId`, `resourceId`, `credentials`) and the Client assembles the headers; blocks take the client from an internal hook and never build requests themselves. The Context caches no business data. Mounting a Provider sends no request and reads no credential stored in the browser. Each field and its header are listed under [configuration fields](../client/README.md#configuration-fields) in the Client README.

`FileBrowserProps` and `ConsoleProps` declare one optional `requestOptions` override of type `OvRequestOptions`, resolved field by field: **explicit block configuration → nearest Provider configuration → library default**. A nested Provider overrides its parent only in the fields it declares. An omitted field or `undefined` inherits, and `baseURL: ''` explicitly returns to same origin. An identity field omitted in the override inherits the Provider's value, while an explicit empty string clears it. An override never modifies the parent Provider or other block instances. Most integrations configure the Provider once. `ScopeCascade` takes no `requestOptions`: it works through the three loaders the host passes.

A host configures the Provider once at the application root:

```tsx
import { OpenVikingProvider } from '@openviking/react/client';
import { Console } from '@openviking/react/modules/console';
import { FileBrowser } from '@openviking/react/modules/file-browser';

<OpenVikingProvider
  baseURL={endpoint}
  apiKey={apiKey}
  accountId={accountId}
  userId={userId}
  resourceId={libraryId}
>
  <FileBrowser />
  <Console />
</OpenVikingProvider>;
```

Inside a block, the client comes from an internal hook. A block passes only the fields it needs to override, for example when it addresses another library:

```ts
const rows = await client.request<FsEntry[]>({
  method: 'GET',
  path: OV_PATHS.fsLs,
  query: { uri },
  options: { resourceId: libraryId },
});
```

That hook is not exported: a host never needs to know which requests a block sends. Components under `components/` need no Provider. Business parameters such as a URI or query text are props of the block, not Provider configuration.

- **Paths are joined in one place.** Endpoint paths live in `OV_PATHS` and `OV_PATH_BUILDERS`, so blocks write no literals, and the Client keeps the path prefix of `baseURL`: `/gateway/openviking` plus `/api/v1/fs/ls` gives `/gateway/openviking/api/v1/fs/ls`, and a leading `/` in an API path never drops the gateway prefix. Blocks join no domain or service prefix.
- **Responses are parsed only in the Client.** An HTTP failure and a native `status: 'error'` response both raise `OvClientError`, which keeps the server's error code, status and request ID. `request` returns `result`, and `requestEnvelope` returns `{ result, hasMore }` for paged listings; FileBrowser pages directories with it through `node_limit` and `offset`. `responseType: 'blob'` returns raw bytes and `stream` yields event-stream events; neither is coerced to JSON.
- **Identity and configuration changes are isolated.** A block treats a new client (a changed Provider configuration), a changed `requestOptions` field and, for FileBrowser, a changed `rootUri` as a new scope: it aborts requests still running and discards their late results, so a response for the old account never lands in the new view. An identity passed as a function cannot announce its own change. FileBrowser tells such functions apart by reference, and Console takes an explicit `scopeKey` that you change together with the identity.
- **Adaptation sits at the integration boundary.** The native API needs only an address and headers. A host adapts an existing gateway once, at the shared Client boundary, keeping cancellation signals, response status and streams. A capability that cannot be expressed equivalently is added by extending the contract, not by a per-block host branch.

## Block boundaries

- **FileBrowser coordinates the current resource.** It keeps the current URI in sync across the directory, search and preview. Pass `uri` and `onUriChange` to control the URI from the host, for example to open a link that `Console` reports through `onOpenUri`. A block reports changes and never reaches into another block's state or the router.
- **Identity comes from the host.** Switching library, space or user is a different operation from switching the signed-in account. `ScopeCascade` provides the library, space and user selection; sign-in, account storage, API Key management and expiry handling stay with the host.
- **Dashboards are components.** `wiki-dashboard` and `experience-dashboard` are pure views under `components/`. FileBrowser's private Hooks load native data, adapt it to the dashboards' display models and render them. `dashboardKinds` limits which kinds FileBrowser detects, and `renderDashboard` replaces the built-in view with the host's own; a nullish return keeps the built-in view.
- **Layout.** Each block lays out its own internal areas, and hosts customize them through props and slots. The outer layout and where a block sits in the page are the host's decision.

## Adding or changing a block

Each block has a kebab-case directory whose entry file has the same name. Its `index.ts` exports the composite component and the types hosts need, and keeps Hooks internal. Add the public JS and CSS entries to `exports` in `package.json` and to `rslib.config.ts`, and import the block's `style.css` from `modules/style.css`. The block's CSS includes the CSS of the components it composes. There is no JavaScript entry that aggregates `modules/`.

The steps, commands and CI are in the [workspace README](../../../../README.md), the package entries are in the [package README](../../README.md), and the implementation rules are in this directory's [AGENTS.md](./AGENTS.md).
