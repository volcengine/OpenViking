# @openviking/react

English / [中文](README_CN.md)

React business components and blocks for building interfaces on OpenViking, the context database for AI agents.
VikingBot is a headless module: import `useVikingBotChat`, `useVikingBotSessions` and their contracts from `@openviking/react/modules/viking-bot`. Console consumes it by default; standalone VikingBot hosts can use it directly. It has no composed view or CSS entry, and hooks are not re-exported by the package root or Console.

## Install

```bash
npm install @openviking/react
```

`react` and `react-dom` are peer dependencies (`^18.2.0 || ^19.0.0`). FilePreview, which FileBrowser uses to show file content, also needs the optional peer dependency `@ark-design/ui`. See the [FilePreview page](../../docs/docs/en/components/file-preview.mdx) for the versions and companion packages to install.

## Quick start

### A business component

A business component renders the data you pass in and sends no requests. Import the component and its stylesheet, and give its container a height:

```tsx
import { useState } from 'react';
import {
  FileTree,
  type FileTreeRow,
} from '@openviking/react/components/file-tree';
import '@openviking/react/components/file-tree/style.css';

const rows: FileTreeRow[] = [
  {
    kind: 'entry',
    id: 'viking://resources',
    uri: 'viking://resources',
    name: 'resources',
    type: 'directory',
    depth: 0,
    expanded: true,
  },
  {
    kind: 'entry',
    id: 'viking://resources/intro.md',
    uri: 'viking://resources/intro.md',
    name: 'intro.md',
    type: 'file',
    depth: 1,
  },
];

export function Tree() {
  const [selectedId, setSelectedId] = useState<string>();
  return (
    <div style={{ height: 320 }}>
      <FileTree
        rows={rows}
        selectedId={selectedId}
        operations={{ select: async (row) => setSelectedId(row.id) }}
      />
    </div>
  );
}
```

### A block

A block loads its own data from the OpenViking API. Wrap it in `OpenVikingProvider`, which holds the server address and credentials:

```tsx
import { OpenVikingProvider } from '@openviking/react/client';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import '@openviking/react/modules/file-browser/style.css';

export function Browser({ apiKey }: { apiKey: string }) {
  return (
    <OpenVikingProvider baseURL="https://ov.example.com" apiKey={apiKey}>
      <div style={{ height: 600 }}>
        <FileBrowser />
      </div>
    </OpenVikingProvider>
  );
}
```

`baseURL` is the server address without `/api/v1`. The other identity fields, the per-block `requestOptions` override and the rest of the Provider rules are in the [blocks overview](../../docs/docs/en/modules/index.mdx).

## Entries

All JavaScript entries are ESM. There is no `@openviking/react/components` or `@openviking/react/modules` entry that re-exports a whole category.

| Import path                           | Provides                                                                                                                                                                                                                       |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `@openviking/react`                   | Business components and blocks with their main `<Name>Props` types, such as `FileTree` and `FileTreeProps`. `FilePreview` is the exception: import it from its subpath.                                                        |
| `@openviking/react/components/<name>` | One component with its related types (rows, options, handles, labels), such as `components/file-tree`. Related components sit in a group, such as `components/auth/email-login-form`.                                          |
| `@openviking/react/modules/<name>`    | A block (`file-browser`, `console` or `scope-cascade`) or the `viking-bot` headless module.                                                                                                                                    |
| `@openviking/react/client`            | `OpenVikingProvider` and the request client (`createOvClient`, `OvClientError` and others).                                                                                                                                    |
| `@openviking/react/icons`             | Icon components and the file-type icon helpers.                                                                                                                                                                                |
| `…/style.css`                         | Stylesheets. JavaScript never imports CSS, so import what you use: `@openviking/react/style.css` for everything, `components/style.css` or `modules/style.css` for one category, or the `style.css` of one component or block. |

Every stylesheet entry works on its own and includes the styles of the components it depends on.

## Documentation

- [Getting started](../../docs/docs/en/guide/getting-started.mdx)
- [Internationalization](../../docs/docs/en/guide/i18n.mdx): `locale` and `labels`
- [Business components](../../docs/docs/en/components/index.mdx): the overview links to one page per business component
- [Blocks](../../docs/docs/en/modules/index.mdx): the overview covers `OpenVikingProvider` and `requestOptions`, with one page per block
- [Request client](./src/client/README.md): `createOvClient`, event streams, errors and endpoint paths

## License

`@openviking/react` is licensed under AGPL-3.0-only, the license of the OpenViking repository ([LICENSE](../../../LICENSE)). The internal Button, Input, Modal and Drawer adapt Arco Design (MIT), whose notice ships in `LICENSE.arco-design`; the visual rules of Button and Input adapt `@arco-design/theme-ve-o-aml-design@0.0.36` (MIT). Icon sources and their licenses, including the Apache-2.0 file-type icons, are recorded in [`packages/icons`](../icons/README.md).
