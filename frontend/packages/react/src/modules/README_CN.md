# 业务模块

[English](README.md) / 中文

`modules/` 按共同完成的交互流程组织 Hook 与组合视图，共享状态与联动在模块内维护。Hook 负责加载请求、适配数据与维护状态，组合视图把 Hook 返回的数据和操作接入 `components/`。完整页面由宿主组装。

## 当前可接入的模块

`file-browser` 已实现：`useFileBrowser` 取数并协调目录、搜索与预览，`FileBrowser` 组合视图渲染目录树与文件预览。`console` 仍是占位组件，只渲染模块名称与占位文案；它的业务 Hook 与交互仍待实现。

模块自己取数，因此 props 里没有数据字段：宿主给配置、受控值与文案，模块通过回调上报变化。

| 模块           | 组件          | 公开入口                                 |
| -------------- | ------------- | ---------------------------------------- |
| `file-browser` | `FileBrowser` | `@openviking/react/modules/file-browser` |
| `console`      | `Console`     | `@openviking/react/modules/console`      |

```tsx
import { FileBrowser } from '@openviking/react/modules/file-browser';
import '@openviking/react/modules/file-browser/style.css';

<FileBrowser labels={{ title: '文件浏览' }} />;
```

两个组件与主 Props 类型也从 `@openviking/react` 根入口导出；Labels、Slots 与枚举类型从模块子路径导出。可运行示例与属性说明见[模块文档](../../../../docs/docs/zh/modules/index.mdx)。以下各节描述目标架构和待迁入能力。

## 职责

- 模块 Hook 默认通过共享 Client 调用开源原生 API，继承 Provider 的请求配置，管理加载、空态、错误、分页、刷新、重试和必要的连接清理。
- Hook 返回视图可消费的数据、状态与操作；组合视图负责把这些能力接入组件，协调模块内的交互。
- 宿主可以直接使用组合视图，也可以只复用模块 Hook，替换视图或调整组件组合。
- 标准接入只配置服务地址与鉴权，不要求宿主逐模块实现数据 Adapter；非标准网关在宿主请求接入层适配。认证、账号作用域、路由、权限执行、页面布局及产品专属流程仍由宿主负责。

依赖方向为 `modules → components`。组件仍只负责视图与交互，可脱离业务模块独立使用。模块专属 Hook、类型、工具和私有视图留在模块目录；跨模块通用 Hook 有真实复用需求后再放入 `hooks/`。

## 个人终端的层级

当前按个人终端规划共享能力，管理后台不在本期范围内。层级表示职责与依赖方向，不对应新的 npm 包：

| 层级           | 职责                                                                                 |
| -------------- | ------------------------------------------------------------------------------------ |
| 宿主应用       | 页面布局、路由、登录与账号、当前库 / 数据空间 / User、权限与能力配置，以及模块间联动 |
| `modules/`     | 一项可独立接入的业务能力：数据加载 Hook、业务状态与操作、可替换的组合视图            |
| `components/`  | 通过 props 和事件复用的视图与交互，不自行取业务数据                                  |
| 服务与基础能力 | 开源原生 API Client、资源类型与纯数据转换；按实际实现放入 `client/`、`lib/` 等边界   |

宿主可以只使用组件、复用模块 Hook 自行组装，或使用模块提供的组合视图。每个业务模块独占一个目录，粒度以可独立接入的交互流程为准：目录、搜索和预览组成 `file-browser`；对话与 TUI 组成 `console`，在模块内切换模式。宿主组合这两个区域及其他模块，不按内部视图分别接入。

## 请求契约与默认配置

共享 Client 以 OpenViking 原生 API 的 method、路径、query / body 和响应 DTO 为契约，维护在 `src/client/`，从 `@openviking/react/client` 导出。它自带基于 `fetch` 的 `createOvClient`，并提供模块依赖的契约——请求形状、配置解析、`OvClient` 与 `OvClientError`。代码不依赖 React，出现 React 之外的消费方后再拆为独立包。模块 Hook 消费原生数据并派生视图所需的状态与操作，不要求宿主逐模块维护数据协议。

React 接入由 `@openviking/react/client` 的 `OpenVikingProvider` 通过 Context 下发通用配置，并在 Provider 实例内复用稳定的 Client。宿主传的是身份字段——`apiKey`、`accountId`、`userId`、`resourceId`——请求头由 Client 组装；模块通过内部 Hook 取用，不自己拼请求。Context 不承担业务数据缓存；Provider 挂载本身不取数，也不读取浏览器中保存的凭证。

| 配置          | 约定                                                                                                                   |
| ------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `baseURL`     | 服务地址及可选网关前缀，不包含原生接口的 `/api/v1`；默认空字符串，表示浏览器同源请求。服务端发起请求时显式提供绝对地址 |
| `apiKey`      | 凭证。开源版映射为 `X-API-Key`；商业版由浏览器 Cookie 承载。函数形式每次请求重新求值                                   |
| `accountId`   | OpenViking 账号，对应数据空间                                                                                          |
| `userId`      | 账号内的用户                                                                                                           |
| `resourceId`  | 当前库；数据面接口需要它                                                                                               |
| `credentials` | 传给 `fetch` 的 `RequestCredentials`。默认 `same-origin`；商业版跨源调用时设为 `include`                               |

