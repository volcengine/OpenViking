# Studio 连接与路由重构记录

日期：2026-11-02。分支：`feat/all_ov_studio`。范围为入口分层 CR 的运行时补齐，沿用既有双版本规则及私有化 ROOT/Admin/User 权限。

## 变更

| 批次          | 落实内容                                                                                                                                | 主要所有者                                                                   |
| ------------- | --------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| 1. 规则执行   | 编辑器使用配置的服务及固定火山地址；官方版禁止 Gateway 自动发现；恢复连接、保存、激活及客户端同步都执行部署准入                         | `platform/deployment`、`connection-policy`、`connection-runtime`             |
| 2. 业务与状态 | 登录并激活、配置提交/激活/删除统一接口；重复登录按完整连接指纹复用；配置、服务端验证主体、当前数据/管理上下文分开；旧验证不能跨连接复用 | `connection-operations`、`connection-identity`、Provider                     |
| 3. 路由装配   | 公共认证页面直接由文件路由渲染；连接布局单独挂载 Provider；AppShell 只保留外壳和主题；默认落点只接收可进入区域                          | `routes`、`studio-route-layout`、`connected-studio-layout`、`platform/entry` |

## 稳定调用边界

```text
认证文件路由 → AuthEntry → 登录表单 → loginAndActivate
                                      ├─ 服务校验及临时 VerifiedConnection
                                      ├─ 按连接指纹复用 / 保存配置
                                      └─ 可进入区域 → 平台入口策略
已连接布局 → AppConnectionProvider → 页面读取状态 / 提交操作
                                      ├─ submitProfile / activateProfile
                                      ├─ 身份探测及 accessContext
                                      └─ 连接同步、请求取消、缓存隔离
```

表单不再组合验证、保存和激活调用。Profile 改名不触发额外凭据验证；改凭据需要验证。选中目录身份后输入 Key 必须匹配该身份；取消编辑、切换连接或过时结果不能提交到新连接。错误沿用 Key 脱敏和本地化文案。加密存储格式及既有作用域记忆保持兼容。

`VerifiedConnection` 是当前验证结果，数据与管理主体各自为 `unchecked` 或 `verified`；复制持久化 JSON 不能成为有效的当前验证快照。Provider 的验证状态仅在对应连接/模式匹配且非离线、非切换阶段时可见，重连或失败复验清除旧结果。它们不是服务端权限的替代品。

`ConnectionAccessContext` 分别表达个人数据身份、管理空间、验证主体及可进入区域。纯入口策略只接受 `workbench`、`management`、`both` 三类结果，不再接收 Key，也不允许调用方单独拼接角色和管理布尔值。`studio-mode` 中历史签名作为兼容包装保留。

## 验证

- `npm test`：221 个文件、1936 项测试全部通过（`/private/tmp/ov-cr-full-test.log`）。包含本地 HTTP fixture 下的 ROOT/Admin/User、trusted/dev、身份切换、控制/数据凭据隔离、失败重连清除验证结果、Gateway 禁用、重复登录复用、编辑器固定地址与真实路由检查。
- `npm run build`：通过，包含 ESLint、国际化检查和 Vite 构建（`/private/tmp/ov-cr-final-build.log`）。保留现有未修改业务文件的 lint warning 及 bundle 大小提示。
- `npx tsc --noEmit`：未全绿；与本轮改动前 `/private/tmp/ov-cr-before-tsc.log` 对比，仍为相同的 43 条既有诊断，无新增。最终输出为 `/private/tmp/ov-cr-final-tsc.log`。只消除行列位移后逐条比较，不仅比较数量。
- 浏览器：本地 3017 的 API Key → 邮箱 → 忘记密码点击跳转及刷新正常；重置密码/邀请直接入口正确提示缺少凭证；认证页均独立于工作台外壳。未提交真实 Key、密码或调用真实账号服务。
- `git diff --check` 通过。没有执行提交、推送或部署。

完整测试之后仅调整新增测试的 HTMLInputElement 泛型以满足 TypeScript；该文件再次 lint 通过，TypeScript 基线复核一致。

## 保留边界

- 火山 ROOT 身份识别、后台跳转及管理接口仍暂缓；邮箱/密码恢复/邀请仍是页面就绪、账号服务待联调。
- 此轮之后，健康检查、身份切换及配置激活已迁到独立连接控制模块；认证界面也已拆分，见[后续重构记录](studio-lifecycle-auth-refactor.md)。凭据存储继续复用。
- 验证证据区分本地测试、构建、浏览器、真实服务和部署；本轮不声明生产服务验收。
