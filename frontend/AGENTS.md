# Frontend Agent Guide

适用于 `frontend/**`。同时遵循仓库 [贡献约定](../CONTRIBUTING.md)；开发命令与目录入口见 [README](./README.md)，依赖和构建配置以实际文件为准。

## 模块与依赖

- `frontend/` 是独立 npm workspace，维护自己的锁文件。Studio、产品文档和 SDK 在各自目录构建，通过公开入口消费共享资产。
- 业务模块以 `modules/` 组织，通过 Hook 调用共享 Client、处理数据与状态，再组合 `components/` 视图。Client 默认遵循 OpenViking 原生 API 契约；Provider 通过 Context 下发通用请求配置，模块与独立 Hook 可局部覆盖，非标准网关在宿主接入层适配。宿主可复用组合视图，也可单独使用模块 Hook 自行组装。模块和组件均可复用框架无关的函数、类型和设计资产；禁止反向依赖 Studio 路由、业务代码和全局状态。
- 按共同完成的交互流程划分业务模块，共享状态与跨组件联动在模块内维护，不按单个视图组件拆模块。模块 Hook 或宿主负责数据加载、分页与会话；宿主协调不同模块之间的联动。视图组件通过 props 接收数据和状态，通过事件回调表达操作意图。完整页面由宿主组装；认证、账号作用域、路由、权限执行及产品专属流程由宿主提供。
- 新增依赖、图标和资源必须能在公开环境安装与使用。内部 SDK、Starling、私有 IconBox、内部 CDN 和内部路径不进入公共库或示例。迁入已有代码时检查其授权及第三方许可证，沿用仓库既定许可证，变更许可证属于独立决策。
- React/ReactDOM 保持为 peer dependencies，并从库构建中 externalize；消费者使用自己的 React 实例。重型编辑器、媒体和浏览器专用依赖按需加载，模块导入和服务端渲染阶段应能在没有 `window`、`document` 的环境运行。
- 包导出、类型声明、CSS 和附属资源一起构成消费接口。新增 worker 或其他资源时，验证发布产物及消费端 URL，避免把某个构建工具的源码导入语法直接暴露给消费者。

## 源码目录

