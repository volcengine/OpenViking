# OpenViking Studio 权限核对

核对日期：2026-10-30。依据当前工作区开源服务端源码，主要范围是 `auth_mode=api_key`。本文记录接口实际权限，不决定页面、导航或功能归属；火山托管服务的权限需要另行核对，不能直接套用本表。

本次采用静态鉴权检查；未执行真实用户删除、角色变更或 Key 重置。接口允许不代表部署一定启用了对应能力，运行时配置、存储模式和资源 ACL 仍可能影响结果。

## 身份与凭据

| 名称 | 服务端含义 | 作用范围 |
| --- | --- | --- |
| Root API Key | 服务级 Root 凭据，不是某个 Account 下的 Admin User Key | 跨 Account 的管理和允许的运维接口 |
| Account Admin API Key | 绑定 Account、User，用户角色为 `admin` 的 API Key | 本 Account 的管理能力，以及该 User 的数据能力 |
| User API Key | 绑定 Account、User，用户角色为 `user` 的 API Key | 本 User 的数据与获授权资源 |

Account Admin 使用的也是 User API Key；它的区别是服务端角色为 `admin`。前端的 `apiKey`、`adminApiKey` 表示数据请求与管理请求使用哪份凭据，不是后端两种独立 Key 格式。

API Key 模式以服务端从 Key 解析出的身份为准。选择 User ID、Account 或修改请求头不能提升权限，也不能把某个 Account 的 Admin Key 变成另一个 Account 的管理员。Root 的管理 Account 上下文与数据 User 身份是两个概念。

权限不是简单的 Root 包含 Admin、Admin 包含 User：Root Key 在 API Key 模式下不能直接调用大部分租户数据接口，例如文件、检索、会话及资源 ACL，需要相应 User/Admin Key。

依据：[API Key 身份与路径检查](../../openviking/server/auth/plugins/api_key.py)、[精确角色鉴权](../../openviking/server/auth/__init__.py)、[角色定义](../../openviking/server/identity.py)。

## 管理能力矩阵

表中的“本 Account”是 Key 绑定的 Account，不是前端任意选中的 Account。Root 跨 Account 能力仍需目标接口接受相应 Account 参数。

| 能力 | Root API Key | Account Admin API Key | 普通 User API Key |
| --- | --- | --- | --- |
| Account 列表、详情、创建、删除 | 可以 | 不可以 | 不可以 |
| 用户列表 | 任意 Account | 本 Account | 本 Account，敏感字段受限 |
| 单个用户的管理详情 | 任意 Account | 本 Account | 不可以 |
| 创建、删除用户 | 任意 Account | 本 Account | 不可以 |
| 将用户设为 `user` 或 `admin` | 任意 Account | 本 Account，包含升降级 | 不可以 |
| 将用户设为 `root` | 不支持通过用户角色接口授予 | 不支持 | 不支持 |
| 用户配置：记忆策略、TTL 等受支持字段 | 任意 Account | 本 Account | 不可调用此管理接口 |
| 用户组创建、列表、删除、成员维护 | 任意 Account | 本 Account | 不可以 |
| Account 抽取规则／记忆模板读取、修改、重置 | 任意 Account | 本 Account | 不可以 |
| Account 功能配置：ACL、经验提取等受支持字段 | 任意 Account | 本 Account | 不可以 |
| Account 配置中的 `vlm`、`query_planner`、`embedding`、`vectordb` | 可以 | 不可读写这些字段 | 不可以 |
| 服务级配置读取、修改、模型连接测试、重启 | 可以 | 不可以 | 不可以 |
| VikingBot 渠道、飞书应用关联、连接与安装管理 | 可以，需运行能力可用 | 不可以 | 不可以 |

Account Admin **不只是能看用户列表**，而是有一组本 Account 管理能力。普通 User 也能调用用户列表接口，因此不能用“列表请求成功”判断是否是管理员。

依据：[Account 隔离及管理接口](../../openviking/server/routers/admin.py)、[Bot 连接管理鉴权](../../openviking/server/routers/bot_studio.py)。

## API Key 的读取与重置

| 操作 | Root | Account Admin | User |
| --- | --- | --- | --- |
| 列表中读取自己完整 Key | 按目标用户及服务端存储策略 | 可以，受凭据暴露策略限制 | 个人 API Key 认证时可以，受凭据暴露策略限制 |
| 列表中读取其他用户完整 Key | 接口允许，取决于服务端是否保留及开放明文 | 不可以，仅返回脱敏前缀 | 不可以，其他用户凭据隐藏 |
| 重置自己的 Key | 可以重置目标用户的 Key | 可以 | 仅个人 API Key 认证时可以 |
| 重置其他用户的 Key | 任意 Account | 本 Account | 不可以 |

重置不是读取：重置会立即使旧 Key 失效，不能用它替代“复制现有 Key”。如果服务端仅保留哈希，Root 也无法还原原始 Key。`include_credentials=false` 的用户目录只返回允许的可用性标记和身份元数据。

