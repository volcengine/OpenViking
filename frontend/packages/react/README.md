# @openviking/react

English / [中文](README_CN.md)

Public UI components, composed modules accept optional `locale: 'zh-CN' | 'en-US'`. Props override the nearest module or Provider; without a configured language, they use `en-US`. Existing `labels` override individual fields; omitted fields follow the current locale and update when it changes. Hosts still provide business content, slots and callback messages.

React foundations for OpenViking AI application interfaces: build tooling, documentation, layered directories, and scoped styles. FileTree, Tui and FilePreview provide working views, alongside five file-type icons. FileBrowser coordinates browsing, previews and dashboards; Console provides a native command panel; ScopeCascade provides an integrated cascade selector.

This package inherits the repository's AGPL-3.0-only license. The release workflow publishes it to npm when a `react@<version>` tag matches the version in this package's manifest.

## Module map

| Source directory                                                       | Public entry                                  | Responsibility                                                                                      |
| ---------------------------------------------------------------------- | --------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| [components/directory-tour](./src/components/directory-tour/README.md) | `@openviking/react/components/directory-tour` | Directory anchor walkthrough with asynchronous host resolution, independent highlights and recovery |
| [components/file-tree](./src/components/file-tree/README.md)           | `@openviking/react/components/file-tree`      | Directory tree rendering, expansion, selection and event callbacks                                  |
| [components/file-preview](./src/components/file-preview/README.md)     | `@openviking/react/components/file-preview`   | Read-only preview state and extension tabs; view is a placeholder                                   |
| [components/tui](./src/components/tui/README.md)                       | `@openviking/react/components/tui`            | Command record, result and input views with host-provided command metadata                          |
| [components/scope-switcher](./src/components/scope-switcher/README.md) | `@openviking/react/components/scope-switcher` | Stateless scope layout and independent ScopeSelector options, panels and search                     |
| [components/agent-connect](./src/components/agent-connect/README.md)   | `@openviking/react/components/agent-connect`  | Fixed Figma connection view with original icons, built-in copy and clipboard feedback               |
| [components/viking-bot](./src/components/viking-bot/README.md)         | `@openviking/react/components/viking-bot`     | Controlled conversation views, session controls, Markdown replies and composer                      |
| [modules](./src/modules/README.md)                                     | `@openviking/react/modules/<module>`          | Console panel and ScopeCascade Hooks and composition, plus FileBrowser and independent dashboards   |
| [client](./src/client/index.ts)                                        | `@openviking/react/client`                    | Framework-neutral request client: `createOvClient`, `OvClient`, `OvClientError`                     |
| [hooks](./src/hooks/README.md), [utils](./src/utils/README.md)         | Internal only                                 | Cross-module Hooks and pure utilities                                                               |

Components implement their reviewed contracts incrementally. FileTree renders every documented row type with keyboard interaction; FilePreview implements the tab shell, header actions and the plain-text preview and editing foundation, with its body not yet wired to FileViewer. ScopeSwitcher is a stateless layout container; independently rendered ScopeSelector children own visibility, with host-owned per-level data subscriptions, search and requests. Tui renders records, results and the input. AgentConnect follows the Figma layout with built-in bilingual agent and developer integration content, host-provided credentials and locale, optional controlled selection and clipboard feedback. VikingBot provides a working view pending design review. Limits are described in each component's own docs. The package also ships five file-type icons, `ArrowRightIcon` and the three scope-level icons (`LibraryIcon`, `SpaceIcon`, `UserIcon`).

Components receive view data and state through props and express user intent through callbacks, following the [component directory conventions](./src/components/AGENTS.md). [modules](./src/modules/README.md) plan Hooks and views per business capability: Hooks load data, hold state and expose operations, and composite views wire them into components. Full pages and product-specific flows belong to the host; the admin console is out of scope for this phase. `console` exports a native command panel; its orchestration Hook stays internal and conversation data and callbacks are supplied by the host. `file-browser` provides browsing, preview and dashboard coordination.

Modules consume the native OpenViking API through a shared Client by default, with the Provider supplying `baseURL`, headers and other configuration, and module views overriding individual fields through `requestOptions`. Standard integration needs no per-module Adapter; nonstandard gateways are adapted at the host's request boundary. The request contract — `OvRequestOptions`, `OvClient` and `OvClientError` — comes from the `@openviking/react/client` subpath, which ships a `fetch`-based `createOvClient`; the Provider is exported from the same subpath. Native module Hooks share this request boundary. ScopeCascade instead accepts three host API adapters and needs no Provider. See [request conventions](./src/modules/README.md#request-conventions-and-default-configuration).

Tui preserves JSON numeric tokens when indenting output. Structured resource links allow only `viking://`, `https://`, and `http://`; other targets render as text. Resource tables retain entry titles, native links and metadata. Output follows updates only near the bottom or after explicit submission. Read-only and disabled consoles cannot submit or insert help commands. `labels.resource` customizes the resource identity column.

### Props conventions

The same props are consumed by different hosts. Hosts differ in flows and interfaces while matching in interaction and UI, so:

