# Account 级 User / Agent 命名空间策略与共享 Session 设计方案

## Context

当前 OpenViking 已具备 `account_id / user_id / agent_id` 三元身份模型，但 `user` 与 `agent` 的关系语义仍然不够明确，`session`、目录和检索对这层关系的表达也不一致，主要问题有：

- 系统内缺少 account 级显式策略来定义 `user scope` 是否再按 `agent` 切分、`agent scope` 是否再按 `user` 切分
- 命名空间决策分散在多处，目录拓扑无法直接体现 `user` / `agent` 关系
- `session` 仍然偏向单 user 视角，不能准确表达一个 account 内多 user / 多 agent 协同场景中的真实参与者身份
- 检索、目录遍历、向量索引、消息身份模型之间缺少一套统一的 owner / participant 语义

本次方案的目标，是把 `user` / `agent` 关系显式提升为 account 级命名空间策略，并为后续混合参与者 session 的提取、审计和协同能力打基础。

---

## 决策摘要

- account 级新增两项配置：
  - `isolate_user_scope_by_agent: bool`
  - `isolate_agent_scope_by_user: bool`
- 默认值：
  - `isolate_user_scope_by_agent = false`
  - `isolate_agent_scope_by_user = false`
- `user` 与 `agent` 目录都采用显式嵌套 canonical URI，不再依赖 hash space。
- `session` 升级为 account 级共享作用域，统一落在：
  - `viking://session/{session_id}`
- `session add-message` 新增 `role_id`：
  - `role=user` 时绑定真实 `user_id`
  - `role=assistant` 时绑定真实 `agent_id`
- 检索与目录可见性统一基于以下信息：
  - `account_id`
  - `uri`
  - `owner_user_id`
  - `owner_agent_id`
- 本方案按不兼容改造设计：
  - 不做旧命名空间兼容
  - 不做历史数据迁移
  - 不做 mixed session `commit / extract` 分流

---

## 一、目标与非目标

### 1.1 目标

- 引入 account 级配置，显式定义 `user` 和 `agent` 的共享边界
- 统一 `user / agent / session` 的逻辑 URI 与底层目录拓扑
- 将 `session` 升级为 account 级共享会话
- 在 `session add-message` 中显式记录消息归属身份
- 统一文件系统可见性与检索过滤规则，避免“能搜到但不能读”

### 1.2 非目标

- 本次不实现历史数据迁移
- 本次不实现双读双写兼容
- 本次不定义混合参与者 session 的 `commit / extract` 分流算法
- 本次不引入 participant 级 ACL，只提供 account 级共享 session

---

## 二、核心设计决策

### 2.1 account 级配置字段

推荐字段名：

- `isolate_user_scope_by_agent: bool`
- `isolate_agent_scope_by_user: bool`

语义如下：

- `isolate_user_scope_by_agent = false`
  - `user` 作用域只按 `user_id` 分区
- `isolate_user_scope_by_agent = true`
  - `user` 作用域按 `(user_id, agent_id)` 分区
- `isolate_agent_scope_by_user = false`
  - `agent` 作用域只按 `agent_id` 分区
- `isolate_agent_scope_by_user = true`
  - `agent` 作用域按 `(agent_id, user_id)` 分区

默认值：

- `isolate_user_scope_by_agent = false`
- `isolate_agent_scope_by_user = false`

这个默认值的含义是：

- `user` 记忆按 user 隔离，不区分 agent
- `agent` 记忆按 agent 隔离，不区分 user

采用这个默认值的原因是：

- 大多数产品里，用户画像、偏好、个人事实更自然地跟 `user` 绑定，应该能跨 agent 复用
- 很多 agent 在系统内更像“共享能力单元”而不是“每个 user 一份的私有副本”，默认跨 user 共享更符合直觉
- 把两者都默认设为“不额外切分”，可以让目录语义最直接，减少初期理解和使用成本

### 2.2 配置方式与生命周期

