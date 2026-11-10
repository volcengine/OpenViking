# FileBrowserEntries

English / [中文](README_CN.md)

Required `locale: 'zh-CN' | 'en-US'` selects built-in Chinese or English copy. Existing `labels` override individual fields; omitted fields follow locale. Hosts provide business content.

The entry row at the top of the file browsing area: connect an agent, import resources.

It is fixed to two entries. The host only decides which appear and what a click does; the icons, copy, equal-width layout and the divider between them belong to the component — both look the same under every host and differ only in behaviour.

```tsx
import { FileBrowserEntries } from '@openviking/react/components/file-browser-entries';
import '@openviking/react/components/file-browser-entries/style.css';

<FileBrowserEntries
  locale="en-US"
  onConnectAgent={openConnectDialog}
  onImportResources={openImportFlow}
/>;
```

## Showing and hiding

A callback is the switch: omit `onConnectAgent` to hide the connect entry, omit `onImportResources` to hide the import entry.

With both omitted nothing renders — leaving an empty tinted strip would suggest something failed to load.

With a single entry **no divider is drawn**: a divider separates two neighbours, and there is nothing left to separate.

```tsx
// Only the import entry, with no divider
<FileBrowserEntries locale="en-US" onImportResources={openImportFlow} />
```

## Copy

Both labels are replaceable and default to English:

```tsx
<FileBrowserEntries
  locale="en-US"
  onConnectAgent={openConnectDialog}
  onImportResources={openImportFlow}
  labels={{
    connectAgent: 'Connect agent',
    importResources: 'Import resources',
  }}
/>
```

## Relation to the module

`FileBrowser`'s `toolbarActions` accepts any node. Passing this component there makes the entry row match the rest of the module:

```tsx
<FileBrowser locale="en-US"
  toolbarActions={
    <FileBrowserEntries locale="en-US" onConnectAgent={connect} onImportResources={import} />
  }
/>;
```

Connection dialogs, import flows and onboarding belong to the host; the component only reports the click.

## Props

| Property            | Type                       | Default  | Description                                               |
| ------------------- | -------------------------- | -------- | --------------------------------------------------------- |
| `locale`            | `'zh-CN' \| 'en-US'`       | Required | Built-in Chinese and English copy.                        |
| `onConnectAgent`    | `() => void`               | —        | The connect entry was clicked; omit it to hide that entry |
| `onImportResources` | `() => void`               | —        | The import entry was clicked; omit it to hide that entry  |
| `labels`            | `FileBrowserEntriesLabels` | —        | The two labels; omitted ones follow locale                |
| `className`         | `string`                   | —        | Additional container class                                |

## Theme

Styles are localised and take their colours from the package baseline. The row's tint derives from the primary colour through `color-mix`, so it follows a host that changes the primary colour.
