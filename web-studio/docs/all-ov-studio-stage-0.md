# Studio 合并：阶段 0 与私有化 API Key 登录

阶段 0 记录日期：2026-11-02；部署配置更新：2026-11-03。分支：`feat/all_ov_studio`；基于上游 `2f6871a7254e560300c42119b6348249d16fe4e2`。后续登录组合与验证另见[UX 记录](all-ov-studio-ux-migration.md#登录组合与-stg-预览2026-11-03)。

## 已实现

- 显式区分 Studio 部署版本、连接服务与服务端角色。开源构建默认私有化，域名不决定版本。
- 首次登录改为服务类型、服务地址、一把 API Key；识别并验证身份后再保存连接。沿用加密存储、错误脱敏、请求取消、双语和既有 dev/trusted 协议兼容。
- ROOT 单 Key 登录将凭据归入管理作用域，不保留虚构的个人 User；直接进入后台。个人目录深链在业务组件挂载前转到后台。
- Admin 单 Key 同时用于其个人数据与本空间管理；普通 User 不显示管理切换或补充管理凭据的快捷入口。
- 官方版固定托管地址，不能恢复或激活其他自部署连接；导航、能力策略与路由均关闭管理后台，包括管理设置深链。旧配置保留在存储中，不删除。
- 旧连接配置、受限浏览器存储策略、离线修复入口、standalone gateway 与显式的管理/数据身份隔离继续保留。

## 登录后的权限

| 身份 | 首次落点 | 个人工作台 | 数据空间管理 | 服务及跨空间管理 |
| --- | --- | --- | --- | --- |
| ROOT | `/admin` → `/admin/users` | 当前 ROOT 身份不提供；需显式配置独立 User Key | 可以 | 可以 |
| Admin User | `/home` | 自身 User | 仅自身 Account | 不可以 |
| 普通 User | `/home` | 自身 User 与授权资源 | 不可以 | 不可以 |

管理与数据请求继续使用各自凭据。旧配置若同时保存 User Key 和 ROOT Key，显式进入管理态会重新认证那把管理 Key；这代表切换到 ROOT 身份，不能由普通 User 角色推导管理权限。服务端仍是最终授权方。

普通 User 的后端成员目录读取接口允许读取本空间成员，并限制其他人的凭据字段。这项读取能力不代表获得成员管理权限；管理写操作及跨空间访问仍被拒绝。

## 部署配置

构建时配置：

```bash
# 不设置即为私有化版
VITE_STUDIO_EDITION=self-hosted npm run build

# 官方生产必须显式选择版本；托管地址可省略，使用生产默认值
VITE_STUDIO_EDITION=volcengine \
VITE_STUDIO_HOSTED_API_URL=https://api.vikingdb.cn-beijing.volces.com/openviking \
npm run build

# STG：Vite base 同时用于资源、路由与启动环境识别
npm run build -- --base /workbench-stg/
npm run preview -- --base /workbench-stg/ --port 4173
```

在预览服务打开 `/workbench-stg/login`：新标签页默认官方入口选择；通过 STG 浮动控件切到私有化，应整页重载回 `/workbench-stg/login`，显示托管优先的 API Key 表单。两种预览的托管地址均为 `https://api.vikingdb-stg.cn-beijing.volces.com/openviking`；自部署地址仍可自行输入。选择仅存在当前标签页 `sessionStorage`，不修改生产版本或发布配置。生产环境忽略此选择，也不显示控件。

环境可通过 `VITE_STUDIO_ENVIRONMENT=stg` 或运行时 `environment: 'stg'` 显式指定；否则仅 `/workbench-stg/` base 识别为 STG，其余默认生产。生产源码默认 `self-hosted`，官方生产必须显式设置 `VITE_STUDIO_EDITION=volcengine` 或等价运行时配置，不能依赖 STG 默认值。

也可以在应用入口模块加载前注入非敏感运行时配置，同名字段优先于构建值：

```html
<script>
  window.__OV_STUDIO_CONFIG__ = {
    edition: 'volcengine',
    hostedApiBaseUrl: 'https://api.vikingdb.cn-beijing.volces.com/openviking'
  }
</script>
```

运行时配置不是权限凭证，不接受 API Key。版本值或托管 URL 非法时显示部署配置错误，不继续初始化应用。托管地址必须为 HTTPS，且不能含凭据、查询参数或 hash。私有化服务地址仍兼容本地 HTTP。

## 阶段 0 验证记录（2026-11-02）

- 最终完整 Vitest 回归通过：218 个测试文件、1910 条测试。ESLint 与 i18n 构建前检查通过；已有 8 条无关的 shadow 警告仍保留。私有化与官方版生产构建通过，现有大 chunk 警告仍保留。
- 前端回归覆盖首次登录角色分类、落点、ROOT 深链、普通 User 管理拒绝、Admin 双入口、官方版连接与管理边界、旧配置与 trusted/dev 兼容。
- 在同一上游提交的干净副本运行 TypeScript 检查，现有错误与改动后的结果完全一致；全仓 `tsc --noEmit` 仍因这些基线问题失败，不能视作类型检查通过。
- 隔离后端使用本仓库真实 `ApiKeyAuthPlugin`、API Key manager、`/health` 和 Admin router；存储为内存 fixture，文件列表为空。15 项检查通过：三种 Key 身份、ROOT 管理/数据边界、Admin 本空间/跨空间边界、普通 User 管理写操作拒绝、无效 Key 与伪造身份头等。
- 临时组件验收让实际 `ApiKeyLogin`、`verifyStudioSetup`、`AppConnectionProvider`、`useStudioMode` 请求上述服务；ROOT/Admin/User 三条链路全部通过。仅替换浏览器持久化和路由驱动，未替换认证请求。该临时测试未纳入默认测试集。
- Chrome 已查看桌面和 390px 手机登录页；表单和按钮可见，无横向溢出。随后浏览器连接连续超时，平板及三种角色的完整浏览器登录、刷新、深链走查未完成。自动化测试不能替代这项证据。
- Impeccable 静态扫描未发现确定性问题；这不等价于完整无障碍审计。

以上属于本地代码、隔离认证与组件验证，不代表火山真实服务联调、CI 或部署验收。

## 后续范围

企业邮箱账号服务、邀请与密码恢复、托管 Session、统一账号菜单、原型完整外壳及其他业务模块仍按[整体实施方案](all-ov-studio-implementation-plan.md)继续。后续已补齐官方入口选择及公共认证页面；目前仅 API Key 链路能完成认证，邮箱相关页面显示服务暂不可用，不宣称账号服务已接通。