这两个字段在 account 创建时配置，并跟随 account 生命周期持久化。

持久化位置：

- `/local/{account_id}/_system/setting.json`

示例：

```json
{
  "namespace": {
    "isolate_user_scope_by_agent": false,
    "isolate_agent_scope_by_user": false
  }
}
```

配置入口：

- `POST /api/v1/admin/accounts`

请求示例：

```json
{
  "account_id": "acme",
  "admin_user_id": "alice",
  "isolate_user_scope_by_agent": false,
  "isolate_agent_scope_by_user": false
}
```

如果请求里省略这两个字段，则服务端按默认值补齐：

```json
{
  "isolate_user_scope_by_agent": false,
  "isolate_agent_scope_by_user": false
}
```

读取入口：

- `GET /api/v1/admin/accounts`
- `GET /api/v1/admin/accounts/{account_id}`（如果后续补充单 account 查询接口）

持久化要求：

- account 创建时，服务端将这两个字段写入 `/{account_id}/_system/setting.json` 的 `namespace` 节点
- 服务启动后加载 account 配置时，这两个字段进入 `AccountNamespacePolicy`
- 后续所有 URI 解析、目录初始化、检索过滤、session 可见性都只能读取这一个来源

修改策略：

- 本阶段不提供 account policy 更新接口
- account 创建完成后，这两个字段视为不可变
- 不支持通过手工修改 `setting.json` 的方式在线切换 policy

原因：

- 修改任一字段都会改变 canonical URI 结构
- 已有目录路径、索引字段、session 派生引用都会跟着变化
- 在没有迁移工具、校验工具和回滚策略之前，不应支持在线修改

如果后续业务确实需要调整：

- 方案一：新建 account，按新 policy 初始化，再做数据迁移
- 方案二：单独立项做离线迁移，不通过常规 admin API 在线修改

### 2.3 配置生效层级

这两个字段只在 `account` 上定义，不在 `user`、`agent`、`session` 或请求级覆盖。

原因：

- 命名空间拓扑属于 account 级存储契约，不适合在请求级动态切换
- 一旦允许运行时切换，就会导致同一 account 内路径布局和检索过滤规则不稳定
- 统一到 account 级后，目录、索引、权限判断才能保持一致

---

## 三、命名空间模型

### 3.1 总体原则

- 逻辑 URI 采用显式嵌套路径，不再依赖不可读的 hash space
- `user`、`agent`、`session` 都有 canonical URI
- 简写 URI 可以保留，但内部必须先 canonicalize，再进入存储、检索和权限判断

### 3.2 四种拓扑矩阵

#### `isolate_user_scope_by_agent=false`，`isolate_agent_scope_by_user=false`

```text
viking://user/{user_id}/...
viking://agent/{agent_id}/...
viking://session/{session_id}/...
```

底层目录：

```text
/local/{account_id}/user/{user_id}/...
/local/{account_id}/agent/{agent_id}/...
/local/{account_id}/session/{session_id}/...
```

访问规则：

- 当前请求身份为 `(user_id=ua, agent_id=aa)` 时，可以访问 `viking://user/ua/...` 下的全部 user 数据，不受当前 agent 影响
- 当前请求身份为 `(user_id=ua, agent_id=aa)` 时，可以访问 `viking://agent/aa/...` 下的全部 agent 数据，不受当前 user 影响
- 不能访问其他 user 的 `viking://user/{other_user_id}/...`
- 不能访问其他 agent 的 `viking://agent/{other_agent_id}/...`
- `session` 按 account 共享，访问规则独立于这两个字段

适用场景：

- 一个 account 内多个 user 共同使用一组标准化 agent，且 agent 的技能、案例、工作区都希望在团队内共享
- 用户侧资料也希望跨 agent 复用，例如统一的用户画像、偏好、长期事实
- 更适合协作型工作区，而不是强隔离场景

#### `isolate_user_scope_by_agent=false`，`isolate_agent_scope_by_user=true`

