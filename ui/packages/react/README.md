# @openviking/ui

React UI modules for OpenViking applications. The initial scaffold exports `StatusBadge`; business modules have not been migrated.

This package is private during initial development and is not published to npm. It inherits the repository's AGPL-3.0-only license.

## 模块目录

| 源码目录                                                             | 公开入口                    | 职责                               |
| -------------------------------------------------------------------- | --------------------------- | ---------------------------------- |
| [patterns/file-tree](./src/patterns/file-tree/README.md)             | `@openviking/ui/tree`       | 目录树、导航、搜索及资源操作交互   |
| [patterns/file-preview](./src/patterns/file-preview/README.md)       | `@openviking/ui/preview`    | 文件预览、详情、编辑与保存交互     |
| [patterns/command-console](./src/patterns/command-console/README.md) | `@openviking/ui/console`    | 命令输入、执行状态与结果展示       |
| [patterns/viking-bot](./src/patterns/viking-bot/README.md)           | `@openviking/ui/viking-bot` | Bot 对话、会话、流式回复与附件交互 |
| [hooks](./src/hooks/README.md)、[utils](./src/utils/README.md)       | 仅包内使用                  | 跨模块 Hook 与纯工具               |

四个子路径目前均为空模块，只预留源码、ESM 构建和类型声明入口；尚无组件或类型可导入。
四块能力按组合区块归入 `patterns`，模块入口与目录同名。
后续迁移在各模块入口中显式导出真实能力，统一维护 `src/index.ts` 的公开导出，并补充可运行示例和 API 文档。
请求、鉴权、路由、账号作用域和业务缓存由应用侧 Adapter 接入。

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
