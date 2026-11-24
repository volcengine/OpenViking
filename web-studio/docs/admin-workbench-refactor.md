# Studio 管理态与用户工作台拆分

管理入口为 `/admin`，成员、用户组、权限策略、记忆模板位于 `/admin/users`。旧 `/users` 及其子路径通过兼容路由跳转。管理态连接配置位于 `/admin/settings`，用户工作台连接配置位于 `/settings`，两者共用同一页面及本地配置。OpenViking 功能配置统一位于 `/admin/configuration`；Root 的服务端参数编辑位于 `/admin/configuration/server`。

左侧连接设置导航、连接选择器和鉴权错误恢复入口按当前路由选择对应设置地址；刷新保留模式。连接检查未完成或服务不可达时，两种模式的本地连接设置都可打开，配置草稿不因健康状态变化被清除。

## 凭据契约

- `adminApiKey` 保存控制面凭据，角色由服务端校验；只有 ROOT 或 ADMIN 才能进入管理页面。ADMIN 限制在服务端授权的 Account。
- `apiKey` 保存数据访问凭据。API Key 模式中，服务端解析出的 Account/User 为权威身份。
- Root Key 误填到普通 Key 输入框时，经服务端确认后归类为管理凭据，不用于数据访问。
- 普通 Key 解析为 ADMIN 且未配置单独控制凭据时，该 Key 同时提供本 Account 管理能力。
- 普通用户不能通过路由、默认入口偏好或已保存的管理 Key 获得管理权限。
- 文件、会话、检索、资源 ACL 等数据请求不能在缺少 User Key 时自动回退到 Root Key。trusted 模式使用服务端验证的 Root 凭据授权 Account/User 身份声明；工作台数据请求和管理请求分别遵守 trusted 协议。
- 仅管理态允许对只读运维端点使用控制凭据；工作台禁止 Root 兜底，任务取消也只使用数据凭据。

## 公共页面与身份恢复

跨设备验证页可由两种模式进入。页面外壳按当前连接记录进入前的模式，刷新后继续使用该模式；记忆按连接配置隔离，不能授予管理权限。明确进入用户业务页或管理页时，以目标路由更新模式。

Root-only 连接仍可管理 Account 级 ACL 配置，但资源目录与授权列表在选择数据身份之前不发出请求。页面直接提供账号管理员身份选择；切换成功后重新加载目录，保留 Root 管理凭据。

## 服务端权限依据

`api_key` 模式 Root 的路径限制定义于 `openviking/server/auth/plugins/api_key.py`。

| 能力 | 权限 |
| --- | --- |
| Account 创建、列表、详情、删除；迁移；全局配置、模型连接测试、重启 | ROOT |
| User、用户组、Account 配置、记忆模板 | ROOT / 本 Account ADMIN，具体操作仍受服务端限制 |
| Account 模型等受限配置字段 | ROOT |
| Bot Studio 连接与安装管理 | ROOT，另需服务能力可用 |
| 用户列表、Key 重置 | 多角色可调用，返回字段及可操作对象不同 |
| 统计、日志、Observer、任务 | 按端点和身份确定范围，不能直接推断为全服务范围 |
| 资源 ACL | 数据身份及资源 MANAGE 权限；Root Key 不能直接调用 |

## 后续交付范围

本轮建立管理路由、服务配置分离、入口与数据凭据防回退的基础。以下仍需独立完成，不能视为本轮已交付：

- 管理监控、任务、日志页面与对应凭据范围适配。
- 抽出完整工作台版本包，并在独立站点宿主验证消费。
- 邮箱 Session 的宿主适配与服务端接口联调。

所有工程变更限定在 `web-studio/`。服务端仅作为接口与鉴权依据。

## 顶部导航