```text
viking://user/{user_id}/...
viking://agent/{agent_id}/user/{user_id}/...
viking://session/{session_id}/...
```

底层目录：

```text
/local/{account_id}/user/{user_id}/...
/local/{account_id}/agent/{agent_id}/user/{user_id}/...
/local/{account_id}/session/{session_id}/...
```

访问规则：

- 当前请求身份为 `(user_id=ua, agent_id=aa)` 时，可以访问 `viking://user/ua/...` 下的全部 user 数据，不受当前 agent 影响
- 当前请求身份为 `(user_id=ua, agent_id=aa)` 时，只能访问 `viking://agent/aa/user/ua/...`
- 不能访问 `viking://agent/aa/user/{other_user_id}/...`
- 不能访问 `viking://agent/{other_agent_id}/user/ua/...`
- `session` 按 account 共享，访问规则独立于这两个字段

适用场景：

- 同一个 user 会在多个 agent 之间切换，希望个人资料和长期偏好能直接复用
- 不同 user 虽然可能使用同名 agent，但 agent 的案例、instructions、技能配置等沉淀需要彼此隔离
- 适合希望保留 user 侧共享，但对 agent 侧共享更谨慎的部署方式

#### `isolate_user_scope_by_agent=true`，`isolate_agent_scope_by_user=false`

```text
viking://user/{user_id}/agent/{agent_id}/...
viking://agent/{agent_id}/...
viking://session/{session_id}/...
```

底层目录：

```text
/local/{account_id}/user/{user_id}/agent/{agent_id}/...
/local/{account_id}/agent/{agent_id}/...
/local/{account_id}/session/{session_id}/...
```

访问规则：

- 当前请求身份为 `(user_id=ua, agent_id=aa)` 时，只能访问 `viking://user/ua/agent/aa/...`
- 不能访问 `viking://user/ua/agent/{other_agent_id}/...`
- 当前请求身份为 `(user_id=ua, agent_id=aa)` 时，可以访问 `viking://agent/aa/...` 下的全部 agent 数据，不受当前 user 影响
- 不能访问其他 agent 的 `viking://agent/{other_agent_id}/...`
- `session` 按 account 共享，访问规则独立于这两个字段

适用场景：

- agent 本身代表某种共享岗位能力或团队能力，希望其案例、技能、instructions 等沉淀在所有 user 之间复用
- 但同一个 user 在不同 agent 下的个人资料或用户记忆不希望互通
- 适合“agent 是主实体，user 只是调用者”的平台型场景

#### `isolate_user_scope_by_agent=true`，`isolate_agent_scope_by_user=true`

```text
viking://user/{user_id}/agent/{agent_id}/...
viking://agent/{agent_id}/user/{user_id}/...
viking://session/{session_id}/...
```

底层目录：

```text
/local/{account_id}/user/{user_id}/agent/{agent_id}/...
/local/{account_id}/agent/{agent_id}/user/{user_id}/...
/local/{account_id}/session/{session_id}/...
```

访问规则：

- 当前请求身份为 `(user_id=ua, agent_id=aa)` 时，只能访问 `viking://user/ua/agent/aa/...`
- 不能访问 `viking://user/ua/agent/{other_agent_id}/...`
- 当前请求身份为 `(user_id=ua, agent_id=aa)` 时，只能访问 `viking://agent/aa/user/ua/...`
- 不能访问 `viking://agent/aa/user/{other_user_id}/...`
- 不能访问 `viking://agent/{other_agent_id}/user/ua/...`
- `session` 按 account 共享，访问规则独立于这两个字段

适用场景：

- 每个 `(user, agent)` 组合都应视为独立工作单元，不能共享任何用户记忆或 agent 沉淀
- 强合规、强审计、强租户内隔离场景
- 适合外包、敏感业务、或需要最小共享面的部署方式

### 3.3 scope 内部结构

#### user scope

```text
memories/
  preferences/
  entities/
  events/
profile.md
```

#### agent scope

```text
memories/
  cases/
  patterns/
skills/
instructions/
```