依据：[用户列表及凭据字段控制](../../openviking/server/routers/admin.py#L1002)、[Key 重置](../../openviking/server/routers/admin.py#L1198)。

## 数据权限、ACL 与可观测性

| 能力 | Root API Key | Account Admin API Key | 普通 User API Key |
| --- | --- | --- | --- |
| 文件、检索、会话等租户数据接口 | 大部分数据路径直接拒绝；需 User/Admin Key | 使用自己的数据身份，受路径和资源权限约束 | 使用自己的数据身份，受路径和资源权限约束 |
| 共享资源 `viking://resources` | 不能直接使用 Root Key 调用资源数据接口 | 对本 Account 资源具有隐式 MANAGE | 按资源 ACL；具有 MANAGE 的 User 也可管理资源 ACL |
| 其他 User 私有 `viking://user/<id>` 命名空间 | 不能直接凭 Root Key 浏览 | 不能仅凭 Admin 角色直接访问 | 不能直接访问 |
| 控制台用量、请求审计 | 相关接口不加 User 过滤；具体 Account 范围按接口上下文 | 本 Account，不限当前 User | 当前 User |
| 后台任务查询／取消 | 支持相应运维查询；禁止取消任务 | 自己 Account + User 的任务 | 自己 Account + User 的任务 |

“管理用户”和“以该用户身份读取数据”是不同权限。Admin 可以维护成员、策略和重置 Key，不代表其当前数据身份能直接读取所有成员的私有记忆。

资源 ACL 只适用于 `viking://resources`。是否启用 ACL、资源继承和当前授权共同影响普通用户的读写与管理权限，不能把所有 ACL 操作都归结为角色是否是 Admin。

依据：[资源隐式 MANAGE](../../openviking/storage/acl.py#L284)、[私有命名空间限制](../../openviking/core/namespace.py#L313)、[用量及审计 User 过滤](../../openviking/observability/usage_audit/api_service.py#L41)、[后台任务鉴权](../../openviking/server/routers/tasks.py)。

## 网关是独立的鉴权维度

网关通过 OpenViking 身份限制 Account、User 范围，同时可以配置独立 Management Key。不能只凭 Root/Admin/User 三个角色决定网关所有按钮的权限。

- 配置了 Management Key 时，网关对象写入还需要通过该管理凭据检查；仅有 Root 或 Account Admin Key 不足以通过此检查。
- 未配置 Management Key 时，对象写入检查的规则不同，不能断言普通 User 在所有部署中都不能修改网关对象。
- 网关日志、Key 列表对 User 按自身过滤，Admin 在本 Account 范围查看。
- 给其他用户签发网关 Key 时，Admin 仍需要实际有效的目标用户凭据；拥有管理角色不能自动读出其他用户完整 Key。
- 管理凭据不替代 OpenViking 操作自身的角色与 Account 检查。应分别看 `/gateway/admin/access` 返回的能力及具体接口检查。

依据：[网关 caller 与能力接口](../../openviking_gateway/management.py#L166)、[网关 Key 签发](../../openviking_gateway/management.py#L420)。

## 前端核对结果

1. 管理能力判定已包含 `root` 与 `admin`；不是只有 Root 角色才能进入管理范围。Account Admin 的范围固定为自身 Account。
2. 之前成员角色编辑错误地按 Root 权限显示；已局部修正为 Root/Admin 可编辑，且只能选择 `user`、`admin`。
3. 之前控制凭据目录验证误用了数据凭据检查路径；已局部修正为显式使用控制凭据。
4. 工作台可见的管理入口不等于已获管理授权；进入后必须校验实际管理 Key 的服务端角色。工作台数据请求不能自动回退到 Root Key。
5. Account Admin 的其他用户 Key 不能复制为完整凭据，是后端明确限制；Root 场景还需检查当前实际使用的管理 Key、服务端明文可用性及响应字段。

依据：[前端管理能力](../src/features/connection/lib/studio-permissions.ts)、[成员角色编辑](../src/features/admin/users/components/user-role-cell.tsx)、[控制凭据目录验证](../src/features/connection/components/setup-identity-directory.tsx)。

本表先固定权限边界。是否共用管理入口、如何分组、工作台是否增加用户入口，后续再按这些能力和实际用户任务规划。

## Trusted 模式补充

以上角色矩阵主要针对 API Key 模式。Trusted 模式要求每个认证请求使用服务配置的 Root Key；数据接口结合 Account/User 断言解析数据身份，管理接口将有效受信凭据识别为 Root。因此持有该凭据的 Studio 可以进入工作台和管理后台，切换数据 User 不会撤销 Root 管理能力，也无需逐个配置 User Key。工作台的服务端 Account/User 目录是此模式的显式能力，不适用于普通 API Key 模式。
