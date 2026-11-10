# @openviking/react

[English](README.md) / 中文

OpenViking AI 应用界面的 React 基础：构建工具、文档、分层目录与局部化样式。FileTree、Tui 与 FilePreview 已提供可用视图，另附五种文件类型图标。Console 提供原生命令面板与受控对话视图，ScopeCascade 提供三级级联 Hook 与组合视图，FileBrowser 仍为布局占位。

本包沿用仓库的 AGPL-3.0-only 许可证。当 `react@<version>` 标签与本包 manifest 中的版本一致时，发布工作流会将其发布到 npm。

## 模块目录

| 源码目录                                                                  | 公开入口                                      | 职责                                                                       |
| ------------------------------------------------------------------------- | --------------------------------------------- | -------------------------------------------------------------------------- |
| [components/file-tree](./src/components/file-tree/README_CN.md)           | `@openviking/react/components/file-tree`      | 目录树渲染、展开、选中与事件回传；视图为占位实现                           |
| [components/file-preview](./src/components/file-preview/README_CN.md)     | `@openviking/react/components/file-preview`   | 只读预览状态与扩展页签；视图为占位实现                                     |
| [components/tui](./src/components/tui/README_CN.md)                       | `@openviking/react/components/tui`            | 已确认的命令记录、结果与输入区类型；视图仍为占位实现                       |
| [components/scope-switcher](./src/components/scope-switcher/README_CN.md) | `@openviking/react/components/scope-switcher` | 无状态作用域布局与独立 ScopeSelector，提供选项、面板及搜索交互             |
| [components/agent-connect](./src/components/agent-connect/README_CN.md)   | `@openviking/react/components/agent-connect`  | 固定 Figma 接入视图，内置原始图标、文案与剪贴板反馈                        |
| [components/viking-bot](./src/components/viking-bot/README_CN.md)         | `@openviking/react/components/viking-bot`     | 对话视图契约，按契约渲染的视图仍为占位实现                                 |
| [modules](./src/modules/README_CN.md)                                     | `@openviking/react/modules/<模块>`            | Console 面板、ScopeCascade 级联 Hook 与组合视图，以及 FileBrowser 占位视图 |
| [client](./src/client/index.ts)                                           | `@openviking/react/client`                    | 框架无关的请求客户端：`createOvClient`、`OvClient`、`OvClientError`        |
| [hooks](./src/hooks/README_CN.md)、[utils](./src/utils/README_CN.md)      | 仅包内使用                                    | 跨模块 Hook 与纯工具                                                       |

组件按各自契约逐步实现。FileTree 已按契约渲染全部行类型并实现键盘交互；FilePreview 已实现页签外壳、标题操作与纯文本预览、编辑基础，正文尚未接入 FileViewer。ScopeSwitcher 已实现无状态布局，独立 ScopeSelector 子组件管理本级展开，数据订阅、搜索与请求由宿主提供。Tui 只渲染固定尺寸空壳；AgentConnect 按 Figma 布局内置中英文 Agent 与开发接入详情和原始图标，由调用方提供密钥与语言，支持可选受控选择和剪贴板反馈，具体限制见各自文档。包还提供五种文件类型图标。
组件通过 props 接收视图数据与状态，通过回调表达用户意图，遵循 [组件目录约定](./src/components/AGENTS.md)。[modules](./src/modules/README_CN.md) 按业务能力规划 Hook 与视图：Hook 负责取数、状态和操作，组合视图将其接入组件。完整页面与产品专属流程由宿主负责，管理后台不在本期范围内。`console` 已导出原生命令组合视图，编排 Hook 保持内部实现，对话通过宿主提供的数据与回调接入；`file-browser` 仍提供占位视图与类型。