#### session scope

```text
messages.jsonl
history/
tools/
.meta.json
```

### 3.4 简写 URI 规则

保留如下简写：

- `viking://user/...`
- `viking://agent/...`

但内部一律按当前 account policy 和请求身份展开为 canonical URI。

示例：当前身份为 `(account=acme, user=ua, agent=aa)`

当 `isolate_user_scope_by_agent=false` 时：

```text
viking://user/memories/preferences/
=> viking://user/ua/memories/preferences/
```

当 `isolate_user_scope_by_agent=true` 时：

```text
viking://user/memories/preferences/
=> viking://user/ua/agent/aa/memories/preferences/
```

当 `isolate_agent_scope_by_user=false` 时：

```text
viking://agent/memories/cases/
=> viking://agent/aa/memories/cases/
```

当 `isolate_agent_scope_by_user=true` 时：

```text
viking://agent/memories/cases/
=> viking://agent/aa/user/ua/memories/cases/
```

要求：

- 存储层只处理 canonical URI
- 向量索引写入只处理 canonical URI
- 权限判断只处理 canonical URI

---

## 四、Session 模型重构

### 4.1 Session 作用域调整

将 `session` 从 user 目录下移出，统一为 account 级共享：

```text
viking://session/{session_id}
```

底层目录：

```text
/local/{account_id}/session/{session_id}
```

设计原因：

- 同一 account 内可能有多个 user / agent 共同参与一个会话
- 把 session 绑定在某个 user 根目录下，会让会话共享变得别扭
- session 作为临时协作容器，更适合按 account 共享，再通过消息身份描述参与者

### 4.2 Session 可见性与权限

推荐权限：

- `create / list / get / add-message / commit`
  - 同 account 内可访问
- `delete`
  - 仅 `ADMIN`
  - 或 `ROOT`

这意味着：

- session 可被 account 内其他 user 看到和继续使用
- 删除权限单独收紧，避免 shared session 的“所有者”语义不清

### 4.3 Session 元数据

`session/.meta.json` 新增字段：

- `created_by_user_id`
- `participant_user_ids`
- `participant_agent_ids`

说明：

- `created_by_user_id` 表示“这个 session 最初是由哪个请求身份创建出来的”
- 写入时机：
  - 第一次真正创建 session 时写入 `.meta.json`
  - 具体包括 `POST /api/v1/sessions` 创建
  - 以及未来如果保留 `auto_create` 语义，则第一次自动创建时也写入
- 写入来源：
  - 直接取创建请求对应的 `RequestContext.user.user_id`
  - 也就是发起这次创建操作的请求用户
- 一旦写入，不再随着后续 `add-message` 的 `role_id` 变化而变化
- 它是审计字段，不直接参与删除权限判断
- `participant_*` 只用于索引、展示、后续提取分流，不承担 ACL

也就是说：

- `created_by_user_id` 不是客户端额外传的参数
- 它是服务端在“创建 session 的那一刻”从当前登录身份里自动记下来的
- 它和消息里的 `role_id` 不是一回事
  - `created_by_user_id` 解决“这个 session 最初由谁创建”
  - `role_id` 解决“这条消息是谁说的”

---

### 4.4 `add-message` 接口扩展

在 `POST /api/v1/sessions/{session_id}/messages` 中新增 `role_id`。

示例：

```json
{
  "role": "user",
  "role_id": "ua",
  "content": "你好"
}
```

```json
{
  "role": "assistant",
  "role_id": "aa",
  "parts": [...]
}
```

规则：

- `role == "user"` 时，`role_id` 表示真实 `user_id`
- `role == "assistant"` 时，`role_id` 表示真实 `agent_id`
- 其他 role 暂不使用 `role_id`

### 4.5 请求头语义与 `RequestContext` 解析

服务端需要区分“真实调用者身份”和“本次请求按谁的视角执行”。

在不同认证模式下，`X-OpenViking-Account`、`X-OpenViking-User`、`X-OpenViking-Agent` 的语义如下：

