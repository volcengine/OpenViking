# @openviking/react

English / [中文](README_CN.md)

React foundations for OpenViking AI application interfaces: build tooling, documentation, layered directories, and scoped styles. FileTree and FilePreview provide placeholder views, alongside five file-type icons. Four business module placeholders support early layout integration; business Hooks and interactions will follow.

This package inherits the repository's AGPL-3.0-only license. The release workflow publishes it to npm when a `react@<version>` tag matches the version in this package's manifest.

## Module map

| Source directory                                                       | Public entry                                  | Responsibility                                                                            |
| ---------------------------------------------------------------------- | --------------------------------------------- | ----------------------------------------------------------------------------------------- |
| [components/file-tree](./src/components/file-tree/README.md)           | `@openviking/react/components/file-tree`      | Directory tree rendering, expansion, selection and event callbacks; view is a placeholder |
| [components/file-preview](./src/components/file-preview/README.md)     | `@openviking/react/components/file-preview`   | Read-only preview state and extension tabs; view is a placeholder                         |
| [components/tui](./src/components/tui/README.md)                       | `@openviking/react/components/tui`            | Reviewed command record, result and input types; view is a placeholder                    |
| [components/scope-switcher](./src/components/scope-switcher/README.md) | `@openviking/react/components/scope-switcher` | Scope breadcrumb level, option, panel and search contract; view is a placeholder          |
| [components/agent-connect](./src/components/agent-connect/README.md)   | `@openviking/react/components/agent-connect`  | Agent connection tab, list and detail contract; view is a placeholder                     |
| [modules](./src/modules/README.md)                                     | `@openviking/react/modules/<module>`          | Placeholder components and types for four business modules                                |
| [client](./src/client/index.ts)                                        | `@openviking/react/client`                    | Framework-neutral request client: `createOvClient`, `OvClient`, `OvClientError`           |
| [hooks](./src/hooks/README.md), [utils](./src/utils/README.md)         | Internal only                                 | Cross-module Hooks and pure utilities                                                     |

All five components are placeholders to be rewritten against their reviewed contracts. FileTree renders every documented row type with keyboard interaction; FilePreview implements the tab shell, header actions and the plain-text preview and editing foundation, with its body not yet wired to FileViewer. Tui, ScopeSwitcher and AgentConnect render a fixed-size shell only. Limits are described in each component's own docs. The package also ships five file-type icons.

Components receive view data and state through props and express user intent through callbacks, following the [component directory conventions](./src/components/AGENTS.md). [modules](./src/modules/README.md) plan Hooks and views per business capability: Hooks load data, hold state and expose operations, and composite views wire them into components. Full pages and product-specific flows belong to the host; the admin console is out of scope for this phase. `file-browser`, `console`, `task-center` and `user-usage` currently ship placeholder components, base Props / Labels types and styles so hosts can integrate layout first; business Hooks, data loading and cross-component coordination are not implemented yet.

Modules consume the native OpenViking API through a shared Client by default, with the Provider supplying `baseURL`, headers and other configuration, and modules or standalone Hooks overriding individual fields through `requestOptions`. Standard integration needs no per-module Adapter; nonstandard gateways are adapted at the host's request boundary. The request contract — `OvRequestOptions`, `OvClient` and `OvClientError` — comes from the `@openviking/react/client` subpath, which ships a `fetch`-based `createOvClient`; the Provider is exported from the same subpath; the module Hooks are not implemented yet. See [request conventions](./src/modules/README.md#request-conventions-and-default-configuration).

### Props conventions

The same props are consumed by different hosts. Hosts differ in flows and interfaces while matching in interaction and UI, so:

- Components receive **already adapted data**: file entries, tabs and content sources are mapped into package types by module Hooks or the host, and components reference no host data model.
- State is expressed with controlled fields, and callbacks only report intent without returning business data.
- Copy is passed through labels, defaulting to English; hosts map i18next or Starling in their integration layer.
- Icons, extension tabs, extra fields and toolbar actions come in as slots or nodes; components bind to no third-party UI library.
- List components render the rows or items the caller has already expanded; paging, loading, error and empty states come from module Hooks or the host.

## Public entries and styles

- The root entry exports each component and each business module placeholder with its main Props type (`<Component>Props`) only — currently `FileTree`, `Tui`, `ScopeSwitcher`, `AgentConnect` and the four business module placeholders. Row types, option types, handles and label bags come from the `components/<component>` and `modules/<module>` subpaths. `FilePreview` is not on the root entry: its types reference the optional `@ark-design/ui`, and putting it there would make that peer required. Business modules currently export placeholder components and types, with Hooks to follow once real capability lands. No aggregate `@openviking/react/components` or `@openviking/react/modules` JavaScript entry. Package exports are listed explicitly and internal files stay closed.
- Components expose only the explicitly listed layered subpaths, with no legacy path aliases.
- JS entries share implementations and never load CSS implicitly. Whole-package, component-set and single-module stylesheets are built from the same source, so import the scope you need.

| Scope                  | CSS entry                                         |
| ---------------------- | ------------------------------------------------- |
| Whole package          | `@openviking/react/style.css`                     |
| Component set          | `@openviking/react/components/style.css`          |
| Single component       | `@openviking/react/components/<module>/style.css` |
| Business module set    | `@openviking/react/modules/style.css`             |
| Single business module | `@openviking/react/modules/<module>/style.css`    |

Each CSS entry works standalone, and the whole-package CSS already includes module placeholder styles. Once real capability lands, module CSS must also include the composite view and the styles of the components it uses; using a module Hook alone needs no CSS. Cross-module public Hooks open `@openviking/react/hooks` only after a real implementation; module-specific public Hooks are exported from their module entry, and private Hooks and utilities stay out of public exports.

```tsx
import { FileBrowser } from '@openviking/react/modules/file-browser';
import '@openviking/react/modules/file-browser/style.css';

<FileBrowser
  labels={{ title: 'File browser', description: 'Placeholder component.' }}
/>;
```

The other modules export `Console`, `TaskCenter` and `UserUsage`; all currently accept `className`, `style` and `labels`, and need no Provider or request configuration.

Experience dashboards belong to preview extensions; task progress starts inside its owning module; API Key display controls are reused on demand while credential creation and management belong to the host. No separate modules are added for these now.

## Icons

```tsx
import { PdfIcon } from '@openviking/react/icons';

<PdfIcon width={20} height={20} className="file-icon" alt="PDF" />;
```

Icons render as 16 × 16 images by default. Decorative icons can keep the empty `alt`, while icons that convey file type can provide a description. SVGs are inlined into the build output, and hosts consume them directly from the package exports.

React and ReactDOM are peer dependencies (`^18.2.0 || ^19.0.0`), automatically externalized by Rslib. Rslib emits shared ESM JavaScript and declarations, plus independently built CSS entries. The public `@openviking/react/style.css` entry resolves to `dist/style.css`. Styles are scoped and do not reset application styles.

From `frontend/`, run `npm run build:lib` to generate `packages/react/dist/`. See the [workspace README](../../README.md) for documentation development and deployment.