- Components receive **already adapted data**: file entries, tabs and content sources are mapped into package types by module Hooks or the host, and components reference no host data model.
- State is expressed with controlled fields, and callbacks only report intent without returning business data.
- UI copy is built in for `locale: 'zh-CN' | 'en-US'`; existing labels override individual fields, and hosts may map i18next or Starling in their integration layer.
- Icons, extension tabs, extra fields and toolbar actions come in as slots or nodes; components bind to no third-party UI library.
- List components render the rows or items the caller has already expanded; paging, loading, error and empty states come from module Hooks or the host.

`WikiDashboard` is exported from `@openviking/react/components/wiki-dashboard`; `ExperienceDashboard` from `@openviking/react/components/experience-dashboard`. Both are pure UI components receiving display data and state; FileBrowser owns native loading and response adaptation. Components and main Props types also appear at the package root. FileBrowser detects Wiki/experience entries, with `dashboardKinds` and `renderDashboard` for host permissions and gateway data. Each component exports `style.css`; FileBrowser styles include both boards. See the [dashboard docs](../../docs/docs/en/components/index.mdx).

## Public entries and styles

- The root entry exports each component and each business module placeholder with its main Props type (`<Component>Props`) only — currently `FileTree`, `Tui`, `ScopeSwitcher`, `ScopeSelector`, `ScopeCascade`, `AgentConnect` and Console, FileBrowser, WikiDashboard and ExperienceDashboard. Row types, option types, handles and label bags come from the `components/<component>` and `modules/<module>` subpaths. `FilePreview` is not on the root entry: its types reference the optional `@ark-design/ui`, and putting it there would make that peer required. ScopeCascade exports a working three-level composition with internal per-level subscriptions from `modules/scope-cascade`; Console exports its panel from `modules/console` and keeps its orchestration Hook internal; FileBrowser provides browsing and dashboard coordination. No aggregate `@openviking/react/components` or `@openviking/react/modules` JavaScript entry. Package exports are listed explicitly and internal files stay closed.
- Components expose only the explicitly listed layered subpaths, with no legacy path aliases.
- JS entries share implementations and never load CSS implicitly. Whole-package, component-set and single-module stylesheets are built from the same source, so import the scope you need.

| Scope                  | CSS entry                                         |
| ---------------------- | ------------------------------------------------- |
| Whole package          | `@openviking/react/style.css`                     |
| Component set          | `@openviking/react/components/style.css`          |
| Single component       | `@openviking/react/components/<module>/style.css` |
| Business module set    | `@openviking/react/modules/style.css`             |
| Single business module | `@openviking/react/modules/<module>/style.css`    |

Each CSS entry works independently and includes the component styles it depends on. Business Hooks remain internal; no headless Hook entry is exposed.

```tsx
import { FileBrowser } from '@openviking/react/modules/file-browser';
import '@openviking/react/modules/file-browser/style.css';

<FileBrowser
  locale="en-US"
  labels={{ title: 'File browser', description: 'Placeholder component.' }}
/>;
```

Console accepts its documented panel props; native command execution uses Provider request configuration and starts only on explicit submission.

Experience dashboards belong to preview extensions; task progress starts inside its owning module; API Key display controls are reused on demand while credential creation and management belong to the host. No separate modules are added for these now.

## Icons

```tsx
import { PdfIcon } from '@openviking/react/icons';

<PdfIcon width={20} height={20} className="file-icon" alt="PDF" />;
```

Icons render as inline SVG. Decorative icons stay `aria-hidden`, while an icon that conveys meaning on its own can take an `aria-label`. SVGs are inlined into the build output, and hosts consume them directly from the package exports. `LibraryIcon`, `SpaceIcon` and `UserIcon` are the scope-level defaults; `ScopeCascade`'s default view applies them automatically when an adapter returns no `icon`.

React and ReactDOM are peer dependencies (`^18.2.0 || ^19.0.0`), automatically externalized by Rslib. Rslib emits shared ESM JavaScript and declarations, plus independently built CSS entries. The public `@openviking/react/style.css` entry resolves to `dist/style.css`. Styles are scoped and do not reset application styles.

From `frontend/`, run `npm run build:lib` to generate `packages/react/dist/`. See the [workspace README](../../README.md) for documentation development and deployment.

### ScopeCascade

Pass three stable API adapters directly to `<ScopeCascade {...api} onChange={handleSelection} />`. Adapters return `{ id, label }` options and receive an `AbortSignal`. The component owns requests, descendant resets, loading, retries and stale-response protection. `onChange` reports a complete `{ libraryId, spaceId, userId }` identity or `null` while incomplete. Import the component and Props from `@openviking/react/modules/scope-cascade` and its CSS from the corresponding `/style.css` entry.

## Maintaining built-in copy

Built-in translations live alongside each component or module in `locales/en-US.ts` and `locales/zh-CN.ts`. The internal `src/shared/locale.ts` contains only shared language types and resolution/override rules, with no business messages or public entry. Modules own workflow copy and reuse the defaults owned by their child components.

Dynamic messages use typed formatter functions; dates and numbers use `Intl`. `npm test` checks matching keys, string placeholders and formatter arity across languages, while type checking validates parameter types. Undefined `labels` entries inherit the selected language; empty strings remain explicit overrides.

Set `locale` on `OpenVikingProvider` for a subtree. Optional module and component Props override the nearest scope, then fall back to `en-US`. Standalone Hooks also inherit the Provider. The private `src/shared/locale-context.tsx` is shared across public entries and remains separate from request-client configuration, so language changes preserve requests and state.