- `trusted` 模式
  - `X-OpenViking-Account / User / Agent` 表示真实调用者身份
  - 此时不存在模拟其他用户身份的语义

- `api_key + ROOT`
  - `X-OpenViking-Account / User / Agent` 表示本次请求的生效上下文
  - `ROOT` 可以显式指定本次请求作用到哪个 account / user / agent

- `api_key + ADMIN`
  - `X-OpenViking-User / Agent` 表示本次请求的生效 user / agent 上下文
  - `ADMIN` 只能在自己的 account 内使用这组请求头模拟用户视角
  - `ADMIN` 不允许通过 `X-OpenViking-Account` 切换到其他 account

- `api_key + USER`
  - `user_id` 只能从当前 user key 对应的身份解析
  - `X-OpenViking-Agent` 仍可用于指定当前请求的 agent 上下文
  - `USER` 不允许通过 `X-OpenViking-User` 切换到其他 user

`RequestContext` 的语义统一为：

- `ctx.user` 表示本次请求的生效身份
- `ctx.role` 表示真实调用者角色
- namespace 解析、检索过滤、文件系统可见性都按 `ctx.user` 执行
- 管理权限按 `ctx.role` 判断

### 4.6 `add-message` 校验与默认填充

`role_id` 的语义如下：

- `role = "user"` 时，`role_id` 表示消息所属的 `user_id`
- `role = "assistant"` 时，`role_id` 表示消息所属的 `agent_id`

校验与填充规则：

- `trusted`、`ROOT`、`ADMIN`
  - 可以显式传入 `role_id`
  - 如果未显式传入：
    - `role = "user"` 时，默认填充 `ctx.user.user_id`
    - `role = "assistant"` 时，默认填充 `ctx.user.agent_id`

- `USER`
  - 可以显式传入 `role_id`，服务端以传入值为准
  - 如果未显式传入：
    - `role = "user"` 时，默认填充 `ctx.user.user_id`
    - `role = "assistant"` 时，默认填充 `ctx.user.agent_id`

额外约束：

- `role = "user"` 时，`role_id` 语义表示消息所属的 `user_id`，由调用方保证为当前 account 下合法的 `user_id`
- `role = "assistant"` 时，`role_id` 作为 `agent_id` 使用
- 本阶段不引入独立的 agent registry；服务端不对 `role_id` 做格式或上下文一致性校验，语义一致性由调用方承担

### 4.7 消息持久化结构

`Message` 增加字段：

```json
{
  "id": "msg_xxx",
  "role": "assistant",
  "role_id": "aa",
  "parts": [...],
  "created_at": "2026-04-09T10:00:00Z"
}
```

`role_id` 表示这条消息的 actor 身份，不等于真实调用者身份。后续从消息派生 tool / skill / memory 归属时，应以消息自身的 `role + role_id` 为准。

### 4.8 Session 派生 URI

`tool_uri` 统一调整为：

```text
viking://session/{session_id}/tools/{tool_id}
```

后续凡是从 message 中派生 tool / skill / memory 归属时，都应优先使用消息上的 `role + role_id`，而不是默认使用当前请求上下文里的 agent / user。


---

## 五、检索、文件系统与可见性

### 5.1 问题

当前很多逻辑依赖单一字符串字段 `owner_space`。这个字段在以下场景下不够稳定：

- 命名空间拓扑有四种组合
- session 改成 account 共享后，不能再把 session 误归到 user owner
- user/agent 简写 URI 需要展开
- 仅靠一个字符串，不方便做结构化可见性判断

### 5.2 索引中的归属信息

向量索引和 `Context` 的归属字段调整为：

- 保留已有字段：
  - `account_id`
  - `uri`
- 新增字段：
  - `owner_user_id`
  - `owner_agent_id`
- 移除字段：
  - `owner_scope`

其中：

