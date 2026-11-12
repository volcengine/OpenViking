# Blocks Agent Guide

适用于本目录及其子目录，同时遵循 [Frontend Agent Guide](../../../../AGENTS.md)。目录用途与开发入口见 [README](./README.md)。

## 区块职责

- 当前区块规划聚焦个人终端，不包含管理后台；区块粒度与组合边界以 [README](./README.md) 为准，不再以工作台整页作为交付单元。
- 按可独立接入的业务能力组合 Hook 与 `components/`，不承载完整页面。Hook 管理请求、响应适配、业务数据状态和操作；组合视图调用 Hook，将数据与回调传给组件。不把请求或业务编排下沉到组件及其内部 Hook。
- 以共同完成的交互流程划分区块，共享状态与联动在区块内维护。目录、搜索和预览组成 `file-browser`，由统一 Hook 协调资源选择、定位、加载与刷新；对话与命令控制台组成 `console`，统一管理面板模式、工具栏与上下文，各自维护会话和执行状态。不按这些内部视图分别开放区块。内部可拆私有 Hook，纯视图组件仍可独立消费。
- 区块 Hook 只供内部组合组件使用，不导出无头 Hook 或控制器。通用展示与交互复用组件层，区块私有视图、Hook、类型和工具留在区块内。
- 默认通过共享 Client 消费 OpenViking 原生 API 的路径、入参与响应类型，继承 Provider 请求配置；区块通过可选 `requestOptions` 局部覆盖，不要求宿主逐区块提供 Adapter。不硬编码服务地址、凭证或账号，不引用宿主私有接口类型。非标准网关在宿主请求接入层适配；认证、账号作用域、路由、权限执行及产品专属流程由宿主负责。
- 加载、错误、分页、刷新、重试及流式连接等数据状态与异步清理由区块 Hook 负责。资源或账号切换、卸载时清理请求和订阅，避免过期结果覆盖当前状态。跨实例共享行为通过明确的数据服务接入。
- 依赖方向为 `modules → components`，区块 Hook 调用共享 Client，Client 不依赖 React Context；组件层不反向依赖区块。配置继承、URL 拼接、headers 与响应解析遵循 [请求约定](./README_CN.md#请求契约与默认配置)，不在区块中重复实现。跨区块通用 Hook 有真实复用需求后再放入 `src/shared/`。
- 区块内部联动由区块负责；宿主通过资源 URI、作用域、事件回调协调不同区块，不直接依赖区块的私有 Hook 或 Store。`file-browser` 默认维护当前资源，允许宿主受控传入或通过公开操作定位；宿主无需重复组装目录、搜索与预览的联动。区块间布局、路由、账号切换及权限执行归宿主。

## 新增区块

- 每个区块独占一个 kebab-case 目录，入口文件与目录同名。从同一入口导出组合组件和必要类型，Hook 保持内部实现。
- 提供 `modules/<区块>` JS 与 `modules/<区块>/style.css` 样式入口，并纳入 `modules/style.css`、整包 `style.css`；区块样式包含当前视图及依赖组件样式。不提供 `modules` 聚合 JS 入口。同步维护公开接口、可运行示例及中英文文档。
- 示例使用固定公开数据或独立模拟服务，覆盖加载、失败、重试和作用域切换，并展示组件接入方式。
- 验证与交付遵循 Frontend Agent Guide；本目录的 `AGENTS.md` 和 `README.md` 须由 Git 跟踪。

VikingBot 对话是公开无头模块的明确例外：`modules/viking-bot` 导出 `useVikingBotChat`、`useVikingBotSessions` 及必要契约，供 Console 与独立 VikingBot 宿主共同使用；不导出组合组件，不提供 CSS 入口，不从根入口或 Console 入口转导出 Hook。其他模块 Hook 仍按上述内部边界执行。
