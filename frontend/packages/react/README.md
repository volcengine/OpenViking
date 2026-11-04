# @openviking/react

React UI modules for OpenViking applications. The initial scaffold exports `StatusBadge`; the seven business modules export their public props types and ship placeholder components until the views are migrated.

This package inherits the repository's AGPL-3.0-only license. The release workflow publishes it to npm when a `react@<version>` tag matches the version in this package's manifest.

## 模块目录

| 源码目录                                                                 | 公开入口                                       | 职责                             |
| ------------------------------------------------------------------------ | ---------------------------------------------- | -------------------------------- |
| [components/file-tree](./src/components/file-tree/README.md)             | `@openviking/react/components/file-tree`       | 目录树渲染、展开、选中与事件回传 |
| [components/file-preview](./src/components/file-preview/README.md)       | `@openviking/react/components/file-preview`    | 只读预览状态与扩展页签           |
| [components/tui](./src/components/tui/README.md)                         | `@openviking/react/components/tui`             | 命令输入、执行状态与结果展示     |
| [components/viking-bot](./src/components/viking-bot/README.md)           | `@openviking/react/components/viking-bot`      | 对话、会话、回复及附件交互       |
| [components/scope-switcher](./src/components/scope-switcher/README.md)   | `@openviking/react/components/scope-switcher`  | 作用域选择与切换交互             |
| [components/agent-connect](./src/components/agent-connect/README.md)     | `@openviking/react/components/agent-connect`   | Agent 接入步骤、配置说明及状态   |
| [components/resource-import](./src/components/resource-import/README.md) | `@openviking/react/components/resource-import` | 导入方式、文件选择、进度及结果   |
| [pages](./src/pages/README.md)                                           | 尚无公开入口                                   | 页面组合、数据获取与处理         |
| [hooks](./src/hooks/README.md)、[utils](./src/utils/README.md)           | 仅包内使用                                     | 跨模块 Hook 与纯工具             |

七个业务模块已定义公开的 props 类型，并从各自子路径导出同名占位组件。占位组件只渲染一个固定尺寸的空壳，不消费 props，用于先行发布包供宿主对照 API；真实实现随视图迁入重写。`StatusBadge` 是已完成的脚手架示例，可从根入口或 `@openviking/react/components/status-badge` 导入。
组件通过 props 接收视图数据与状态，通过回调表达用户意图，遵循 [组件目录约定](./src/components/AGENTS.md)。[pages](./src/pages/README.md) 负责取数、处理业务状态和编排；尚未定义首个页面。

### Props 约定

同一套 props 由不同宿主接入。宿主之间的流程与接口不同，交互与 UI 对齐，因此：

- 组件只接收**已适配的数据**：文件条目、消息、任务、作用域选项都由宿主映射成包内类型，组件不引用任一宿主的数据模型。
- 状态用受控字段表达，回调只上报意图，不返回业务数据。
- 文案通过 labels 传入，缺省为英文；动态文案由宿主在接入层映射 i18next 或 Starling。
- 图标、扩展页签、额外字段、工具栏动作通过插槽或节点传入，组件不绑定第三方 UI 库。
- 列表型组件按「宿主展开后的行/项」渲染，分页、加载、错误、空态都由宿主提供。

## 公开入口与样式

- 根入口聚合已实现的公开 API；具体组件使用 `components/<模块>` 子路径。不提供 `@openviking/react/components` 或 `@openviking/react/pages` 分类聚合 JS 入口。包导出显式列举，内部文件不开放。
- 当前私有开发包的旧 `/tree`、`/preview`、`/console`、`/tui` 和 `/viking-bot` 子路径统一替换为上述分层路径，不保留旧路径别名。
- JS 入口共享实现，不隐式加载 CSS。整包、组件集合与单模块样式由同一份源码构建，选择所需范围导入即可。

| 范围     | CSS 入口                                        |
| -------- | ----------------------------------------------- |
| 整包     | `@openviking/react/style.css`                   |
| 组件集合 | `@openviking/react/components/style.css`        |
| 单组件   | `@openviking/react/components/<模块>/style.css` |

单组件 CSS 可独立使用。后续有真实页面时再开放 `@openviking/react/pages/<页面>`、`pages/style.css` 及 `pages/<页面>/style.css`；页面 CSS 包含页面自身与依赖组件所需样式，整包 CSS 同步纳入页面样式。公共 Hook 有真实实现后再开放 `@openviking/react/hooks`；Hook 不提供 CSS。模块私有 Hook 和工具不进入公共导出。

经验看板属于预览扩展；任务进度先归所属模块；API Key 展示控件按需复用，凭证创建及管理由宿主负责。本次不新增这些独立模块。

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

React and ReactDOM are peer dependencies (`^18.2.0 || ^19.0.0`), automatically externalized by Rslib. Rslib emits shared ESM JavaScript and declarations, plus independently built CSS entries. The public `@openviking/react/style.css` entry resolves to `dist/style.css`. Styles are scoped and do not reset application styles.

From `frontend/`, run `npm run build:lib` to generate `packages/react/dist/`. See the [workspace README](../../README.md) for documentation development and deployment.