全局顶部栏左侧显示 OpenViking Logo，以及连接配置、Account 空间和 User 三个独立选择器；右侧放置管理模式／工作台切换和 Studio 设置。连接设置作为左侧常驻导航显示；右侧设置菜单仅展示默认入口、语言和主题等偏好。侧栏只保留功能、接入和资源导航。窄屏保留 Logo 图标和空间／User 选择器，通过折叠菜单打开功能导航。

## 服务管理页面归属

OpenViking 配置通过管理侧栏的统一入口访问，Root 和 Account Admin 可配置当前账号的功能；服务端参数编辑 `/admin/configuration/server` 作为“服务管理”分组下独立的“服务端配置”导航，仅 Root 可见和访问；Account 配置页不再展示跳转卡片。网关管理放在“服务管理”分组，页面迁至 `/admin/gateway/*`，开源服务的 Root 和 Account 管理员可进入，网关内部仍根据 `/gateway/admin/access` 和独立 Management Key 判断读写权限。用户工作台不再显示网关管理，普通 User 直达新地址时不挂载管理页面。旧 `/gateway/*` 链接会保留子路径、查询条件和锚点跳转。独立部署的 Gateway Studio 保留原有授权能力。此调整只涉及 Web Studio，不更改后端接口权限。

## 连接、Account、User 独立选择

工作台顶部左侧依次为三个独立下拉框：连接配置、Account、User ID。管理态只保留连接和管理 Account，并展示经过验证的 Root API Key／Account Admin API Key 类型；不展示工作台 User 选择器。Account Admin 的管理 Account 以静态文本展示，不提供只有一个选项的下拉框；只有 Root 能查询和切换服务端 Account。管理态连接列表与分组摘要仅展示 Account，不展示数据 User ID；回到工作台后恢复 Account/User 标识。顶部身份范围按页面用途展示：连接设置、连接编辑、新增凭据以及用户管理、Bot 管理、Account 功能配置和数据工作台均保留身份切换；工作台展示 Account 与 User，管理态只展示 Account；只有服务端全局参数页仅保留服务连接入口，以独立图标区分 Account 与 User，不添加 Account: 文字前缀。切换模式保留数据身份，进入管理态不会将 User Key 当作管理凭据。连接入口按服务类型和地址分组，同一服务下的多份凭据只显示一项，条目仅展示连接名称和地址；Account 与 User 信息分别放在后两个选择器。分组不合并、删除或提升任何已保存凭据，重复选择当前服务不会切换身份；API Key 模式下工作台的 Account 和 User 列表均由本机已配置的 User API Key 整理，不请求服务端 Account/User 目录；管理态 Root 的 Account 选择器可读取服务端 Account，工作台的 User 选择器只展示本机保存的 Key。工作台跨 Account 切换时同步恢复该 Account 已保存的 User 凭据，避免携带旧 Account 的 Key；管理态切换 Account 则清空旧 User，等待显式选择。切换 Account/User 保留当前连接配置 ID、名称与管理凭据。缓存及下拉状态按身份作用域隔离。

## 右侧 Studio 设置

Studio 使用服务连接及 API Key 授权。右侧入口展示当前作用身份：工作台显示当前 User ID 和首字母头像；Root 管理范围显示 Root，Account 管理范围显示 Account Admin。展开后仍为默认入口、语言、主题与 GitHub 等 Studio 偏好。连接设置放在左侧导航，两个模式均可访问；右侧不复制切换连接、切换 User 或添加 API Key 的操作。连接配置、Account、User 的切换和添加连接统一在左侧选择器中完成，避免把 User ID 当成登录账号。顶部 API Key 入口只用于查看和复制当前模式的凭据，不承担连接配置或身份选择。工作台展示 User ID 与当前 User Key，管理态展示管理凭据类型；Key 默认脱敏，支持显隐和复制，关闭弹层或切换身份后重置显隐状态。工作台的模式入口使用宫格图标和“管理后台”文案。

