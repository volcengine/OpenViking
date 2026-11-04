# Studio 生命周期与认证模块拆分

日期：2026-11-02。分支：`feat/all_ov_studio`。承接入口分层及连接操作 CR，继续落实目录职责和公开接口。

## 当前结构

```text
platform/
  connection/service-provider.ts   # 服务识别
  access/features.ts               # 功能目录及支持策略
app/
  composition/auth-entry.tsx       # 认证入口装配
  navigation/feature-path.ts       # 页面与功能匹配
features/
  auth/
    components/                    # API Key、邮箱、密码恢复、邀请及布局
    lib/auth-routes.ts             # 认证页面类型、表单校验
  connection/
    access.ts                      # 业务读取/操作接口
    login.ts                       # 认证建立连接的接口
    hooks/use-app-connection.tsx    # React 接入
    lib/
      connection-controller.ts     # 生命周期、状态及取消机制
      connection-contract.ts       # ConnectionSnapshot 和内部上下文契约
      connection-operations.ts     # 登录/配置业务操作
      connection-verification.ts   # 连接验证
      connection-identity.ts       # 已验证主体与访问上下文
      profile-bootstrap.ts         # 启动恢复与活动配置保存
      capability-response.ts       # 能力响应识别
      connection-runtime.ts        # 客户端同步和凭据选择
```

## 职责与行为

`ConnectionController` 持有连接、配置、服务模式、数据/管理验证结果及请求代次，统一完成启动、重连、配置激活、身份切换、区域恢复和缓存清理。状态通过同一份 `ConnectionSnapshot` 发布。角色从当前有效验证结果派生；开发模式沿用既有 Root 能力，不再另存一份可能与验证结果矛盾的角色状态。

Provider 只接入导航与缓存清理、订阅快照、启动/停止生命周期。跨区域时仍在子页面请求前同步限制请求凭据，布局提交恢复数据/管理作用域后再显示子页面。停止生命周期会取消请求并使旧操作失效；恢复后旧响应不能覆盖新状态。

认证界面归 auth；auth 通过公开 `login.ts` 建立连接，connection 不反向依赖认证 UI。验证、配置持久化不再由含糊的 onboarding 模块拥有。`onboarding.ts` 保留旧向导兼容出口；真实认证路由继续直接渲染独立认证页。

29 个不需要完整连接配置的业务模块文件改为 `useConnectionAccess()` 或 `useConnectionCommands()`。读取接口实际只返回列举的字段，不包含 Key、原始连接和可变 Profile。需要完整凭据的既有协议、配置调用方保留原接口，并在架构测试中列举，后续按请求接口逐个迁移。接口缩小不代表已经实现选择性订阅或减少所有重渲染。

服务识别与功能支持规则移出 shared；页面匹配归 app，网络能力探测及响应解释归 connection。旧 `shared/lib/studio-service` 保留兼容类型/策略出口，生产代码不再从中读取产品规则。

架构检查增加：auth → connection 仅使用登录接口；禁止 connection → auth；连接控制模块不能依赖 React/Router，也不允许其他 feature 导入；新增业务消费者不能使用完整内部上下文；生产模块不能从旧 shared 兼容出口读取策略。

## 验证

- 完整 `npm test`：222 个文件、1937 项测试全部通过（`/private/tmp/ov-next-full-tests.log`）。保留既有 ROOT/Admin/User、trusted、请求隔离、配置恢复及认证路由回归；新增控制模块停止/重启及过时结果检查。
- `npm run build` 通过，包含全量 lint、i18n 和 Vite 构建（`/private/tmp/ov-next-build.log`）。现有其他业务文件仍有 8 条 lint warning，以及 bundle 大小提示。
- 构建后仅做了类型契约命名和测试输入类型收窄；涉及文件再次 lint 通过（`/private/tmp/ov-next-final-scoped-lint.log`），完整测试通过。`npx tsc --noEmit` 与改动前逐条比较，忽略行列移动后均为相同的 43 条既有诊断，无新增（`/private/tmp/ov-next-final-tsc.log`）。
- 浏览器检查本地 3017：默认 API Key 登录、跳转邮箱登录、跳转忘记密码及直接刷新正常；认证布局独立，未提交真实凭据，检查时无运行时错误。
- `git diff --check` 通过。本轮未提交、推送或部署。

## 继续沿用的边界

- 私有化与官方火山的入口、地址、服务候选规则保持一致；火山 ROOT 跳转及账号接口继续暂缓。
- 加密存储格式、trusted/dev、数据/管理 Key 隔离、旧链接和旧配置恢复继续复用。
- 未重组文件系统全部内部模型、改写所有直接协议调用或引入另一套通用状态管理框架。