模块默认通过共享 Client 消费 OpenViking 原生 API，由 Provider 下发 `baseURL`、headers 等配置，模块或独立 Hook 可通过 `requestOptions` 局部覆盖。标准接入无需逐模块实现 Adapter；非标准网关集中在宿主请求接入层适配。请求契约——`OvRequestOptions`、`OvClient` 与 `OvClientError`——来自 `@openviking/react/client` 子路径，自带基于 `fetch` 的实现；Provider 从同一子路径导出；这些原生 API 模块的 Hook 尚未实现；ScopeCascade 按宿主提供的三个接口适配函数接入，不依赖 Provider。详见[请求约定](./src/modules/README_CN.md#请求契约与默认配置)。

### Props 约定

同一套 props 由不同宿主接入。宿主之间的流程与接口不同，交互与 UI 对齐，因此：

- 组件只接收**已适配的数据**：文件条目、页签与内容来源由模块 Hook 或宿主映射成包内类型，组件不引用任一宿主的数据模型。
- 状态用受控字段表达，回调只上报意图，不返回业务数据。
- 文案通过 labels 传入，缺省为英文；动态文案由宿主在接入层映射 i18next 或 Starling。
- 图标、扩展页签、额外字段、工具栏动作通过插槽或节点传入，组件不绑定第三方 UI 库。
- 列表型组件按上层展开后的行/项渲染，分页、加载、错误、空态由模块 Hook 或宿主提供。

## 公开入口与样式

- 根入口只导出各组件、各业务模块的组件本体与主 Props 类型（`<组件名>Props`），当前为 `FileTree`、`Tui`、`ScopeSwitcher`、`ScopeSelector`、`ScopeCascade`、`AgentConnect` 、Console 与 FileBrowser 占位组件；行类型、选项类型、句柄与文案包等子类型只从 `components/<组件>`、`modules/<模块>` 子路径导出。`FilePreview` 不在根入口：它的类型依赖可选的 `@ark-design/ui`，放进根声明会让该 peer 变成必需。ScopeCascade 从 `modules/scope-cascade` 导出组合视图与独立订阅 Hook，Console 从 `modules/console` 导出组合视图，编排 Hook 保持内部实现；FileBrowser 仍为占位。不提供 `@openviking/react/components` 或 `@openviking/react/modules` 分类聚合 JS 入口。包导出显式列举，内部文件不开放。
- 组件只提供明确列出的分层子路径，不保留旧路径别名。
- JS 入口共享实现，不隐式加载 CSS。整包、组件集合与单模块样式由同一份源码构建，选择所需范围导入即可。

| 范围         | CSS 入口                                        |
| ------------ | ----------------------------------------------- |
| 整包         | `@openviking/react/style.css`                   |
| 组件集合     | `@openviking/react/components/style.css`        |
| 单组件       | `@openviking/react/components/<模块>/style.css` |
| 业务模块集合 | `@openviking/react/modules/style.css`           |
| 单业务模块   | `@openviking/react/modules/<模块>/style.css`    |

各 CSS 入口可独立使用，整包 CSS 已包含模块占位样式。真实能力迁入后，模块 CSS 须同步包含组合视图与依赖组件的样式；单独使用模块 Hook 无需导入 CSS。跨模块公共 Hook 有真实实现后再开放 `@openviking/react/hooks`；模块专属的公开 Hook 从对应模块入口导出，私有 Hook 和工具不进入公共导出。

```tsx
import { FileBrowser } from '@openviking/react/modules/file-browser';
import '@openviking/react/modules/file-browser/style.css';

<FileBrowser
  labels={{ title: '文件浏览', description: '占位组件，待接入。' }}
/>;
```

其余模块导出 `Console`，目前接受 `className`、`style` 与 `labels`，无需 Provider 或请求配置。

经验看板属于预览扩展；任务进度先归所属模块；API Key 展示控件按需复用，凭证创建及管理由宿主负责。本次不新增这些独立模块。

## 图标

```tsx
import { PdfIcon } from '@openviking/react/icons';

<PdfIcon width={20} height={20} className="file-icon" alt="PDF" />;
```

图标默认以 16 × 16 的图片渲染。装饰性图标可沿用默认空 `alt`，用于传达文件类型时可提供描述。SVG 随构建产物内联，宿主直接消费包导出。

React 与 ReactDOM 为 peer dependencies（`^18.2.0 || ^19.0.0`），由 Rslib 自动 externalize。Rslib 产出共享的 ESM JavaScript 与声明文件，以及独立构建的 CSS 入口。公开入口 `@openviking/react/style.css` 指向 `dist/style.css`。样式局部化，不重置应用样式。

在 `frontend/` 下运行 `npm run build:lib` 生成 `packages/react/dist/`。文档开发与部署见[前端工作区 README](../../README.md)。

### ScopeCascade

将三个引用稳定的接口适配函数传给 `useScopeCascade({ loadLibraries, loadSpaces, loadUsers })`，再渲染 `<ScopeCascade controller={controller} />`。接口返回 `{ id, label }` 选项并接收 `AbortSignal`；空间接口额外接收库 ID，用户接口接收库 ID 与空间 ID。模块负责级联请求、下级重置、加载、错误、重试和旧响应隔离。每一级独立订阅，调用 Hook 的根组件不订阅请求状态。

从 `@openviking/react/modules/scope-cascade` 导入 Hook 和组合视图，并显式导入对应 `/style.css`。自定义界面可在叶子组件中使用 `useScopeCascadeLevel(controller.levels[index])`，无需 `memo`。适配函数引用变化会重置级联；当前按完整列表加载，不包含服务端搜索和分页。
