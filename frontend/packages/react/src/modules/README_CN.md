# 区块

[English](README.md) / 中文

`modules/` 存放区块。每个区块由内部 Hook 和组合组件组成：Hook 加载数据、适配数据并维护状态，组合组件把 Hook 返回的数据和操作接入 `components/` 的视图。宿主直接使用组合组件，自行组装完整页面。

## 区块

| 区块            | 组件           | 公开入口                                  | 组合内容                                                          |
| --------------- | -------------- | ----------------------------------------- | ----------------------------------------------------------------- |
| `file-browser`  | `FileBrowser`  | `@openviking/react/modules/file-browser`  | 围绕同一个当前资源的目录树、搜索与文件预览                        |
| `console`       | `Console`      | `@openviking/react/modules/console`       | 同一面板内的对话（`VikingBot`）与命令（`CommandConsole`）两种模式 |
| `scope-cascade` | `ScopeCascade` | `@openviking/react/modules/scope-cascade` | 级联的库、空间与用户选择器                                        |

每个区块还有样式入口 `@openviking/react/modules/<区块>/style.css`，它包含所组合组件的样式。用法、属性和示例见[区块文档](../../../../docs/docs/zh/modules/index.mdx)。

## 职责

- `useFileBrowser` 与 `useConsole` 是内部实现。它们通过共享 Client 调用 OpenViking 原生 API，继承 Provider 的请求配置，管理加载、空态、错误、分页、刷新、重试，并清理仍在进行的请求。`ScopeCascade` 自己不发请求：三个加载函数由宿主提供。`Console` 只发命令请求，对话视图的数据和回调由宿主通过 `conversation` 传入。
- 组合组件把 Hook 返回的数据和操作交给 `components/` 的视图，并协调区块内的交互。
- 宿主直接使用组合组件，不导出无头 Hook 或控制器。
- 标准接入只需要服务地址与鉴权，不要求逐区块实现数据 Adapter。非标准网关由宿主在请求接入层统一适配。登录、账号作用域、路由、权限执行、页面布局及产品专属流程由宿主负责。
- 区块粒度以共同完成的交互流程为准：目录、搜索和预览组成 `file-browser`；对话与命令组成 `console`。宿主组合区块，不按内部视图分别接入。

依赖方向是单向的：`modules/` → `components/` → `shared/`，以及 `modules/` → `client/`。组件是纯视图，只接收数据、状态和回调，不取数，也不从 `modules/` 导入任何东西。仅一个区块使用的 Hook、类型和工具留在该区块目录；多个区块共用的代码再放入 `src/shared/`。

## 请求契约与默认配置

共享 Client 按 method、路径、query 和 body 向 OpenViking 原生 API 发请求，并解析原生响应外壳。它维护在 `src/client/`，从 `@openviking/react/client` 导出：基于 `fetch` 的 `createOvClient`、`OvClient` 接口、`OvRequestOptions`、`OvClientError`，以及路径表 `OV_PATHS` 与 `OV_PATH_BUILDERS`。该目录里只有 `OpenVikingProvider` 使用 React。区块 Hook 消费原生数据并派生视图所需的状态与操作，宿主因此不需要逐区块维护数据协议。客户端本身的用法见 [Client README](../client/README_CN.md)。

