# FileBrowserEntries

English / [中文](README_CN.md)

Required `locale: 'zh-CN' | 'en-US'` selects built-in Chinese or English copy. Existing `labels` override individual fields; omitted fields follow locale. Hosts provide business content.

The entry row at the top of the file browsing area: connect an agent, import resources.

It has two fixed entries. Pass `agentConnect` for the built-in connection drawer, or callbacks for host-owned flows. The component owns the icons, labels, equal-width layout and divider.

```tsx
import { FileBrowserEntries } from '@openviking/react/components/file-browser-entries';
import '@openviking/react/components/file-browser-entries/style.css';

<FileBrowserEntries locale="en-US"
  agentConnect={{ apiKey: 'demo-api-key', locale: 'en-US' }}
  onImportResources={openImportFlow}
/>;
```

## Showing and hiding

Pass `agentConnect` to open the built-in right-side `AgentConnect` drawer. Its props, including the required `apiKey` and `locale`, are forwarded to the panel. The entry stylesheet includes all panel styles. The drawer slides in from the right and closes with its Close button, backdrop or Esc; narrow containers can scroll the panel horizontally.

`onConnectAgent` still supports a host-owned flow and runs on click when both options are supplied. Hide the connect entry by omitting both `agentConnect` and `onConnectAgent`; omit both `onImportResources` and `renderImportResources` to hide the import entry.

Nothing renders without visible entries.

With a single entry **no divider is drawn**: a divider separates two neighbours, and there is nothing left to separate.

```tsx
// Only the import entry, with no divider
<FileBrowserEntries locale="en-US" onImportResources={openImportFlow} />
```

## Copy

Both labels are replaceable and default to English:

```tsx
<FileBrowserEntries locale="en-US"
  agentConnect={{ apiKey: 'demo-api-key', locale: 'en-US' }}
  onImportResources={openImportFlow}
  labels={{
    connectAgent: 'Connect agent',
    importResources: 'Import resources',
  }}
/>
```

## Relation to the module

`FileBrowser` includes this component internally. Pass `agentConnect`, `onImportResources` and optional `renderImportResources` directly to the module and replace copy through `labels.entries`. This component remains available for independently assembled views.

## Custom import trigger

`renderImportResources(trigger)` receives the native button with its icon, label and `onImportResources` handler. Wrap it with a host-owned `Popover` or return a replacement node; no empty callback is needed to enable the entry. Returning `null` hides the entry and its divider. Nothing renders when all entries are hidden.

The host supplies `Popover` and `ImportMenu` below. The default button is the popover's direct child, allowing it to attach its ref and event handlers for positioning. Reusing the button preserves its click handler; a replacement node owns its interactions.

```tsx
<FileBrowserEntries locale="en-US"
  renderImportResources={(trigger) => (
    <Popover trigger="click" content={<ImportMenu />}>
      {trigger}
    </Popover>
  )}
  labels={{ importResources: 'Import resources' }}
/>
```

## Props

| Property                | Type                                   | Default | Description                                               |
| ----------------------- | -------------------------------------- | ------- | --------------------------------------------------------- |
| `locale` | `'zh-CN' \| 'en-US'` | Required | Built-in Chinese and English copy. |
| `agentConnect`          | `AgentConnectProps`                    | —       | Enables the built-in connection drawer                    |
| `onConnectAgent`        | `() => void`                           | —       | Click notification; use alone for a host-owned flow       |
| `onImportResources`     | `() => void`                           | —       | Click handler of the default import button                |
| `renderImportResources` | `(trigger: ReactElement) => ReactNode` | —       | Wrap or replace the default button; return `null` to hide |
| `labels`                | `FileBrowserEntriesLabels`             | —       | Entry labels and drawer `close` label; omitted fields follow locale |
| `className`             | `string`                               | —       | Additional container class                                |

## Theme

Styles are localised and take their colours from the package baseline. The row's tint derives from the primary colour through `color-mix`, so it follows a host that changes the primary colour.

## Drawer implementation

The internal `Drawer` in `shared/components/drawer` adapts [Arco Design Drawer](https://github.com/arco-design/arco-design/tree/main/components/Drawer). It accepts content through `children`; `FileBrowserEntries` supplies `AgentConnect`. It keeps a body portal, backdrop, 300ms right-side slide transition, focus lock and scroll lock. Other placements, footer actions, resizing and container configuration are omitted. It uses no native browser dialog. Arco’s MIT notice ships in `LICENSE.arco-design`; the package license remains AGPL-3.0-only.