模块组件和独立 Hook 使用同一份可选 `requestOptions` 覆盖类型，即 `@openviking/react/client` 的 `OvRequestOptions`。按字段解析：**模块 / Hook 显式配置 → 最近的 Provider 配置 → 库默认值**；嵌套 Provider 在父配置上覆盖。不传字段或传 `undefined` 表示继承，`baseURL: ''` 则明确切回同源；覆盖里省略的身份字段继承 Provider 的值，显式传空字符串则清除它。覆盖不修改父 Provider，不影响其他模块实例。大部分接入只配置一次 Provider。两个模块的 `FileBrowserProps` 与 `ConsoleProps` 均已声明该字段。

Provider 已实现；业务 Hook 尚未实现，因此模块目前还不会发起请求。宿主在应用根部配置一次：

```tsx
import { OpenVikingProvider } from '@openviking/react/client';

<OpenVikingProvider
  baseURL={endpoint}
  apiKey={apiKey}
  accountId={accountId}
  userId={userId}
  resourceId={libraryId}
>
  <FileBrowser />
  <Console />
</OpenVikingProvider>;
```

模块通过内部 Hook 取客户端，只传需要覆盖的字段，例如访问另一个库时：

```ts
const rows = await client.request<FsEntry[]>({
  method: 'GET',
  path: OV_PATHS.fsLs,
  query: { uri },
  options: { resourceId: libraryId },
});
```

该 Hook 不对外导出：宿主不需要知道模块会发什么请求。纯视图组件完全不需要 Provider。当前身份可由 Context 下发，其中 `accountId` 对应数据空间，`userId` 对应该空间内的 User；身份作用域与请求配置分别维护。URI、查询词等模块业务参数仍通过模块 / Hook 入参提供。

- **路径只有一处拼接**：接口路径由 `@openviking/react/client` 的 `OV_PATHS` / `OV_PATH_BUILDERS` 集中维护，模块不写字面量。Client 保留 `baseURL` 的路径前缀。例如 `/gateway/openviking` 加 `/api/v1/fs/ls` 得到 `/gateway/openviking/api/v1/fs/ls`，不会因接口路径以 `/` 开头而丢掉网关前缀。模块不拼接域名或服务前缀。
- **响应只在 Client 解析**：区分 HTTP 失败与原生响应的 `status: 'error'`，保留服务端错误码、状态和请求标识。`request` 默认返回 `result`；二进制响应用 `responseType: 'blob'` 取原始字节，事件流用 `stream` 逐个消费，两者都不强转 JSON。`has_more` 这类 `result` 之外的分页元数据目前不返回，需要分页的模块按 `offset` / `node_limit` 自行判断。
- **配置与身份变化要隔离**：请求、缓存键和订阅以实际生效的服务地址及宿主当前连接 / 身份作用域隔离。切换服务或账号后取消旧请求并忽略过期结果；动态 headers 不代替身份切换信号，也不把 API Key 明文放入缓存键。
- **适配集中在接入层**：原生接口只需配置地址和 headers；存量网关若有额外封装，由宿主在共享 Client 接入层统一适配，并保留取消信号、响应状态及流。无法等价表达的能力通过显式扩展契约接入，不在每个模块增加宿主分支。

