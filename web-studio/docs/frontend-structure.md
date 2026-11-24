# Web Studio 前端工程结构

代码按用户工作台、管理后台和共享连接流程组织。目录归属表达功能职责；服务端授权及前端能力检查共同决定页面是否可用，目录本身不授予权限。

## 目录职责

| 目录                                | 职责                                                             |
| ----------------------------------- | ---------------------------------------------------------------- |
| `src/app/`                          | 全局启动、布局、导航、Provider 组合、路由入口及授权边界          |
| `src/features/workbench/<feature>/` | 文件系统、检索、会话、任务、监控等用户数据功能                   |
| `src/features/admin/<feature>/`     | 用户、用户组、抽取规则、Account 配置、服务端配置、Bot 和网关管理 |
| `src/features/connection/`          | 连接配置、凭据存储、身份选择、角色校验、模式切换、首次引导       |
| `src/features/resource-acl/`        | 管理页与资源页共用的资源权限逻辑和界面                           |
| `src/features/oauth/`               | 授权同意、跨设备验证和身份目录                                   |
| `src/shared/`                       | UI 基础组件、通用呈现组件、传输层、纯数据模型和工具              |
| `src/routes/`                       | TanStack 文件路由声明、查询参数校验和重定向装配                  |
| `src/gen/`                          | 生成客户端，按接口生成流程维护                                   |
| `src/i18n/`                         | 国际化运行时和翻译资源                                           |

功能内部按需使用 `components/`、`hooks/`、`lib/`、`types/`，页面入口放在功能根目录。测试就近放置；路由接线测试保留在路由旁。只有产生多份同类代码时才增加子目录。

## 依赖方向

- 路由装配 app 边界和 feature 页面；feature 不依赖路由文件。页面通过 `getRouteApi` 或 Router hooks 获取类型化参数，避免反向引用路由声明。
- app 组合 feature 和 shared；feature 可使用 shared。shared 不依赖 app、feature 或路由实现。
- workbench 与 admin 不互相导入。两者共用的文件模型、URI、会话行等属于 shared；需要身份和权限状态的通用业务仍由 connection、resource-acl、oauth 拥有。
- connection 不依赖 workbench 或 admin。服务端模型配置、连接测试和配置文件 API 归 admin/server-configuration。
- 同一功能内部可以相互引用。跨功能引用使用明确的源文件入口，例如 connection 的 `hooks/use-app-connection`、`lib/types`、`lib/studio-permissions`，resources 的 `lib/api`、`hooks/viking-fm` 和复用组件。需要页面的路由直接导入页面入口。

不建立把所有页面、组件和模型汇总在一起的全量 barrel。这样公共 hooks 不会间接拉入所有页面，TanStack Router 的自动代码分割保持有效。新增共享 API 时应明确其所有者；只有跨模式、且不依赖业务身份的基础能力才下沉 shared。

## 生命周期与凭据

`app/layouts/app-shell` 负责首次引导及连接 Provider 装配；`connection-boundary` 负责按连接和身份隔离页面生命周期；`studio-layout` 负责布局与导航。连接模块的 React Provider 负责状态协调，`connection-runtime` 负责凭据选择、规范化、作用域标识和客户端同步。

管理权限、工作台数据凭据、trusted 身份声明和持久化规则保持原有契约，见[权限矩阵](./permission-matrix.md)和[管理态拆分](./admin-workbench-refactor.md)。路由地址、旧链接兼容跳转和本机连接记录格式保持兼容。

## 验证

`npm test` 中的 `script/architecture.test.ts` 检查层间依赖和薄路由约束，覆盖静态导入、再导出和动态导入。`components.json` 的生成路径同步指向 shared，避免新增基础组件回到旧目录。

提交前运行完整测试和 `npm run build`；build 包含 ESLint 和国际化检查。新增目录或移动模块时检查 Worker、动态加载、测试 mock、生成配置和文档链接，不手工移动或改写生成客户端。

工作台身份与管理 Account 分别保存在连接配置中，切换区域时先恢复作用域并清理缓存，再挂载子页面。最后验证的 `authMode` 仅用于首次入口提示；运行时重新探测服务端模式后才决定是否可以使用 trusted 凭据，不能根据缓存值授予权限。