- `uri` 使用标准路径
- `scope` 从 `uri` 的前缀解析得到，不单独存 `owner_scope`
- `owner_user_id` / `owner_agent_id` 用来表达这条数据绑定到哪个 user / agent
- `uri` 和 `owner_user_id` / `owner_agent_id` 需要保持一致，不能互相冲突

### 5.3 写入规则

所有写入到索引的数据，都必须先确定标准 URI，再由标准 URI 生成 `owner_user_id` / `owner_agent_id`。

写入时规则如下：

#### resource

```text
uri = viking://resources/...
owner_user_id = null
owner_agent_id = null
```

#### user scope

```text
uri = viking://user/{user_id}/...                         if isolate_user_scope_by_agent = false
uri = viking://user/{user_id}/agent/{agent_id}/...       if isolate_user_scope_by_agent = true

owner_user_id = user_id
owner_agent_id = null                                    if isolate_user_scope_by_agent = false
owner_agent_id = agent_id                                if isolate_user_scope_by_agent = true
```

#### agent scope

```text
uri = viking://agent/{agent_id}/...                      if isolate_agent_scope_by_user = false
uri = viking://agent/{agent_id}/user/{user_id}/...       if isolate_agent_scope_by_user = true

owner_agent_id = agent_id
owner_user_id = null                                     if isolate_agent_scope_by_user = false
owner_user_id = user_id                                  if isolate_agent_scope_by_user = true
```

#### session

```text
uri = viking://session/{session_id}/...
owner_user_id = null
owner_agent_id = null
```

### 5.4 查询过滤规则

检索时先加：

```text
account_id == ctx.account_id
```

然后根据当前 `(user_id, agent_id)` 和 account policy，算出本次请求可见的路径根：

#### resource 根路径

```text
viking://resources/
```

#### session 根路径

```text
viking://session/
```

#### user 根路径

```text
viking://user/{user_id}/...                         if isolate_user_scope_by_agent = false
viking://user/{user_id}/agent/{agent_id}/...       if isolate_user_scope_by_agent = true
```

#### agent 根路径

```text
viking://agent/{agent_id}/...                      if isolate_agent_scope_by_user = false
viking://agent/{agent_id}/user/{user_id}/...       if isolate_agent_scope_by_user = true
```

检索过滤由两部分组成：

- `account_id == ctx.account_id`
- `uri` 必须落在上述可见根路径下
- `owner_user_id` / `owner_agent_id` 必须满足当前 policy 对应的可见范围

如果请求显式传了 `target_uri`，则：

- 先把 `target_uri` 归一化成标准路径
- 校验该 `target_uri` 是否在当前请求可见范围内
- 再把检索范围收敛到该 `target_uri` 前缀下

说明：

- session 在本方案中按 account 共享，因此统一落在 `viking://session/`
- `uri` 用来表达真实路径范围
- `owner_user_id` / `owner_agent_id` 用来表达绑定到哪个 user / agent
- 文件系统可见性与检索过滤必须复用同一套判断规则

### 5.5 结果校验与错误处理

检索返回后，服务端仍需对命中结果做一次基于 `uri + owner_user_id + owner_agent_id` 的一致性校验，避免索引脏数据或历史遗留数据泄漏。

错误处理如下：

- `target_uri` 路径形状和当前 policy 不匹配：返回 `400`
- `target_uri` 本身形状没问题，但当前身份无权访问：返回 `403`
- 不传 `target_uri` 时，按当前身份可见范围做全局检索

---

### 5.6 文件系统与目录初始化

#### VikingFS

需要引入统一的 namespace resolver，承担以下职责：

- 根据 account policy + 当前身份生成 canonical user / agent 根路径
- 将简写 URI canonicalize
- 解析 URI 对应的 owner 结构
- 判断路径是否对当前 `(account_id, user_id, agent_id)` 可见

`VikingFS` 的以下能力都需要切到新规则：

- `_uri_to_path`
- `_path_to_uri`
- `_is_accessible`
- `ls / tree / stat / read / write`

#### DirectoryInitializer