Provider 的继承遵循 [React Context](https://react.dev/reference/react/useContext)，请求层迁入参考[现有 Client 封装](../../../../../web-studio/src/shared/api/ov-client/client.ts)。传输层与 Provider 已按这些约定实现；首个模块 Hook 落地时再一起验证。

## 业务模块划分

两个模块目录及其占位组件已经建立，以下是各模块后续迁入的目标能力，尚未实现。组合视图所依赖的组件当前也均为占位实现，模块的真实实现需先等这些视图落地。产品引导、资源与 Skill 导入流程及同步配置由宿主负责，不提供对应业务模块。

| 模块           | Hook 与业务职责                                                                                   | 组合视图                                                                    |
| -------------- | ------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| `file-browser` | 统一管理目录、检索、当前 URI、预览加载与编辑保存；协调定位、刷新、历史与版本对比                  | 组合 `FileTree`、搜索交互与 `FilePreview`，提供目录和内容联动的文件浏览区域 |
| `console`      | 统一管理对话 / TUI 模式、公共上下文与工具栏；处理对话会话、消息流、附件、记忆提交及命令执行与记录 | 组合模块内的对话视图、`Tui` 与模式切换，提供完整的交互面板                  |

历史列表与版本对比先作为 `file-browser` 的预览能力，编译确认作为 `console` 中的 Bot 动作流程；有独立消费需求后再拆公开模块，不为每个按钮或弹窗建模块。公共视图组件按真实复用需求补充，表中组合视图不要求提前新增组件占位。

### 文件浏览模块

`file-browser` 计划覆盖目录导航、搜索与内容预览的整个联动区域，提供统一的 `useFileBrowser` Hook 与 `FileBrowser` 组合视图：

- Hook 统一返回目录、搜索、当前资源及预览的数据、状态与操作。内部可拆私有 Hook，宿主也可只使用统一 Hook 自行组装视图。
- 点击目录节点或搜索结果后，模块协调当前 URI、目录定位与预览加载；切换资源时清理旧请求，避免过期内容覆盖当前预览。
- 保存、重命名或删除资源后，模块刷新受影响的目录、搜索结果与预览；删除当前资源时同步清理选中状态和内容。
- 组合视图负责区域内的搜索、目录与预览布局。`FileTree`、`FilePreview` 等纯视图组件保持独立，不承担数据加载或跨组件编排。
- 连接 Agent、导入资源等操作入口通过宿主回调或插槽接入；模块不内置接入弹窗、导入流程或产品引导。配额提示等产品内容也通过宿主插槽接入。

以上交互已实现：`useFileBrowser` 取数与协调目录、搜索、预览的联动，`FileBrowser` 组合视图按控制台的上下文面板布局渲染。

### 交互面板模块

`console` 计划覆盖对话与 TUI 共用的整个面板，提供统一的 `useConsole` Hook 与 `Console` 组合视图：

- Hook 统一返回当前模式、公共上下文、对话与命令的数据、状态和操作；模式切换与工具栏操作在模块内协调，宿主也可只使用 Hook 组装视图。
- 对话与 TUI 使用同一服务、身份与宿主传入的资源上下文；各自维护消息历史、命令记录与运行状态，切换模式时保留各自状态。
- 对话能力包括会话、消息流、附件、记忆提交与编译动作；TUI 能力包括命令定义、执行、取消与执行记录，可用命令遵循宿主提供的能力。
- 组合视图负责模式切换、公共工具栏与内容区域，操作按当前模式分发。`Tui` 作为可独立消费的纯视图组件接入；对话视图暂无公共组件，由模块内部实现。
- 打开资源、定位产物或刷新目录通过事件交给宿主，与 `file-browser` 的联动不依赖对方私有状态；登录、身份切换与产品引导仍由宿主负责。

以上交互与 `useConsole` 仍为规划；当前仅导出 `Console` 占位组件和基础类型。

## 组合与扩展边界

- **当前资源由文件浏览模块协调**：`file-browser` 默认维护选中 URI 并同步目录、搜索和预览。宿主可受控传入 URI，或通过模块公开操作打开任务产物与 Bot 链接；模块上报资源变化，不直接操作其他模块的 Store 或路由。
- **预览页签属于同一资源**：个人终端不维护多个已打开文件的工作区页签。L0 / L1 / 目录内容以及经验看板等属于当前资源的内容视图；不据此收窄通用 `FilePreview` 的页签 API。
- **特定内容通过扩展接入**：经验看板、会话内容及 Wiki 等视图有独立的取数与展示逻辑，通过预览扩展页签接入，留在业务扩展或宿主；不让通用 `FilePreview` 识别这些业务类型。Skill 浏览复用目录与目录内容。
- **任务状态归任务数据源**：`console` 不上报任务进度或资源变更——它观察不到请求返回之后才完成的异步工作。需要响应任务结果的宿主自行从任务数据源读取；任务状态不在各模块复制维护。
- **身份由宿主提供**：库 / 数据空间 / User 切换与登录账号切换是不同操作。作用域切换视图由宿主提供；登录、账号保存、API Key 管理与失效处理仍归宿主。模块按显式作用域隔离请求、缓存与订阅，切换后清理旧操作。
- **可选能力由宿主接入**：删除、OAuth、OVPack、同步来源、隐私及境外提示由宿主提供服务能力、动作和文案。OVPack 使用独立的目录迁移服务契约，不混入普通资源上传；可作为目录操作扩展。当前不规划 TOS 导入、手写 Skill、独立编译页面或管理后台模块。
- **布局按模块边界负责**：`file-browser` 负责目录、搜索与预览的内部布局，`console` 负责对话 / TUI 模式切换及面板内部布局；宿主可通过插槽或独立 Hook 定制。两个区域的外部布局由宿主决定；引导、任务、帮助、账号等顶栏入口的位置也归宿主。

## 当前状态与开发入口

每个模块使用独立的 kebab-case 目录，入口文件与目录同名，已同步提供占位组件、基础类型和独立构建入口。真实业务迁入后，从同一模块入口分别导出 Hook 与组合视图；当前不提供占位 Hook 或请求配置 API。

开发命令和构建入口见 [前端工作区 README](../../../../README.md)，包结构见[包说明](../../README.md)，实施约束见本目录 [AGENTS.md](./AGENTS.md)。

已开放 `@openviking/react/modules/<模块>`，不提供分类聚合 JS 入口。模块 CSS 同时提供 `@openviking/react/modules/style.css` 与 `@openviking/react/modules/<模块>/style.css`，整包 `@openviking/react/style.css` 也包含模块样式。单模块 CSS 当前包含占位视图样式，迁入组合视图后须同步包含所需组件样式；后续单独使用 Hook 无需导入 CSS。
