# @openviking/ui

React UI modules for OpenViking applications. The initial scaffold exports `StatusBadge`; business modules have not been migrated.

This package is private during initial development and is not published to npm. It inherits the repository's AGPL-3.0-only license.

## 模块目录

| 源码目录                                                                 | 公开入口                    | 职责                               |
| ------------------------------------------------------------------------ | --------------------------- | ---------------------------------- |
| [components/file-tree](./src/components/file-tree/README.md)             | `@openviking/ui/tree`       | 目录树、导航、搜索及资源操作交互   |
| [components/file-preview](./src/components/file-preview/README.md)       | `@openviking/ui/preview`    | 文件预览、详情、编辑与保存交互     |
| [components/command-console](./src/components/command-console/README.md) | `@openviking/ui/console`    | 命令输入、执行状态与结果展示       |
| [components/viking-bot](./src/components/viking-bot/README.md)           | `@openviking/ui/viking-bot` | Bot 对话、会话、流式回复与附件交互 |
| [pages](./src/pages/README.md)                                           | 尚无公开入口                | 完整页面组合、数据获取与处理       |
| [hooks](./src/hooks/README.md)、[utils](./src/utils/README.md)           | 仅包内使用                  | 跨模块 Hook 与纯工具               |

四个子路径目前均为空模块，只预留源码、ESM 构建和类型声明入口；尚无组件或类型可导入。
四块能力均归入 `components` 视图层，模块入口与目录同名，遵循 [组件目录约定](./src/components/AGENTS.md)。`StatusBadge` 只是脚手架示例。
后续迁移在各模块入口中显式导出真实能力，统一维护 `src/index.ts` 的公开导出，并补充可运行示例和 API 文档。
组件通过 props 接收处理后的数据和状态，通过事件回调上报用户意图，自身不包含数据获取逻辑。[页面组合层](./src/pages/README.md) 位于 `src/pages/`，负责组合完整页面、获取和处理数据，再传给组件渲染；当前仅有目录说明与约定，尚无页面实现。

## 使用示例

```tsx
import { StatusBadge } from '@openviking/ui';
import '@openviking/ui/style.css';

export default function Example() {
  return <StatusBadge status="ready">Connected</StatusBadge>;
}
```

React and ReactDOM are peer dependencies (`^18.2.0 || ^19.0.0`), automatically externalized by Rslib. Rslib emits ESM JavaScript, declarations, and an explicit stylesheet in one build. The public `@openviking/ui/style.css` entry resolves to `dist/index.css`; consumers must import it once. Styles are scoped and do not reset application styles.

From `ui/`, run `npm run build:lib` to generate `packages/react/dist/`. See the [workspace README](../../README.md) for documentation development and deployment.
