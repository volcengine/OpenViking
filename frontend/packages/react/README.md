# @openviking/react

React foundations for OpenViking AI application interfaces: build tooling, documentation, layered directories, and scoped styles. Applications provide business UI, data, authentication, and workflow orchestration. FileTree and FilePreview provide reusable views, alongside five file-type icons. Business modules will combine data-loading Hooks with composed views.

This package inherits the repository's AGPL-3.0-only license. The release workflow publishes it to npm when a `react@<version>` tag matches the version in this package's manifest.

## 模块目录

| 源码目录                                                           | 公开入口                                    | 职责                             |
| ------------------------------------------------------------------ | ------------------------------------------- | -------------------------------- |
| [components/file-tree](./src/components/file-tree/README.md)       | `@openviking/react/components/file-tree`    | 目录树渲染、展开、选中与事件回传 |
| [components/file-preview](./src/components/file-preview/README.md) | `@openviking/react/components/file-preview` | 只读预览状态与扩展页签           |
| [modules](./src/modules/README.md)                                 | 尚无公开入口                                | 业务 Hook、数据加载与视图组合    |
| [hooks](./src/hooks/README.md)、[utils](./src/utils/README.md)     | 仅包内使用                                  | 跨模块 Hook 与纯工具             |

FileTree 已实现目录树视图与交互，FilePreview 已实现页签与纯文本预览、编辑基础；具体限制见各自文档。包还提供五种文件类型图标。
组件通过 props 接收视图数据与状态，通过回调表达用户意图，遵循 [组件目录约定](./src/components/AGENTS.md)。[modules](./src/modules/README.md) 按业务能力组合 Hook 与视图：Hook 负责取数、状态和操作，组合视图将其接入组件。接入方可直接使用组合视图，也可单独复用模块 Hook 自行组装；完整页面与产品专属流程由宿主负责。模块层级与候选目录按个人终端规划，管理后台不在本期范围内；当前尚无业务模块实现。

模块默认通过共享 Client 消费 OpenViking 原生 API，由 Provider 下发 `baseURL`、headers 等配置，模块或独立 Hook 可通过 `requestOptions` 局部覆盖。标准接入无需逐模块实现 Adapter；非标准网关集中在宿主请求接入层适配。详见[请求约定](./src/modules/README.md#请求契约与默认配置)；Client 与 Provider 当前也尚未实现或导出。

### Props 约定

同一套 props 由不同宿主接入。宿主之间的流程与接口不同，交互与 UI 对齐，因此：

- 组件只接收**已适配的数据**：文件条目、页签与内容来源由模块 Hook 或宿主映射成包内类型，组件不引用任一宿主的数据模型。
- 状态用受控字段表达，回调只上报意图，不返回业务数据。
- 文案通过 labels 传入，缺省为英文；动态文案由宿主在接入层映射 i18next 或 Starling。
- 图标、扩展页签、额外字段、工具栏动作通过插槽或节点传入，组件不绑定第三方 UI 库。
- 列表型组件按上层展开后的行/项渲染，分页、加载、错误、空态由模块 Hook 或宿主提供。

## 公开入口与样式

- 根入口仅导出 FileTree 及其公开类型；具体组件使用 `components/<组件>` 子路径；业务模块实现后使用 `modules/<模块>` 子路径，分别导出 Hook 与组合视图。不提供 `@openviking/react/components` 或 `@openviking/react/modules` 分类聚合 JS 入口。包导出显式列举，内部文件不开放。
- 组件只提供明确列出的分层子路径，不保留旧路径别名。
- JS 入口共享实现，不隐式加载 CSS。整包、组件集合与单模块样式由同一份源码构建，选择所需范围导入即可。

| 范围     | CSS 入口                                        |
| -------- | ----------------------------------------------- |
| 整包     | `@openviking/react/style.css`                   |
| 组件集合 | `@openviking/react/components/style.css`        |
| 单组件   | `@openviking/react/components/<模块>/style.css` |

单组件 CSS 可独立使用。后续有真实业务模块时再开放 `@openviking/react/modules/<模块>`、`modules/style.css` 及 `modules/<模块>/style.css`；模块 CSS 包含组合视图自身与依赖组件所需样式，整包 CSS 同步纳入模块样式。单独使用模块 Hook 无需导入 CSS。跨模块公共 Hook 有真实实现后再开放 `@openviking/react/hooks`；模块专属的公开 Hook 从对应模块入口导出，私有 Hook 和工具不进入公共导出。

经验看板属于预览扩展；任务进度先归所属模块；API Key 展示控件按需复用，凭证创建及管理由宿主负责。本次不新增这些独立模块。

## 图标

```tsx
import { PdfIcon } from '@openviking/react/icons';

<PdfIcon width={20} height={20} className="file-icon" alt="PDF" />;
```

图标默认以 16 × 16 的图片渲染。装饰性图标可沿用默认空 `alt`，用于传达文件类型时可提供描述。SVG 随构建产物内联，宿主直接消费包导出。

React and ReactDOM are peer dependencies (`^18.2.0 || ^19.0.0`), automatically externalized by Rslib. Rslib emits shared ESM JavaScript and declarations, plus independently built CSS entries. The public `@openviking/react/style.css` entry resolves to `dist/style.css`. Styles are scoped and do not reset application styles.

From `frontend/`, run `npm run build:lib` to generate `packages/react/dist/`. See the [workspace README](../../README.md) for documentation development and deployment.