- 按 [README 的目标目录](./README.md#target-layout) 放置资产。React 包发布名为 `@openviking/react`，Vue 落地后独立交付为 `@openviking/vue`。源码边界可以先在 workspace 内共享，通过子路径提供消费入口。有独立安装、依赖隔离或独立发布节奏的需求时再拆包。
- 跨框架纯函数、资源类型和数据转换放在 `packages/lib/`。OpenViking 请求与服务适配器放在 `packages/client/`。仅供单个模块使用的工具留在模块内。仅随 React 使用的共享能力随 React 包交付。跨模块公共 Hook 有真实实现后再从 `@openviking/react/hooks` 开放，组件不能使用取数或缓存 Hook。出现独立于框架的消费需求时，再为 `lib` 定义框架无关的公开包入口，并在未安装 React 或 Vue 的项目中验证。
- 图标的 SVG 源文件、来源与授权放在 `packages/icons/`。React 图标组件放在 `packages/react/src/icons/`，随 React 包从 `@openviking/react/icons` 交付。各框架的封装直接消费同一份 SVG 源文件。构建产物包含图标实现和类型声明，消费者通过 React 包一次安装。
- React 源码的 `components/` 放独立视图组件，`modules/` 放按业务能力划分的 Hook 与组合视图，不提供完整页面。模块 Hook 暴露可独立消费的数据、状态与操作，组合视图复用 Hook 并组装组件；宿主差异通过服务契约、参数、回调与插槽接入。当前先提供五个模块的占位组件、基础类型与样式入口，便于宿主接入布局；真实业务与 Hook 随后迁入，占位状态和 API 范围须在示例与文档中明确。可独立部署的微应用放在 `apps/`，例如 `apps/workbench-embed/`，消费公开模块与组件，管理挂载、卸载、样式隔离和宿主通信。
- 每个组件、业务模块或区块独占一个 kebab-case 目录，入口文件与目录同名。实现、CSS Modules、测试及私有子组件就近放置，例如 `components/<模块>/<模块>.tsx` 和同目录的 `<模块>.module.css`。模块私有的 Hook、类型和工具留在模块内，不提前提升到公共目录。
- 根入口与子路径分别维护公开导出；组件子路径为 `@openviking/react/components/<组件>`，业务模块为 `@openviking/react/modules/<模块>`，从模块入口分别导出 Hook 与组合视图。不提供 `components`、`modules` 分类聚合 JS 入口。包导出显式列举，内部文件不开放；各入口共享实现，避免重复创建 Provider、Context 或状态对象。同步维护 Rslib 入口和声明路径。
- 目录按实际内容创建，不预建空目录，也不为单个简单模块增加不必要的转发层。

## 样式与交互

- 库样式局部化，主题变量使用 `--ov-ui-` 前缀。消费者无需额外扫描库源码生成 Tailwind CSS，也不承担库的全局 reset；库样式不修改应用的 `html`、`body`、`:root` 或 `.dark`。
- 文档站的全局样式、布局和主题覆盖只作用于文档站。CSS 同时提供整包 `style.css`、分类 `components/style.css` / `modules/style.css` 和单模块 `<分类>/<模块>/style.css` 入口；JS 不隐式加载样式。各 CSS 入口共用样式源码并可独立消费，模块 CSS 包含当前占位视图及后续组合视图所需组件样式；单独使用 Hook 无需 CSS。保留构建中的 CSS 副作用声明。
- 复用模块使用实例内状态和引用。持久化、请求缓存等需要跨实例共享的行为通过明确的 Adapter 接入，避免用全局 DOM ID、事件或 singleton Store 隐式关联实例与账号。
- 公开组件提供可替换的默认文案，动态文案通过类型化参数传入。宿主在接入层映射 i18next 或 Starling。状态通过可见文本表达；可操作控件使用合适的语义、键盘行为和焦点样式。

## 示例与文档

- 顶部导航以 Guide、Components 和 OpenViking docs 外链为基础。Guide 承载入门、安装、样式与集成说明；Components 承载视图组件。出现实际内容后再增加 Modules 和 Patterns，分别对应业务 Hook 与组合视图，以及纯视图组合示例；独立 API 栏目在内容需要时再引入。
- 文档按语言组织在 `docs/docs/en/` 和 `docs/docs/zh/`，各语言内按 `guide/`、`components/`、`modules/`、`patterns/` 分类。业务模块归入 Modules，不把所有模块都归入 Components。
- 英文为默认语言，保留 `/ui/` 下的原有地址；简体中文使用 `/ui/zh/`。新增或修改文档时同步维护两种语言的页面、导航和侧边栏，保持对应页面路径一致，以支持原位语言切换。可运行示例共用 `docs/docs/_demos/`，不要为翻译复制实现；组件文案仍由调用者定制。
- 模块分类入口指向该分类的 `index.mdx` 概览，而不是首个模块；侧边栏按路由前缀隔离，仅列出当前分类的概览与详情，并按职责分组。新增分类时同时提供真实内容、概览和导航，不创建空栏目或占位页面。
- 示例从 `@openviking/react` 的公开导出及样式入口导入，不通过相对路径绕过接口。外部 TSX 示例是可运行代码与源码展示的同一事实源。
- 开发与正式文档均通过包导出消费实际库产物。开发模式由官方 workspace 插件启动 Rslib watch；不使用源码 alias 或 TypeScript paths 绕过产物解析，以发现缺失导出、CSS 和打包资源等问题。
- 新增或修改公开能力时，同步检查导出、示例、属性表和导航。手写属性表中的默认值、异步语义和错误行为必须与实际实现一致。
- 示例使用固定、公开的数据和可独立运行的 Adapter；模拟结果应明确标识，截图与输出中不包含真实密钥、内部地址或个人数据。

## 静态部署

- `/ui/` 是组件站的部署前缀。链接与静态资源使用 Rspress 的 base 处理；修改路由或资源路径时检查页面直达、刷新和嵌套路径，不能只检查首页。
- GitHub Pages 每次替换整个站点。组件站产物须与产品文档合并后发布，不能新增一条只上传 UI 产物的 Pages 部署。
- TOS 需要先上传页面依赖的资源，再上传 HTML；无扩展名页面需对应实际 HTML 对象。保留产品文档路径，避免创建遮蔽 `/ui/` 目录的 `/ui` 对象。
- 部署入口与产物合并规则见 [Pages 工作流](../.github/workflows/_docs-deploy.yml)、[TOS 工作流](../.github/workflows/docs-tos.yml)。分支检查通过、正式站发布和 npm 发包是不同结果，交付时分别说明。

## 验证与交付

- JS/TS 文件（含 JSX、TSX）每个文件最多 200 行，不计空行和纯注释行，由 lint 的 `max-lines` 规则检查。
- 按变更选择验证：纯文字改动检查格式、链接和差异；类型、实现或构建配置改动运行 workspace 的格式检查、类型检查和完整构建。命令见 README，CI 环境见 [公共构建 action](../.github/actions/build-ui/action.yml)。
- 用户可见的行为或样式改动，在浏览器中运行构建后的文档并实际操作示例，检查相关的主题、窄屏和失败状态；截图后查看图片，构建成功本身不能证明页面可用。
- 修改包导出、peer dependencies、资源或打包策略时，从真实打包产物验证消费者，包括声明支持的 React 版本，并检查声明、CSS 与 SVG 能在 workspace 外解析；文档开发模式不能代替独立消费验证。
- 修改部署时，验证合并后主站页面仍存在，并检查 UI 深链及静态资源。线上尚未发布或外部检查尚未结束时，报告实际状态。
- 交付前检查最终 diff、`git diff --check` 和工作区状态。`frontend/AGENTS.md` 应由 Git 跟踪；构建产物和临时验证文件保持忽略。