`OpenVikingProvider` 通过 Context 下发通用配置，并在 Provider 实例内复用稳定的 Client。宿主传的是字段值（`baseURL`、`apiKey`、`accountId`、`userId`、`resourceId`、`credentials`），请求头由 Client 组装；区块通过内部 Hook 取用客户端，不自己拼请求。Context 不缓存业务数据。Provider 挂载本身不取数，也不读取浏览器中保存的凭证。每个字段及其请求头见 Client README 的[配置字段](../client/README_CN.md#配置字段)。

`FileBrowserProps` 与 `ConsoleProps` 声明同一个可选的 `requestOptions` 覆盖，类型是 `OvRequestOptions`，按字段解析：**区块显式配置 → 最近的 Provider 配置 → 库默认值**。嵌套的 Provider 只覆盖它声明的字段。不传字段或传 `undefined` 表示继承，`baseURL: ''` 明确切回同源。覆盖里省略的身份字段继承 Provider 的值，显式传空字符串则清除它。覆盖不会修改父 Provider，也不影响其他区块实例。大部分接入只配置一次 Provider。`ScopeCascade` 没有 `requestOptions`：它通过宿主传入的三个加载函数工作。

宿主在应用根部配置一次 Provider：

```tsx
import { OpenVikingProvider } from '@openviking/react/client';
import { Console } from '@openviking/react/modules/console';
import { FileBrowser } from '@openviking/react/modules/file-browser';

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

区块内部通过 Hook 取客户端，只传需要覆盖的字段，例如访问另一个库时：

```ts
const rows = await client.request<FsEntry[]>({
  method: 'GET',
  path: OV_PATHS.fsLs,
  query: { uri },
  options: { resourceId: libraryId },
});
```

该 Hook 不对外导出：宿主不需要知道区块会发什么请求。`components/` 下的组件完全不需要 Provider。URI、查询词等业务参数是区块的 props，不属于 Provider 配置。

- **路径只有一处拼接**：接口路径集中在 `OV_PATHS` 与 `OV_PATH_BUILDERS`，区块不写字面量；Client 保留 `baseURL` 的路径前缀，例如 `/gateway/openviking` 加 `/api/v1/fs/ls` 得到 `/gateway/openviking/api/v1/fs/ls`，不会因接口路径以 `/` 开头而丢掉网关前缀。区块不拼接域名或服务前缀。
- **响应只在 Client 解析**：HTTP 失败与原生响应的 `status: 'error'` 都抛出 `OvClientError`，保留服务端错误码、状态和请求标识。`request` 返回 `result`；`requestEnvelope` 返回 `{ result, hasMore }`，用于分页列表，FileBrowser 就用它配合 `node_limit` 与 `offset` 分页。`responseType: 'blob'` 返回原始字节，`stream` 逐个产出事件流事件，两者都不强转 JSON。
- **身份与配置变化要隔离**：新的客户端（Provider 配置变了）、`requestOptions` 里变化的字段，以及 FileBrowser 的 `rootUri` 变化，都会让区块视为新的作用域：中止仍在进行的请求并丢弃它们的迟到结果，旧账号的响应因此不会落进新视图。以函数形式传入的身份无法自己通知变化：FileBrowser 按函数引用区分它们，Console 则提供显式的 `scopeKey`，身份变化时要同时更换它。
- **适配集中在接入层**：原生接口只需要地址和请求头。存量网关由宿主在共享 Client 接入层统一适配一次，并保留取消信号、响应状态及流。无法等价表达的能力通过扩展契约接入，不在每个区块增加宿主分支。

## 区块边界

- **当前资源由 FileBrowser 协调**：它在目录、搜索和预览之间同步当前 URI。宿主传入 `uri` 与 `onUriChange` 即可受控地决定 URI，例如打开 `Console` 通过 `onOpenUri` 上报的链接。区块只上报变化，不直接操作其他区块的状态或路由。
- **身份由宿主提供**：切换库、空间或用户，与切换登录账号是不同的操作。`ScopeCascade` 提供库、空间与用户的选择；登录、账号保存、API Key 管理与失效处理仍归宿主。
- **看板是组件**：`wiki-dashboard` 与 `experience-dashboard` 是 `components/` 下的纯视图。FileBrowser 的私有 Hook 加载原生数据、适配成看板的展示模型并渲染。`dashboardKinds` 限制 FileBrowser 识别哪些看板，`renderDashboard` 用宿主自己的视图替换内置视图，返回空值则保留内置视图。
- **布局**：每个区块负责自己内部区域的布局，宿主通过 props 和插槽定制。外部布局以及区块在页面中的位置由宿主决定。

## 新增或修改区块

每个区块使用独立的 kebab-case 目录，入口文件与目录同名。`index.ts` 导出组合组件及宿主需要的类型，Hook 保持内部实现。公开的 JS 与 CSS 入口要同时加入 `package.json` 的 `exports` 和 `rslib.config.ts`，并在 `modules/style.css` 中引入该区块的 `style.css`。区块样式包含所组合组件的样式。不提供聚合 `modules/` 的 JavaScript 入口。

步骤、命令与 CI 见[前端工作区 README](../../../../README.md)，包的入口见[包说明](../../README_CN.md)，实施约束见本目录 [AGENTS.md](./AGENTS.md)。