API Key 模式的 User 选择器不调用 `fetchAdminUsers`，即使当前连接同时持有 Root Key。显式切换身份时将之前的 User Key 保存在当前连接的本地历史中，重载后可恢复，且不增加连接配置条目。历史按服务地址、提供方、Account 和 User 隔离；替换或清除当前配置的 Key 时清除对应旧 Key 历史。服务端全量用户管理保留在管理页面。

## 首次引导与连接配置

首次接入只使用 `/onboarding`，已有配置时访问引导路由会进入连接设置。旧 `/config` 仅做兼容跳转：新用户进入 `/onboarding`，已有配置进入 `/settings`。

日常列表保留 `/settings` 和 `/admin/settings`；新增配置使用 `/connections/new` 或 `/admin/connections/new`，编辑配置使用 `/connections/$profileId` 或 `/admin/connections/$profileId`。所有表单在 Studio 外壳内独立展示，支持直达、刷新、取消返回和连接不可用时修复配置。新增或修改凭据时复用首次接入的验证器，服务端返回的 Account/User 与 Key 类型为权威结果；新增成功后保存并启用，编辑名称不触发网络校验。管理态和工作台的配置入口始终保留各自模式。


## 普通用户与管理功能分离

- `/vikingbot` 仅保留当前数据身份的 Web 对话、新建和删除会话，不调用 Bot 管理能力、渠道或平台历史接口。
- `/admin/vikingbot` 仅 Root 可进入，集中管理飞书等渠道、应用凭据、运行用户、启停、删除、验证，以及平台会话历史。Account Admin 和普通 User 深链访问在组件挂载前被拒绝。
- `/admin/configuration` 是统一的 OpenViking 配置页，Account 级经验提取开关作为其中一个配置项，支持 Root 和 Account Admin。Root 可通过左侧独立的“服务端配置”导航进入 `/admin/configuration/server` 管理模型、存储及服务运行参数。`/admin/agent-evolution` 只兼容跳转到统一配置页，不再有独立经验设置导航。右上角 Studio 设置仅保留默认入口、语言和主题等本地偏好；工作台不挂载经验提取配置。
- OAuth 公共入口只使用当前或手动提供的用户凭据；服务器身份目录只在 `/admin/oauth/verify` 和 `/admin/oauth/consent` 中启用。
- API Key 模式的 Bot 健康检查、聊天、打断和引导请求不会回退到 Root Key；trusted 协议要求每次认证请求携带 Root，工作台使用该凭据与 Account/User 断言头；管理接口不附加数据身份断言，避免与目标用户或 Account 冲突。API Key 模式的工作台仍禁止 Root 兜底。
- 已迁移的用户、用户组、ACL、抽取规则、服务配置和网关管理继续保留在 `/admin`。资源导入导出、快照、检索、会话、日志、任务及监控接口支持普通用户，按其数据权限使用，继续保留在工作台。
- 本次只变更 Studio，未更改服务端权限规则。

### Local User credentials and storage

- The User selector's “Configure User API Key” opens `/connections/user-key`. The current service and Account are read-only context; User ID is resolved from the supplied key. Root keys and keys belonging to a different Account are rejected before tenant data validation. Saving retains previous User credentials in the active connection without creating another service profile or replacing its management key.
- Connection and profile payloads are encrypted with AES-GCM (256-bit key, fresh 96-bit IV per write, storage-slot name as authenticated additional data). A non-exportable Web Crypto key is persisted in IndexedDB. Startup restores credentials automatically; no password, unlock screen, or repeated key entry is required.
- Existing plaintext connection/profile records are migrated before the application mounts. The obsolete SDK session-storage key is removed after successful migration. New credentials are not written to plaintext Web Storage. Save errors are surfaced; onboarding and credential forms wait for encrypted persistence before completing.
- This is encryption at rest against casual plaintext storage disclosure, not isolation from same-origin code, XSS, browser-profile compromise, or extensions with sufficient site/debugger access. A non-exportable key can still be used for decryption by authorized origin code. Stronger isolation would require a backend credential broker or native keystore integration and is outside this frontend-only change.
- Web Crypto requires HTTPS or a trustworthy local origin such as localhost/127.0.0.1. When Web Crypto or IndexedDB is unavailable, connections live only in page memory with a persistent notice; refreshing requires reconfiguration. Existing records remain untouched and no plaintext persistence is used. Decryption failures still stop startup and preserve existing records. Clearing site data removes both saved connections and their encryption key.


