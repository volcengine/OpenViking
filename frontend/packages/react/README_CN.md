# @openviking/react

[English](README.md) / 中文

用于在 OpenViking（面向 AI 智能体的上下文数据库）之上构建应用界面的 React 业务组件与区块。

## 安装

```bash
npm install @openviking/react
```

`react` 与 `react-dom` 是 peer 依赖（`^18.2.0 || ^19.0.0`）。FileBrowser 通过 FilePreview 展示文件内容，FilePreview 还需要可选的 peer 依赖 `@ark-design/ui`，版本和需要配套安装的包见 [FilePreview 页面](../../docs/docs/zh/components/file-preview.mdx)。

## 快速开始

### 业务组件

业务组件只渲染传入的数据，不发请求。引入组件和样式，并给容器设置高度：

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

### 区块

区块自己从 OpenViking API 取数。把它放进 `OpenVikingProvider`，由 Provider 持有服务地址和凭证：

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

`baseURL` 是不含 `/api/v1` 的服务地址。其余身份字段、各区块的 `requestOptions` 覆盖，以及 Provider 的完整规则见[区块概览](../../docs/docs/zh/modules/index.mdx)。

## 入口

所有 JavaScript 入口都是 ESM。不提供 `@openviking/react/components`、`@openviking/react/modules` 这类整类聚合的入口。

| 导入路径                              | 提供                                                                                                                                                                              |
| ------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `@openviking/react`                   | 业务组件和区块，以及各自的主 Props 类型，例如 `FileTree` 与 `FileTreeProps`。`FilePreview` 除外，需要从它的子路径导入。                                                           |
| `@openviking/react/components/<名称>` | 单个组件及其相关类型（行、选项、句柄、文案），例如 `components/file-tree`。同族组件放在分组目录下，例如 `components/auth/email-login-form`。                                      |
| `@openviking/react/modules/<名称>`    | 单个区块：`file-browser`、`console` 或 `scope-cascade`。                                                                                                                          |
| `@openviking/react/client`            | `OpenVikingProvider` 与请求客户端（`createOvClient`、`OvClientError` 等）。                                                                                                       |
| `@openviking/react/icons`             | 图标组件与文件类型图标函数。                                                                                                                                                      |
| `…/style.css`                         | 样式。JavaScript 不会引入 CSS，用到什么引入什么：整包用 `@openviking/react/style.css`，一类用 `components/style.css` 或 `modules/style.css`，单个组件或区块用各自的 `style.css`。 |

每个样式入口都可以独立使用，并包含所依赖组件的样式。

## 文档

- [快速开始](../../docs/docs/zh/guide/getting-started.mdx)
- [国际化](../../docs/docs/zh/guide/i18n.mdx)：`locale` 与 `labels`
- [业务组件](../../docs/docs/zh/components/index.mdx)：概览页链接到每个业务组件的页面
- [区块](../../docs/docs/zh/modules/index.mdx)：概览页介绍 `OpenVikingProvider` 与 `requestOptions`，每个区块一个页面
- [请求客户端](./src/client/README_CN.md)：`createOvClient`、事件流、错误与接口路径

## 许可证

`@openviking/react` 使用 AGPL-3.0-only 许可证，与 OpenViking 仓库一致（[LICENSE](../../../LICENSE)）。内部的 Button、Input、Modal 与 Drawer 改编自 Arco Design（MIT），声明随包保留在 `LICENSE.arco-design`；Button 与 Input 的视觉规则改编自 `@arco-design/theme-ve-o-aml-design@0.0.36`（MIT）。图标的来源与授权（包括 Apache-2.0 的文件类型图标）记录在 [`packages/icons`](../icons/README.md)。