目录初始化要区分两类节点：

- 真实作用域根
  - 需要生成 `memories / skills / instructions` 等预置结构
- 中间容器目录
  - 只承担路径承载作用
  - 不重复写入预置抽象与 overview

例如在 `isolate_user_scope_by_agent=true` 且 `isolate_agent_scope_by_user=true` 下：

```text
viking://agent/{agent_id}
```

只是容器；

```text
viking://agent/{agent_id}/user/{user_id}
```

才是实际的 agent scope 根。

---

## 六、接口与类型变更

### 6.1 Admin API

`POST /api/v1/admin/accounts` 新增：

- `isolate_user_scope_by_agent`
- `isolate_agent_scope_by_user`

`GET /api/v1/admin/accounts` 返回同样两项配置。

account 级 namespace policy 持久化在：

- `/local/{account_id}/_system/setting.json`

文件示例：

```json
{
  "namespace": {
    "isolate_user_scope_by_agent": false,
    "isolate_agent_scope_by_user": false
  }
}
```

如果创建请求未显式传值，则服务端使用默认值：

- `isolate_user_scope_by_agent = false`
- `isolate_agent_scope_by_user = false`

本阶段不提供修改 account policy 的更新接口。

原因：

- 修改 policy 本质上是重排目录和索引
- 没有迁移机制前，不应允许运行时修改
- 直接手工修改 `setting.json` 也不在支持范围内

### 6.2 核心类型

需要扩展：

- `AccountInfo`
- `ResolvedIdentity`
- `RequestContext`
- `Message`
- `SessionMeta`
- `Context`

## 七、影响模块

本次方案预计影响如下模块族：

- 多租户与认证
  - account 配置读写
  - auth middleware
  - request context
- 命名空间解析
  - `UserIdentifier`
  - 新增统一 resolver / policy
- 文件系统
  - `VikingFS`
  - `DirectoryInitializer`
- session
  - `Session`
  - `SessionService`
  - `sessions router`
  - `Message`
- 检索与向量
  - `Context`
  - embedding message converter
  - semantic processor
  - collection schema
  - vector backend filter
- 接入层
  - Python SDK
  - HTTP client
  - Rust CLI

---

## 八、兼容性策略

本次不采用“全量兼容”或“双读双写”策略，而是按 scope 分别处理：

### 8.1 user scope

- `user` 侧默认路径本身就是 `viking://user/{user_id}/...`
- 如果新拓扑仍然采用 user 共享形态，则 AGFS 路径天然兼容
- 因此本阶段不对 `user` 侧做目录迁移

### 8.2 session scope

- `session` 只在 AGFS 中维护，不进入 VectorDB
- 在确认 account 内不存在同名 `session_id` 的前提下，session 迁移按纯目录移动处理：

```text
viking://session/{user_id}/{session_id}
-> viking://session/{session_id}
```

- 本阶段不保留旧 session 根路径兼容读
- 迁移完成后，session 的 list / get / delete / add-message / commit 全部只认新路径

### 8.3 agent scope

- 本次不支持旧 agent 数据自动迁移到新命名空间
- 不保留旧 hash namespace 的兼容访问
- 不继续让 `memory.agent_scope_mode` 参与服务端命名空间决策
- 如需保留旧 agent 数据，可在升级前自行使用现有 `ovpack` 做离线备份

推荐迁移方式：

- 升级前：在旧版本中从 legacy hash namespace 导出，例如：

```bash
ov export viking://agent/{legacy_agent_space_hash}/memories ./agent_memory.ovpack
```

- 升级后：在新版本中按目标 account policy 导入到 agent space 的父目录：

```bash
# isolate_agent_scope_by_user = false
ov import ./agent_memory.ovpack viking://agent/{agent_id}/ --force

# isolate_agent_scope_by_user = true
ov import ./agent_memory.ovpack viking://agent/{agent_id}/user/{user_id}/ --force
```

