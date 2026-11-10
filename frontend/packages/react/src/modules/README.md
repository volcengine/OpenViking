# Business modules

English / [中文](README_CN.md)

`modules/` organises Hooks and composite views around interactions that are completed together, keeping shared state and coordination inside the module. Hooks load requests, adapt data and hold state; composite views wire the data and operations a Hook returns into `components/`. The host assembles full pages.

## Placeholder components available today

`file-browser` is implemented: `useFileBrowser` loads the data and coordinates the directory, the search and the preview, and the `FileBrowser` composite view renders the tree and the preview. `console` is still a placeholder that renders only a module name and placeholder copy; its business Hook and interactions are still to come.

Because a module loads its own data, no prop carries data. A host supplies configuration, the controlled value and the copy; the module reports changes through callbacks.

| Module         | Component     | Public entry                             |
| -------------- | ------------- | ---------------------------------------- |
| `file-browser` | `FileBrowser` | `@openviking/react/modules/file-browser` |
| `console`      | `Console`     | `@openviking/react/modules/console`      |

```tsx
import { FileBrowser } from '@openviking/react/modules/file-browser';
import '@openviking/react/modules/file-browser/style.css';

<FileBrowser labels={{ title: 'File browser' }} />;
```

The two components and their main Props types are also exported from the `@openviking/react` root entry; Labels, Slots and the enum types come from the module subpath. Runnable examples and property descriptions are in the [module docs](../../../../docs/docs/en/modules/index.mdx). The sections below describe the target architecture and capability still to be migrated.

## Responsibilities

- Module Hooks call the native open-source API through the shared Client by default, inherit the Provider's request configuration, and manage loading, empty, error, paging, refresh, retry and any connection cleanup.
- Hooks return the data, state and operations a view can consume; composite views wire those capabilities into components and coordinate interactions inside the module.
- Hosts can use a composite view directly, or reuse only the module Hook and replace the view or rearrange the components.
- Standard integration configures only a service address and credentials, with no per-module data Adapter required; nonstandard gateways are adapted at the host's request boundary. Authentication, account scope, routing, permission enforcement, page layout and product-specific flows remain with the host.

The dependency direction is `modules → components`. Components still own only view and interaction and remain usable without any business module. Module-specific Hooks, types, utilities and private views stay in the module directory; cross-module shared Hooks move to `hooks/` only once real reuse appears.

## Layers of the personal terminal

Shared capability is planned for the personal terminal, with the admin console out of scope for this phase. The layers express responsibility and dependency direction and do not correspond to new npm packages:

| Layer                    | Responsibility                                                                                                                                                  |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Host application         | Page layout, routing, sign-in and accounts, current library / data space / user, permission and capability configuration, and coordination between modules      |
| `modules/`               | One independently integrable business capability: data-loading Hook, business state and operations, replaceable composite view                                  |
| `components/`            | Views and interaction reused through props and events, loading no business data themselves                                                                      |
| Services and foundations | Native open-source API Client, resource types and pure data transforms, placed in `client/`, `lib/` and similar boundaries according to the real implementation |

Hosts can use components alone, reuse module Hooks and assemble the view themselves, or use the composite views the modules provide. Each business module owns one directory, with granularity set by an independently integrable interaction: directory, search and preview make up `file-browser`; conversation and TUI make up `console`, switching modes inside the module. Hosts compose these two areas and the other modules and do not integrate the internal views separately.

## Request conventions and default configuration

The shared Client takes the native OpenViking API's method, path, query / body and response DTOs as its contract, maintained in `src/client/` and exported from `@openviking/react/client`. It ships a `fetch`-based `createOvClient` plus the contract modules depend on — request shape, configuration resolution, `OvClient` and `OvClientError`. The code has no React dependency, so it can move to its own package once a consumer outside React appears. Module Hooks consume native data and derive the state and operations a view needs, so hosts need not maintain a data protocol per module.

React integration is provided by `OpenVikingProvider` from `@openviking/react/client`, which distributes common configuration through Context and reuses a stable Client within each Provider instance. A host passes identity values — `apiKey`, `accountId`, `userId`, `resourceId` — and the Client assembles the headers; modules read the client through an internal hook and never build a request themselves. The Context does not cache business data; mounting a Provider fetches nothing and reads no credentials saved in the browser.

| Configuration | Convention                                                                                                                                                                                                     |
| ------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `baseURL`     | Service address with an optional gateway prefix, excluding the native `/api/v1`; defaults to an empty string meaning a same-origin browser request. Server-side requests supply an absolute address explicitly |
| `apiKey`      | The credential. In the open-source edition it becomes `X-API-Key`; the commercial edition carries it in the browser cookie. A function is re-evaluated per request                                             |
| `accountId`   | OpenViking account, which corresponds to the data space                                                                                                                                                        |
| `userId`      | User within the account                                                                                                                                                                                        |
| `resourceId`  | Current library; data-plane requests need it                                                                                                                                                                   |
| `credentials` | `RequestCredentials` for `fetch`. Defaults to `same-origin`; the commercial edition sets `include` for cross-origin calls                                                                                      |

