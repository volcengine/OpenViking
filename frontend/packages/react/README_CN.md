# @openviking/react

[English](README.md) / 中文

公开 UI 组件、组合模块 均支持可选的 `locale: 'zh-CN' | 'en-US'`。Props 覆盖最近的模块或 Provider，均未配置时默认 `en-US`。已有 `labels` 保留逐项覆盖能力，业务内容、插槽和回调文案仍由宿主提供。

OpenViking AI 应用界面的 React 基础：构建工具、文档、分层目录与局部化样式。FileTree、Tui 与 FilePreview 已提供可用视图，另附五种文件类型图标。Console 提供原生命令面板与受控对话视图，ScopeCascade 提供三级级联选择器，FileBrowser 提供目录、预览与看板联动。

本包沿用仓库的 AGPL-3.0-only 许可证。合入 Changesets 版本 PR 后，发布工作流会自动将新版本发布到 npm。也可以推送与本包版本一致的 `react@<version>` 标签触发发布。

## 模块目录

| 源码目录                                                                  | 公开入口                                      | 职责                                                                |
| ------------------------------------------------------------------------- | --------------------------------------------- | ------------------------------------------------------------------- |
| [components/directory-tour](./src/components/directory-tour/README.md)    | `@openviking/react/components/directory-tour` | 目录锚点引导，支持宿主异步定位、独立高亮与异常恢复                  |
| [components/file-tree](./src/components/file-tree/README_CN.md)           | `@openviking/react/components/file-tree`      | 目录树渲染、展开、选中与事件回传；视图为占位实现                    |
| [components/file-preview](./src/components/file-preview/README_CN.md)     | `@openviking/react/components/file-preview`   | 只读预览状态与扩展页签；视图为占位实现                              |
| [components/tui](./src/components/tui/README_CN.md)                       | `@openviking/react/components/tui`            | 已确认的命令记录、结果与输入区类型；视图仍为占位实现                |
| [components/scope-switcher](./src/components/scope-switcher/README_CN.md) | `@openviking/react/components/scope-switcher` | 无状态作用域布局与独立 ScopeSelector，提供选项、面板及搜索交互      |
| [components/agent-connect](./src/components/agent-connect/README_CN.md)   | `@openviking/react/components/agent-connect`  | 固定 Figma 接入视图，内置原始图标、文案与剪贴板反馈                 |
| [components/viking-bot](./src/components/viking-bot/README_CN.md)         | `@openviking/react/components/viking-bot`     | 对话视图契约，按契约渲染的视图仍为占位实现                          |
| [modules](./src/modules/README_CN.md)                                     | `@openviking/react/modules/<模块>`            | Console 面板、ScopeCascade 级联选择与 FileBrowser 联动              |
| [client](./src/client/index.ts)                                           | `@openviking/react/client`                    | 框架无关的请求客户端：`createOvClient`、`OvClient`、`OvClientError` |
| [hooks](./src/hooks/README_CN.md)、[utils](./src/utils/README_CN.md)      | 仅包内使用                                    | 跨模块 Hook 与纯工具                                                |

组件按各自契约逐步实现。FileTree 已按契约渲染全部行类型并实现键盘交互；FilePreview 已实现页签外壳、标题操作与纯文本预览、编辑基础，正文尚未接入 FileViewer。ScopeSwitcher 已实现无状态布局，独立 ScopeSelector 子组件管理本级展开，数据订阅、搜索与请求由宿主提供。Tui 只渲染固定尺寸空壳；AgentConnect 按 Figma 布局内置中英文 Agent 与开发接入详情和原始图标，由调用方提供密钥与语言，支持可选受控选择和剪贴板反馈，具体限制见各自文档。包还提供五种文件类型图标。
组件通过 props 接收视图数据与状态，通过回调表达用户意图，遵循 [组件目录约定](./src/components/AGENTS.md)。[modules](./src/modules/README_CN.md) 按业务能力规划 Hook 与视图：Hook 负责取数、状态和操作，组合视图将其接入组件。完整页面与产品专属流程由宿主负责，管理后台不在本期范围内。`console` 已导出原生命令组合视图，编排 Hook 保持内部实现，对话通过宿主提供的数据与回调接入；`file-browser` 提供目录、预览与看板联动。