### Workbench request credential isolation

- 工作台冷启动、健康检查、角色识别及业务请求只使用当前 User Key。缺少 User Key 时允许匿名读取健康元数据，不将管理 Key 作为数据凭据兜底；页面提示配置或选择已保存的 User。
- 全局 SDK 按路由同步切换数据／管理作用域，发生在子页面渲染之前。数据作用域覆盖遗留认证头为当前 User 凭据，并在传输前拒绝 `/api/v1/admin` 请求；切换页面后排队中的请求不能借用管理凭据。
- Root／Account Admin 权限探测仅在管理态执行，离开时取消未完成的探测。`api_key` 模式以 `/health` 返回的实际角色为准；仅 trusted 管理模式需要额外探测控制面。
- 工作台的“返回管理模式”入口可以根据本机是否配置管理凭据显示，展示入口不授予管理权限。进入管理态后才校验管理 Key，管理页面仍依据校验结果和服务端授权开放。
- 工作台不触发 Account ACL 配置读取、管理目录枚举或管理能力诊断。Account/User 列表来自本机保存的 User Key。
- trusted 认证在服务端要求 Root Key；服务端确认模式后，工作台按 trusted 协议发送受信凭据及 Account/User 断言。API Key 模式禁止回退到 Root。

## Account Admin 验收

Admin User Key 可单独配置，无需 Root Key；服务端确认 `admin` 后可进入本 Account 管理后台。识别为管理凭据时同步采用服务端 Account/User，避免从旧连接继承错误身份。后台允许管理成员、USER/ADMIN 角色、用户组、抽取规则和 Account 配置；服务端配置、Account 生命周期与 Bot 连接管理仍仅 Root 可用。

自动化回归覆盖从工作台与后台直达入口启动、没有 Root 凭据、创建用户及角色更新请求，以及工作台不发送 Root 管理请求。Chrome 使用已有 Admin User Key 单独配置验证了成员列表、角色编辑入口、新增用户表单及模式切换；未对真实成员执行增删、角色修改或 Key 重置。

## Trusted 认证兼容

首次接入自动识别 trusted；配置服务的 Root Key 后可通过直接路由或顶部按钮进入工作台与管理后台。工作台通过 Root 凭据和 Account/User 头声明数据身份；管理接口使用 Root 凭据，不附带数据身份断言。当前 User 的数据角色不限制该受信凭据本身的服务管理权限。默认入口偏好在 trusted 模式下也可选择工作台，不要求另行配置 User Key。

Trusted 工作台的 Account 选择器读取服务端 Account 目录，User 选择器读取不含凭据的用户目录；可选择已有用户，或在 User 搜索框输入其他合法 User ID 后直接切换。身份切换保留受信凭据及连接配置，清理旧身份查询缓存，不为每个 User 新建连接。普通 API Key 模式仍只展示本机已配置 Key 的身份列表。

验证包括隔离 HTTP 集成测试，以及 Chrome 中使用后端真实 TrustedAuthPlugin 的本地验收环境；业务数据接口使用验收数据，没有修改现有 dev 服务的认证配置。

## 前端工程结构

工作台、管理后台及公共连接流程已按功能迁至 `src/features/`；外壳、导航和路由边界位于 `src/app/`。`src/routes/` 只保留路由声明，公开 URL 不变。目录职责、共享代码规则与依赖检查见[前端工程结构](./frontend-structure.md)。

## 接入与复访流程

首次接入、三类 Key 组合、团队成员创建、两种区域的身份恢复及受限浏览器存储规则见[用户使用流程](./user-journeys.md)。
