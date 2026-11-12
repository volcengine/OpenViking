# React Agent Guide

适用于 `frontend/packages/react/**`，同时遵循 [Frontend Agent Guide](../../AGENTS.md) 和更深目录的规则。

## 公开导出

- 目录以 `index.ts` 作为唯一出口，包根入口 `src/index.ts` 只从这里取。
- 根入口只导出各业务组件、各区块的组件本体与主 Props 类型（`<组件名>Props`），作为最常用的快捷方式。
- 子类型、回调参数类型、行与选项等数据结构、句柄与文案包，一律只从 `components/<组件>`、`modules/<区块>` 子路径导出。根入口不聚合它们。
- `src/client/` 的请求客户端不依赖 React，从 `@openviking/react/client` 交付；它不进入根入口。
- 依赖可选 peer 的组件不进入根入口：`FilePreview` 的类型引用 `@ark-design/ui`，放进根声明会让该可选 peer 对只用其他根导出的消费者变成必需。
- 新增或调整导出时，同步维护 `package.json` 的 `exports`、Rslib 入口与声明路径，并从真实打包产物验证根入口与各子路径都能解析。

## Props 字段顺序

公开 Props 按 必选参数 → 可选参数 → 回调 → 插槽 → 文案 → `requestOptions` 排列，回调一律可选。同一类别内保持逻辑相邻，例如受控值与其回调、初始值与其受控值。

由 lint 规则 `ov-ui/props-order` 检查，作用于 `packages/react/src/**/types.ts`。

## useEffect 使用门槛

- 新增或修改组件、自定义 Hook 时，非必要不使用 `useEffect`。只有需要在渲染提交后，将组件与明确的外部系统保持同步，且更直接的声明式 API、事件处理或已有专用 Hook 无法满足时，才允许使用。
- 外部系统包括浏览器订阅、非 React 控件和网络服务；组件内部的 props、state 及其派生值不是外部系统。“挂载后执行”“依赖变化时执行”或“只运行一次”本身不能证明必要性。
- 不得通过改名、封装自定义 Hook 或替换为 `useLayoutEffect` 绕过本规则。按 React 官方的 [You Might Not Need an Effect](https://react.dev/learn/you-might-not-need-an-effect) 判断是否可以移除 Effect。

## 优先选择替代方式

| 场景                                                             | 应采用的方式                                                                                                                      |
| ---------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| 根据 props/state 计算文案、过滤结果或选中项                      | 渲染时直接计算；仅在确有性能需要时用 `useMemo`，不另存 state 再用 Effect 同步                                                     |
| 点击、提交、删除等用户操作，以及由此触发的请求、通知或父组件回调 | 在对应事件处理器中执行，必要时提取共享函数；不通过 state 标记再由 Effect 接力执行                                                 |
| props 改变后重置状态，或同步父子组件状态                         | 优先使用受控状态、提升状态；身份变化需要重置整个子树时使用 `key`                                                                  |
| 订阅外部可读状态，如 Store 或浏览器在线状态                      | 优先复用订阅 API，或使用 [`useSyncExternalStore`](https://react.dev/reference/react/useSyncExternalStore)，不手写 Effect 镜像状态 |

数据获取优先复用宿主、框架或已有数据层能力。区块 Hook 或宿主数据层确需随当前查询参数同步服务端结果时，可以使用 Effect；不能一概改成点击事件，否则 URL 导航等变化可能漏取数据。遵守分层边界：`components/` 及其私有 Hook 仍不得取数或创建业务连接。

## 每个 Effect 必须说明必要性

在每一个实际 `useEffect` 调用的紧邻上方添加注释；自定义 Hook 内的调用也不例外。注释必须回答：

1. 正在与哪个具体外部系统同步，哪些输入决定同步内容？
2. 为什么不能通过渲染计算、事件处理或已有专用 API 完成，必须由 Effect 随组件保持同步？
3. 依赖变化或卸载时如何清理、撤销或使旧结果失效？无需清理时说明原因。

例如，为需要持续响应容器尺寸变化的视图添加订阅时：

```ts
// useEffect 必要性：用 ResizeObserver 订阅当前容器的尺寸；
// DOM 节点提交后才可订阅，且尺寸也会因外部布局变化，不能只在点击事件中更新。
// 容器更换或组件卸载时 disconnect，避免旧订阅继续回调。
```

仅写“初始化”“监听变化”“同步状态”或“避免重复执行”不满足要求。

## 必要 Effect 的实现要求

- 按 [`useEffect` 官方约定](https://react.dev/reference/react/useEffect) 声明完整依赖；不得靠省略依赖或关闭 Hooks lint 消除重跑。
- 按 [Synchronizing with Effects](https://react.dev/learn/synchronizing-with-effects) 对称地建立和清理订阅、连接、定时器等资源；异步请求取消或忽略过期结果，避免旧结果覆盖新状态。
- 保证开发环境 Strict Mode 的建立、清理、再建立过程正确；不得用“只执行一次”的 ref 标记或全局变量掩盖缺失清理的问题。
- 浏览器 API 在客户端的必要阶段访问，保持模块导入和服务端渲染安全；不要把副作用搬进渲染函数或 `useMemo` 来规避 Effect。

业务 Hook 与控制器是区块内部实现，不从根入口或区块子路径导出；`components/` 保持纯 UI、接口无关。

VikingBot 对话是公开无头模块的明确例外：`modules/viking-bot` 导出 `useVikingBotChat`、`useVikingBotSessions` 及必要契约，供 Console 与独立 VikingBot 宿主共同使用；不导出组合组件，不提供 CSS 入口，不从根入口或 Console 入口转导出 Hook。其他模块 Hook 仍按上述内部边界执行。
