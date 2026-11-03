# Studio 入口分层与枚举

日期：2026-11-02；登录组合与 STG 预览更新：2026-11-03。范围：部署入口、认证页面组装、管理能力与默认落点的现有策略收口。

本次不改变火山 ROOT 的跳转与管理接入。其产品目标是验证 ROOT 后进入管理后台，但用户已将这部分延后，当前保留原有托管行为。邮箱账号服务仍待联调。

## 目录与依赖

```text
src/platform/
  studio-contract.ts              # 跨模块稳定枚举与类型
  connection/
    types.ts                      # 连接数据契约
    configuration.ts              # 纯配置规范化与数据凭据选择提示
    service-provider.ts           # 服务识别及显式选择
  deployment/
    config.ts                     # 唯一读取部署环境配置的位置
    types.ts                      # 登录候选、地址规则与入口配置契约
    editions/self-hosted.ts        # 私有化配置
    editions/volcengine.ts         # 官方火山配置
    experience.ts                 # 启动选择配置及连接准入
  entry/
    paths.ts                      # 登录及主要区域入口地址
    resolve-entry.ts              # 登录后默认落点
  access/management.ts            # 管理能力与入口策略
  access/features.ts              # 功能目录及支持规则
src/app/composition/auth-entry.tsx # 将配置和地址默认值传给认证页面
```

启动路径：`main.tsx → initializeStudioExperience → edition 配置 → AuthEntry → 认证页面`。

生产启动前校验并选择一次配置，后续修改 window 上的原始对象不会切换已运行的应用。孤立测试可以调用纯函数 `resolveStudioExperience(config)`；未启动整个应用的调用方保留解析当前配置的兼容入口。

`platform` 不依赖其他项目层、React 或存储。业务使用已选定的配置或策略结果，不读取部署输入。旧的 `shared/lib/studio-deployment.ts`、`features/connection/lib/studio-permissions.ts` 及类型导出保留兼容出口。服务识别及功能支持规则也已迁到 platform；导航匹配归 app，能力响应解析和网络探测归 connection。旧 `shared/lib/studio-service.ts` 只保留兼容出口，架构检查禁止新增生产调用。

## 独立维度

枚举采用 `as const` 对象加同名联合类型，保留已有配置、存储与协议字符串。

| 枚举              | 值                                                  | 职责                                          |
| ----------------- | --------------------------------------------------- | --------------------------------------------- |
| `StudioEdition`   | `self-hosted`、`volcengine`                         | 产品部署版本，不从域名推断                    |
| `ServiceProvider` | `opensource`、`volcengine`、`unknown`               | 连接服务，独立于部署版本                      |
| `AuthMethod`      | `api-key`、`email`                                  | 登录交互方式                                  |
| `LoginEntry`      | `email`、`volcengine-api-key`、`openviking-api-key` | 区分三个具体登录入口                          |
| `StudioSurface`   | `auth`、`workbench`、`management`                   | 页面区域，`StudioMode` 仅包含后两个           |
| `ConnectionRole`  | `root`、`admin`、`user`、`unknown`                  | 验证得到的身份，不从登录方式或 Key 字符串推断 |
| `ServerAuthMode`  | `api_key`、`trusted`、`dev`、`oidc`、`ldap`         | 服务协议模式，不与 UI 的 `api-key` 混用       |
| `StudioEntry`     | `/login`、`/login/email`、`/login/api-key` 等       | 稳定入口地址；文件路由仍按 TanStack 约定声明  |

开发、测试、生产是发布环境，不通过这些枚举隐式授予权限。私有化部署绑定域名仍然是 `self-hosted`。

2026-11-03：新增独立的 `StudioEnvironment`（`production` / `stg`）。原始部署配置在 Vite base 为 `/workbench-stg/` 时选择 STG；可由运行时 `environment` 或构建 `VITE_STUDIO_ENVIRONMENT` 显式覆盖。环境决定默认火山地址，不改写原始发布配置的 `edition`；仅 STG 的启动预览可以覆盖实际入口组合。默认 STG API 为 `https://api.vikingdb-stg.cn-beijing.volces.com/openviking`。服务识别同时支持生产与 STG 官方域名，使用 Bearer 认证。STG 恢复及激活火山连接时要求匹配其配置地址，不将生产 Key 自动搬到 STG。旧向导也复用入口配置的地址。