模块默认通过共享 Client 消费 OpenViking 原生 API，由 Provider 下发 `baseURL`、headers 等配置，模块可通过 `requestOptions` 局部覆盖。标准接入无需逐模块实现 Adapter；非标准网关集中在宿主请求接入层适配。请求契约——`OvRequestOptions`、`OvClient` 与 `OvClientError`——来自 `@openviking/react/client` 子路径，自带基于 `fetch` 的实现；Provider 从同一子路径导出；业务 Hook 均为模块内部实现；ScopeCascade 按宿主提供的三个接口适配函数接入，不依赖 Provider。详见[请求约定](./src/modules/README_CN.md#请求契约与默认配置)。

### Props 约定

同一套 props 由不同宿主接入。宿主之间的流程与接口不同，交互与 UI 对齐，因此：

- 组件只接收**已适配的数据**：文件条目、页签与内容来源由模块 Hook 或宿主映射成包内类型，组件不引用任一宿主的数据模型。
- 状态用受控字段表达，回调只上报意图，不返回业务数据。
- 界面文案内置中英文，通过可选的 `locale: 'zh-CN' | 'en-US'` 选择；已有 labels 可逐项覆盖，动态文案由宿主在接入层映射 i18next 或 Starling。
- 图标、扩展页签、额外字段、工具栏动作通过插槽或节点传入，组件不绑定第三方 UI 库。
- 列表型组件按上层展开后的行/项渲染，分页、加载、错误、空态由模块 Hook 或宿主提供。

`WikiDashboard` 从 `@openviking/react/components/wiki-dashboard` 导出，`ExperienceDashboard` 从 `@openviking/react/components/experience-dashboard` 导出。两者均为纯 UI，接收展示数据和状态；原生取数及响应适配由 FileBrowser 内部负责；组件和主 Props 类型同时从根入口导出。FileBrowser 自动识别 Wiki/经验入口，并接受 `dashboardKinds` 和 `renderDashboard` 接入宿主权限与网关数据。各自样式通过组件的 `style.css` 导出，FileBrowser 样式已包含两类看板。详见[看板文档](../../docs/docs/zh/components/index.mdx)。

## 公开入口与样式

- 根入口只导出各组件、各业务模块的组件本体与主 Props 类型（`<组件名>Props`），当前为 `FileTree`、`Tui`、`ScopeSwitcher`、`ScopeSelector`、`ScopeCascade`、`AgentConnect` 、Console、FileBrowser、WikiDashboard 与 ExperienceDashboard；行类型、选项类型、句柄与文案包等子类型只从 `components/<组件>`、`modules/<模块>` 子路径导出。`FilePreview` 不在根入口：它的类型依赖可选的 `@ark-design/ui`，放进根声明会让该 peer 变成必需。ScopeCascade 从 `modules/scope-cascade` 导出组件，订阅 Hook 保持内部实现，Console 从 `modules/console` 导出组合视图，编排 Hook 保持内部实现；FileBrowser 提供目录与看板联动。不提供 `@openviking/react/components` 或 `@openviking/react/modules` 分类聚合 JS 入口。包导出显式列举，内部文件不开放。
- 组件只提供明确列出的分层子路径，不保留旧路径别名。
- JS 入口共享实现，不隐式加载 CSS。整包、组件集合与单模块样式由同一份源码构建，选择所需范围导入即可。

| 范围         | CSS 入口                                        |
| ------------ | ----------------------------------------------- |
| 整包         | `@openviking/react/style.css`                   |
| 组件集合     | `@openviking/react/components/style.css`        |
| 单组件       | `@openviking/react/components/<模块>/style.css` |
| 业务模块集合 | `@openviking/react/modules/style.css`           |
| 单业务模块   | `@openviking/react/modules/<模块>/style.css`    |

各 CSS 入口可独立使用，并包含所依赖的组件样式。所有业务 Hook 和控制器均为内部实现，不提供无头 Hook 公开入口。

```tsx
import { FileBrowser } from '@openviking/react/modules/file-browser';
import '@openviking/react/modules/file-browser/style.css';

<FileBrowser
  locale="zh-CN"
  labels={{ title: '文件浏览', description: '占位组件，待接入。' }}
/>;
```

Console 的原生命令通过 Provider 请求配置接入，仅在显式提交命令后访问服务；对话数据和回调由宿主提供。

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

将三个引用稳定的接口适配函数直接传给 `<ScopeCascade {...api} onChange={handleSelection} />`。接口返回 `{ id, label }` 选项并接收 `AbortSignal`。组件内置请求、级联、取消、重试和各级独立订阅。`onChange` 返回完整 `{ libraryId, spaceId, userId }` 身份，身份不完整时返回 `null`。从 `@openviking/react/modules/scope-cascade` 导入组件和 Props，并引入对应 `/style.css`。

## 内置文案维护

内置语言资源就近放在各组件、模块的 `locales/en-US.ts` 与 `locales/zh-CN.ts`，随代码一起维护。共同的语言类型、默认值和逐项覆盖规则位于内部 `src/shared/locale.ts`；该文件不存放业务词条，也不提供公开入口。模块仅维护流程文案，组件默认文案由组件自己维护。

动态文案使用类型化格式化函数，日期与数字使用 `Intl`。`npm test` 自动检查中英文 key、字符串占位符和格式化函数参数数量，类型检查约束参数类型。已有 `labels` 的未定义项继承当前语言，空字符串作为显式覆盖保留。

在 `OpenVikingProvider` 上配置子树语言。模块与组件的可选 `locale` Props 覆盖最近的语言作用域，最终默认 `en-US`。内部 `src/shared/locale-context.tsx` 由各公开入口共享，与请求 Client 配置分离，切换语言保留请求和状态。

内部 Input、Button、Modal 与 Drawer 参考 Arco Design 的交互逻辑（MIT），声明随包保留在 `LICENSE.arco-design`。Input、Button 的视觉规则裁剪自火山控制台使用的 `@arco-design/theme-ve-o-aml-design@0.0.36`（MIT），不使用 Arco 默认皮肤，也不提供公开组件入口。
