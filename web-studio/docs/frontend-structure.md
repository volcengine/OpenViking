# Web Studio 前端工程结构

代码按用户工作台、管理后台和共享连接流程组织。目录归属表达功能职责；服务端授权及前端能力检查共同决定页面是否可用，目录本身不授予权限。

## 目录职责

| 目录                                | 职责                                                                 |
| ----------------------------------- | -------------------------------------------------------------------- |
| `src/platform/`                     | 部署配置、稳定枚举、连接契约、入口与能力的纯策略                     |
| `src/app/`                          | 全局启动、布局、导航、Provider 组合、路由入口及授权边界              |
| `src/features/workbench/<feature>/` | 文件系统、检索、会话、任务、监控等用户数据功能                       |
| `src/features/admin/<feature>/`     | 用户、用户组、抽取规则、Account 配置、服务端配置、Bot 和网关管理     |
| `src/features/auth/`                | 公共认证页面、表单校验、密码恢复和邀请界面；通过连接登录接口建立连接 |
| `src/features/connection/`          | 连接生命周期、配置和凭据存储、身份选择、角色校验及区域切换           |
| `src/features/resource-acl/`        | 管理页与资源页共用的资源权限逻辑和界面                               |
| `src/features/oauth/`               | 授权同意、跨设备验证和身份目录                                       |
| `src/shared/`                       | UI 基础组件、通用呈现组件、传输层、纯数据模型和工具                  |
| `src/routes/`                       | TanStack 文件路由声明、查询参数校验和重定向装配                      |
| `src/gen/`                          | 生成客户端，按接口生成流程维护                                       |
| `src/i18n/`                         | 国际化运行时和翻译资源                                               |

功能内部按需使用 `components/`、`hooks/`、`lib/`、`types/`，页面入口放在功能根目录。测试就近放置；路由接线测试保留在路由旁。只有产生多份同类代码时才增加子目录。

## 依赖方向

- 路由装配 app 边界和 feature 页面；feature 不依赖路由文件。页面通过 `getRouteApi` 或 Router hooks 获取类型化参数，避免反向引用路由声明。
- platform 只依赖平台内部模块，不依赖 app、features、shared、路由、React 或存储。app 组合 feature、platform 和 shared；feature 可使用 platform 和 shared。shared 不依赖 app、feature 或路由实现，旧服务类型兼容出口可引用 platform；生产代码直接引用规则的所有者。
- workbench 与 admin 不互相导入。两者共用的文件模型、URI、会话行等属于 shared；需要身份和权限状态的通用业务仍由 connection、resource-acl、oauth 拥有。
- auth 通过 `features/connection/login` 建立连接，connection 不依赖 auth。connection 不依赖 workbench 或 admin。服务端模型配置、连接测试和配置文件 API 归 admin/server-configuration。
- 同一功能内部可以相互引用。连接模块对业务公开 `access.ts`（身份/能力读取及连接操作）和 `login.ts`（建立连接）；类型契约归 `lib/connection-contract`，稳定服务契约归 platform。需要完整凭据的既有协议调用方暂时保留内部接口，并由架构测试明确列举；普通业务页面使用公开接口。resources 的 `lib/api`、`hooks/viking-fm` 和复用组件保留现有跨功能入口。需要页面的路由直接导入页面入口。

不建立把所有页面、组件和模型汇总在一起的全量 barrel。这样公共 hooks 不会间接拉入所有页面，TanStack Router 的自动代码分割保持有效。新增共享 API 时应明确其所有者；只有跨模式、且不依赖业务身份的基础能力才下沉 shared。

## 生命周期与凭据

公共认证页面由各自文件路由直接渲染；路由 `staticData.studioPublic` 决定是否进入 `app/layouts/connected-studio-layout`。该布局负责启动检查及连接 Provider 装配，`app-shell` 只保留视觉外壳。`connection-boundary` 负责按连接和身份隔离页面生命周期；`studio-layout` 负责布局与导航。

连接模块的接口按职责分开：

- `connection-policy` 执行部署准入及 Gateway 发现规则；客户端变更和持久化前均检查，表单候选也来自同一部署配置。
- `connection-operations` 提供登录并激活、提交配置、激活及删除配置；验证、查重、保存、失效结果检查由连接模块处理，页面只提交意图和展示结果。
- `connection-identity` 区分输入配置、临时验证结果和当前访问上下文，分别记录数据身份与管理身份。导航只消费计算出的可进入区域，不再接收整份凭据及独立权限布尔值。
- `ConnectionController` 拥有服务探测、重连、配置激活、数据/管理身份切换、缓存失效和请求取消，发布统一的 `ConnectionSnapshot`。React Provider 通过 `useSyncExternalStore` 订阅状态、接入导航和清缓存，并在挂载/卸载时启动或停止控制模块。
- `connection-runtime` 负责凭据选择、作用域标识和客户端同步；`connection-verification` 负责登录校验，`profile-bootstrap` 负责启动恢复及保存；纯配置规范化和服务识别归 `platform/connection`。

旧 `verifyStudioSetup`、`studioSetupDestination` 和 `studio-mode` 的入口函数保留为兼容出口，新登录和真实路由使用新的业务接口。生命周期已移出 React Provider；控制模块在停用时取消探测并使旧操作失效。它仍复用现有凭据存储和请求实现。

管理权限、工作台数据凭据、trusted 身份声明和持久化规则保持原有契约，见[权限矩阵](./permission-matrix.md)和[管理态拆分](./admin-workbench-refactor.md)。路由地址、旧链接兼容跳转和本机连接记录格式保持兼容。

## 产品规则归属

`platform/access/features` 拥有功能目录和支持规则；`platform/connection/service-provider` 拥有服务识别；`app/navigation/feature-path` 负责页面匹配；`connection/lib/capability-response` 负责能力响应识别，`capabilities` 负责网络探测。基础 UI 和通用传输仍归 shared。

本轮目录及验证详情见[生命周期与认证模块拆分](studio-lifecycle-auth-refactor.md)。

## 验证

`npm test` 中的 `script/architecture.test.ts` 检查层间依赖和薄路由约束，覆盖静态导入、再导出和动态导入。`components.json` 的生成路径同步指向 shared，避免新增基础组件回到旧目录。

提交前运行完整测试和 `npm run build`；build 包含 ESLint 和国际化检查。新增目录或移动模块时检查 Worker、动态加载、测试 mock、生成配置和文档链接，不手工移动或改写生成客户端。

工作台身份与管理 Account 分别保存在连接配置中，切换区域时先恢复作用域并清理缓存，再挂载子页面。最后验证的 `authMode` 仅用于首次入口提示；运行时重新探测服务端模式后才决定是否可以使用 trusted 凭据，不能根据缓存值授予权限。
