# @openviking/react

React UI modules for OpenViking applications. The initial scaffold exports `StatusBadge`; business modules have not been migrated.

This package is private during initial development and is not published to npm. It inherits the repository's AGPL-3.0-only license.

## 模块目录

| 源码目录                                                                 | 公开入口                       | 职责                               |
| ------------------------------------------------------------------------ | ------------------------------ | ---------------------------------- |
| [components/file-tree](./src/components/file-tree/README.md)             | `@openviking/react/tree`       | 目录树渲染、展开和选中交互         |
| [components/file-preview](./src/components/file-preview/README.md)       | `@openviking/react/preview`    | 只读文件内容与预览状态展示         |
| [components/command-console](./src/components/command-console/README.md) | `@openviking/react/console`    | 命令输入、执行状态与结果展示       |
| [components/viking-bot](./src/components/viking-bot/README.md)           | `@openviking/react/viking-bot` | Bot 对话、会话、流式回复与附件交互 |
| [hooks](./src/hooks/README.md)、[utils](./src/utils/README.md)           | 仅包内使用                     | 跨模块 Hook 与纯工具               |

四个子路径目前均为空模块，只预留源码、ESM 构建和类型声明入口；尚无组件或类型可导入。
四个入口都承载可复用视图，归入 `components/`。完整业务编排由 `pages/` 或宿主管理。模块入口与目录同名。
后续迁移在各模块入口中显式导出真实能力，同步维护对应的包导出和构建入口，并补充可运行示例和 API 文档。
请求、鉴权、路由、账号作用域和业务缓存由应用侧 Adapter 接入。

## 使用示例

```tsx
import { StatusBadge } from '@openviking/react';
import '@openviking/react/style.css';

export default function Example() {
  return <StatusBadge status="ready">Connected</StatusBadge>;
}
```

### 图标

```tsx
import { PdfIcon } from '@openviking/react/icons';

<PdfIcon width={20} height={20} className="file-icon" alt="PDF" />;
```

图标默认以 16 × 16 的图片渲染。装饰性图标可沿用默认空 `alt`，用于传达文件类型时可提供描述。SVG 随构建产物内联，宿主直接消费包导出。

React and ReactDOM are peer dependencies (`^18.2.0 || ^19.0.0`), automatically externalized by Rslib. Rslib emits ESM JavaScript, declarations, and an explicit stylesheet in one build. The public `@openviking/react/style.css` entry resolves to `dist/index.css`; consumers must import it once. Styles are scoped and do not reset application styles.

From `frontend/`, run `npm run build:lib` to generate `packages/react/dist/`. See the [workspace README](../../README.md) for documentation development and deployment.