`platform/deployment/experience.ts` 的 `resolveStudioStartup` 仅在 STG 选择预览组合：默认官方，允许私有化，保持原环境与托管地址；生产忽略预览选择。`app/composition/studio-preview.ts` 负责读取标签页 `sessionStorage`，切换时保存选择并整页重载到保留 Vite base 的 `/login`；浮动控件由同目录的 `studio-preview-control.tsx` 组装。

## 登录配置

| 部署     | 默认登录 | API Key 候选    | 地址规则                    |
| -------- | -------- | --------------- | --------------------------- |
| 私有化   | API Key，默认火山托管 | 火山托管、自部署 OV | 火山地址只读，OV 地址可输入 |
| 官方火山 | 企业登录 / API Key 选择页 | 仅火山 | 固定为部署配置中的火山地址，API Key 页隐藏地址 |

两个部署中的火山 API Key 候选具有相同的服务及地址语义。官方选择页进入 `/login/email` 或 `/login/api-key`，两页均可返回选择页；官方 API Key 页隐藏服务选择，自部署 OV API Key 选项只在私有化出现。既有邮箱、密码恢复和邀请公共页面保留兼容访问；页面可访问不表示邮箱服务已接入。

`ApiKeyLogin` 接收 `ApiKeyLoginConfig` 和 `initialAddress`，不访问部署配置、环境变量或当前 URL。它按候选展示服务选项，按地址规则呈现可编辑或只读字段，并在切换服务时清空 Key。

`AuthEntry` 选择默认登录页并注入配置及地址默认值。`PublicAuthPage` 负责公共跳转和表单组装，不判断部署版本。

## 后续约束

- 配置表达产品支持范围，不是认证凭证。角色权限仍需身份及接口校验。
- 管理入口、能力和默认落点集中在平台模块，现有路由保护消费同一策略。
- 服务能力、对象权限、网络状态和页面交互仍按各自职责判断，不全部塞进版本配置。
- 新版本在 `editions/` 增加配置并注册；枚举映射有完整类型约束。业务页面不增加版本条件。
- 协议确实不同才增加 Adapter；已有请求、凭据迁移、取消请求和身份缓存隔离继续复用。
- 认证界面已独立归 `features/auth`，Provider 的生命周期已交给连接控制模块；文件系统内部模型仍按业务模块逐步迁移。

`script/architecture.test.ts` 检查平台依赖方向、业务层不读取原始部署配置、不直接访问 `edition`，以及部署输入只由配置模块读取，并保留既有薄路由约束。

## CR 后续实现

2026-11-02 按 CR 收口执行边界，具体变更与验证见[连接与路由重构记录](studio-connection-refactor.md)。

- 固定火山地址、服务候选和 Gateway 发现策略同时由实际编辑、启动及连接变更路径执行。
- 登录页面调用 `loginAndActivate`；连接编辑器调用 `submitProfile`。准确相同的连接重复登录复用已有记录，改凭据会重新验证。
- `VerifiedConnection`、`ConnectionAccessContext` 与持久化配置分开；默认落点只消费 `StudioEntryAccess` 和服务/偏好，不接收 Key 或独立的 `role`/`canManage` 参数。历史入口函数保留兼容包装。
- `/login` 的索引及子路由、密码恢复和邀请路由直接渲染 `AuthEntry`。路由元数据决定公共布局与主题，AppShell 不再维护认证路径渲染分支。

后续已完成连接生命周期、认证模块和接口归属拆分，见[生命周期与认证模块拆分](studio-lifecycle-auth-refactor.md)。

## 入口分层时的历史验证

以下记录属于前一轮入口配置改造；本轮验证以重构记录为准。

- 75 个测试文件、608 项回归通过，覆盖连接、身份、权限、页面与路由：`/tmp/ov-entry-refactor-regression.log`。
- 最后将地址默认值从表单移到入口后，6 个文件、81 项定向验证通过，新增配置注入与切换服务清空 Key 检查：`/tmp/ov-entry-final-tests.log`。
- `npm run build` 通过，包含 lint 和 i18n 检查：`/tmp/ov-entry-refactor-build.log`。
- `tsc` 输出与上一轮基线 `/tmp/ov-auth-pages-tsc.log` 一致，仍有原有诊断，无新增错误。
- 浏览器分别检查私有化及官方默认登录、切换方式、服务选择和固定地址；未提交真实凭据。临时官方预览已停止，原 3017 服务保留。

以上是本地验证，不代表生产部署或火山 ROOT 真实接口联调完成。