Module components and standalone Hooks share one optional `requestOptions` override type, `OvRequestOptions` from `@openviking/react/client`, resolved field by field: **explicit module / Hook configuration → nearest Provider configuration → library default**. A nested Provider overrides its parent. An omitted field or `undefined` means inherit, while `baseURL: ''` explicitly returns to same-origin; an identity field omitted in the override inherits the Provider's value, and an explicit empty string clears it. Overrides never modify the parent Provider and do not affect other module instances. Most integrations configure the Provider once. The `FileBrowserProps` and `ConsoleProps` types already declare this field.

The Provider is implemented; the business Hooks are not, so a module does not request anything yet. The host configures it once at the application root:

```tsx
import { OpenVikingProvider } from '@openviking/react/client';

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

Inside a module, the client comes from an internal hook. A module passes only the fields it needs to override, for example when it addresses another library:

```ts
const rows = await client.request<FsEntry[]>({
  method: 'GET',
  path: OV_PATHS.fsLs,
  query: { uri },
  options: { resourceId: libraryId },
});
```

That hook is not exported: a host never needs to know which requests a module sends. Pure view components need no Provider at all. The current identity may be distributed through Context, where `accountId` corresponds to the data space and `userId` to the user within it; identity scope and request configuration are maintained separately. Business parameters such as URI and query text are still passed as module / Hook inputs.

- **Paths are joined in one place only**: endpoint paths live in `OV_PATHS` / `OV_PATH_BUILDERS` from `@openviking/react/client`, so modules write no literals. The Client keeps the `baseURL` path prefix. For example `/gateway/openviking` plus `/api/v1/fs/ls` yields `/gateway/openviking/api/v1/fs/ls`, and a leading `/` in an API path never drops the gateway prefix. Modules join no domain or service prefix.
- **Responses are parsed only in the Client**: HTTP failures are distinguished from a native `status: 'error'` response, preserving the server error code, status and request identifier. `request` returns `result` by default; binary responses use `responseType: 'blob'` for the raw bytes and event streams use `stream`, and neither is coerced to JSON. Pagination metadata outside `result`, such as `has_more`, is not returned today, so a module that pages decides through `offset` / `node_limit`.
- **Configuration and identity changes must be isolated**: requests, cache keys and subscriptions are isolated by the effective service address and the host's current connection / identity scope. Switching service or account cancels old requests and ignores stale results; dynamic headers do not substitute for an identity-switch signal, and an API Key is never put into a cache key in plain text.
- **Adaptation is centralised at the integration boundary**: native APIs need only an address and headers; any extra wrapping for existing gateways is adapted uniformly by the host at the shared Client boundary, preserving cancellation signals, response status and streams. Capabilities that cannot be expressed equivalently are integrated through an explicit extension of the contract, not a per-module host branch.

Provider inheritance follows [React Context](https://react.dev/reference/react/useContext); the request layer migration references the [existing Client wrapper](../../../../../web-studio/src/shared/api/ov-client/client.ts). The transport and the Provider follow these conventions; they are verified again when the first module Hook is implemented.

## Business module breakdown

The two module directories and their placeholder components exist; below are the target capabilities each will take on, none implemented yet. The components these composite views build on are also placeholders today, so a module's real implementation depends on those views landing first. Product onboarding, the resource and Skill import flows and sync configuration belong to the host, with no corresponding business module.

| Module         | Hook and business responsibility                                                                                                                                              | Composite view                                                                                                           |
| -------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `file-browser` | Manages directory, retrieval, current URI, preview loading and edit saving; coordinates positioning, refresh, history and version comparison                                  | Composes `FileTree`, the search interaction and `FilePreview` into a file browsing area that links directory and content |
| `console`      | Manages conversation / TUI mode, common context and toolbar; handles conversation sessions, message streams, attachments, memory submission and command execution and records | Composes the module's own conversation view, `Tui` and mode switching into a complete interaction panel                  |

History lists and version comparison start as preview capabilities of `file-browser`, and compile confirmation as a Bot action flow in `console`; they become public modules only once an independent consumer appears, and no module is created per button or dialog. Public view components are added when real reuse appears, and the composite views in the table do not require new component placeholders ahead of time.

### File browser module

`file-browser` is planned to cover the whole linked area of directory navigation, search and content preview, providing a unified `useFileBrowser` Hook and a `FileBrowser` composite view:

- The Hook returns the data, state and operations for the directory, search, current resource and preview together. Private Hooks may be split out internally, and a host may use only the unified Hook and assemble the view itself.
- Clicking a directory node or a search result makes the module coordinate the current URI, directory positioning and preview loading; switching resources clears stale requests so outdated content cannot overwrite the current preview.
- After saving, renaming or deleting a resource, the module refreshes the affected directory, search results and preview; deleting the current resource clears the selection and content at the same time.
- The composite view owns the search, directory and preview layout within the area. Pure view components such as `FileTree` and `FilePreview` stay independent and take on no data loading or cross-component coordination.
- Entries such as connecting an agent or importing resources come in through host callbacks or slots; the module embeds no connection dialog, import flow or product onboarding. Product content such as quota hints also comes in through host slots.

The interactions above are implemented: `useFileBrowser` loads the data and coordinates the directory, the search and the preview, and the `FileBrowser` composite view lays them out as the console's context panel does.

### Interaction panel module

`console` is planned to cover the whole panel shared by the conversation view and TUI, providing a unified `useConsole` Hook and a `Console` composite view:

- The Hook returns the current mode, common context, conversation and command data, state and operations together; mode switching and toolbar actions are coordinated inside the module, and a host may also use only the Hook and assemble the view.
- The conversation view and TUI use the same service, identity and host-supplied resource context; each keeps its own message history, command records and running state, preserved when switching modes.
- Conversation capabilities include sessions, message streams, attachments, memory submission and compile actions; TUI capabilities include command definitions, execution, cancellation and execution records, with available commands following the capabilities the host provides.
- The composite view owns mode switching, the common toolbar and the content area, dispatching operations by the current mode. `Tui` is consumed as an independently usable pure view component; the conversation view has no shared component yet and is implemented inside the module.
- Opening a resource, locating an artifact or refreshing a directory is handed to the host through events, and coordination with `file-browser` does not depend on the other module's private state; sign-in, identity switching and product onboarding remain with the host.

The interactions above and `useConsole` are still planned; only the `Console` placeholder component and base types are exported today.

## Composition and extension boundaries

- **The file browser module coordinates the current resource**: `file-browser` maintains the selected URI by default and keeps directory, search and preview in sync. A host can pass the URI in a controlled way, or open task artifacts and Bot links through the module's public operations; the module reports resource changes and never manipulates another module's Store or routing directly.
- **Preview tabs belong to the same resource**: the personal terminal does not keep workspace tabs for multiple open files. L0 / L1 / directory content and experience dashboards are content views of the current resource; the generic `FilePreview` tab API is not narrowed on that basis.
- **Specific content arrives through extensions**: experience dashboards, session content and Wiki views have their own loading and display logic and are integrated through preview extension tabs, staying in business extensions or the host; the generic `FilePreview` is never taught these business types. Skill browsing reuses the directory and its content.
- **Task state stays with the task data source**: the console does not report task progress or resource changes, because it cannot observe work that finishes after the request returns. A host that needs to react to a task reads it from the task data source itself; task state is not duplicated across modules.
- **Identity comes from the host**: switching library / data space / user and switching sign-in accounts are different operations. The scope-switching view is provided by the host; sign-in, account storage, API Key management and expiry handling remain with the host. Modules isolate requests, caches and subscriptions by explicit scope and clear old operations after a switch.
- **Optional capabilities are integrated by the host**: delete, OAuth, OVPack, sync sources, privacy and overseas notices are supplied by the host as service capabilities, actions and copy. OVPack uses a separate directory-migration service contract and is not mixed into ordinary resource upload; it can extend directory operations. TOS import, hand-written Skills, a standalone compile page and admin console modules are not planned for this phase.
- **Layout follows module boundaries**: `file-browser` owns the internal layout of directory, search and preview, and `console` owns conversation / TUI mode switching and the panel's internal layout; hosts can customise them through slots or standalone Hooks. The outer layout of both areas is the host's decision, as is the placement of top-bar entries such as onboarding, tasks, help and account.

## Current state and development entry

Each module uses its own kebab-case directory with `index.ts` as the single entry, and already ships a placeholder component, base types and an independent build entry. Once real business logic migrates, the Hook, the composite view and public types are exported from the same entry; no placeholder Hook or request-configuration API is provided today.

Development commands and build entries are in the [UI workspace README](../../../../README.md), the package structure is in the [package README](../../README.md), and implementation constraints are in this directory's [AGENTS.md](./AGENTS.md).

`@openviking/react/modules/<module>` is open, with no category-level aggregate JS entry. Module CSS provides both `@openviking/react/modules/style.css` and `@openviking/react/modules/<module>/style.css`, and the whole-package `@openviking/react/style.css` includes module styles too. Single-module CSS currently contains the placeholder view styles and must include the component styles it needs once the composite view migrates; using a Hook alone needs no CSS.