- 不要把 `.ovpack` 直接导入到 `.../memories/` 本身，否则会得到 `.../memories/memories/...`
- 当 `isolate_agent_scope_by_user = true` 时，`viking://agent/{agent_id}/user/` 不是合法目标；必须显式提供 `user_id}`

- 当前 agent root 是 hash space，而不是显式 `agent_id`
- hash 值不能从结果稳定反推出原始 `user_id / agent_id`
- 如果目标拓扑还涉及 agent 共享，会进一步引入多份旧数据合并问题

### 8.4 总体取舍

本次兼容策略总结如下：

- `user`：天然兼容，不迁
- `session`：直接 `mv`
- `agent`：不自动兼容，只提供 ovpack 导出

这个取舍的目的是把新命名空间模型尽快收敛为单一存储契约，而不是把 legacy hash 逻辑继续带入新实现。

---

## 九、风险与边界

### 9.1 混合参与者 session 的提取策略尚未定义

当一个 session 同时包含多个 user 和多个 assistant 时，后续 `commit / extract` 如何分流到对应 user / agent 作用域，是独立问题。

本次只解决：

- 会话可以共享
- 每条消息的参与者身份可追踪
- 检索与目录可见性有稳定 owner 语义

### 9.2 session 共享不等于 participant ACL

本方案采用 account 级共享 session。也就是说，同 account 内用户都可以看见 session。

如果未来需要“仅参与者可见”的 session，需要额外引入 participant ACL 模型，这不在本次范围内。

### 9.3 拓扑切换不可在线修改

一旦 account 已有数据，切换 `isolate_user_scope_by_agent` 或 `isolate_agent_scope_by_user` 会导致：

- 目录 root 变化
- 索引字段变化
- 检索过滤条件变化

因此本阶段不支持在线修改。

---

## 十、测试方案

### 10.1 account policy

- 创建 account 时两项配置写入成功
- list account 能返回两项配置
- 默认值为 `false / false`
- 四种组合可正确加载

### 10.2 namespace matrix

- `user` / `agent` canonical URI 生成正确
- 简写 URI 展开正确
- 四种拓扑下底层目录映射正确
- 中间容器目录与真实作用域根区分正确

### 10.3 session

- session 根路径为 `viking://session/{session_id}`
- session list 为 account 级
- `role_id` 校验正确
- `Message.role_id` 持久化正确
- `participant_user_ids` / `participant_agent_ids` 正确累积
- `ADMIN / ROOT` 删除权限正确

### 10.4 retrieval / visibility

- FS `ls / stat / read` 与检索结果一致
- user scope 在四种 policy 下可见性正确
- agent scope 在四种 policy 下可见性正确
- session scope 按 account 共享
- cross-account 不泄漏

### 10.5 negative cases

- 缺失 `role_id`
- 非法 nested URI
- policy 与路径不匹配
- 尝试访问非本 user / agent 可见作用域

---

## 十一、推荐落地顺序

建议按以下顺序实施：

1. 引入 account policy 与统一 namespace resolver
2. 改造 `UserIdentifier`、`RequestContext`、Admin API
3. 改造 `VikingFS` 与目录初始化
4. 改造 `session` 路径与 `Message.role_id`
5. 改造 `Context`、embedding、vector schema 与过滤逻辑
6. 最后更新 SDK / CLI 与文档

这样可以先把“路径与身份语义”固定，再去改造检索与接入层，风险更可控。

---

## 结论

本方案的核心是把 `user`、`agent` 的共享关系显式提升为 account 级策略，并将 `session` 升级为 account 级共享容器，再用 `role_id` 和标准 URI 把消息、目录和检索统一到同一套身份模型上。

如果接受这份方案，后续实现应严格遵守两条主线：

- 所有 URI 先 canonicalize，再进入存储、检索、权限判断
- 所有可见性与索引判断都基于 `account_id + uri + owner_user_id + owner_agent_id`

这两条一旦立住，后续 mixed session extraction、participant ACL、审计追踪才有稳定的基础。
