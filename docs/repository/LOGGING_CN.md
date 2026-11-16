# 开发者日志输出说明

本文件规定 OpenViking 各运行时（Python、Rust、C++ 引擎、TypeScript）的日志级别语义、内容边界和落地方式。它面向提交代码的开发者，不是给最终用户的部署说明；`log.level`、`log.format`、`log.output` 等配置项仍以[配置指南](../zh/guides/01-configuration.md)为准。

级别用途约束适用于 OpenViking 自有代码；第三方依赖保留原有级别语义，按 8.10 接入统一输出。本文包含尚未落地的目标规范，现状和实施顺序分别见第 7、9 节，不能把规范示例视为当前已支持的配置能力。

相关入口：

- 日志实现：`openviking_cli/utils/logger.py`（`get_logger`、`reconfigure_logging`、OTel 桥接）
- 日志配置模型：`openviking_cli/utils/config/log_config.py`
- 审计事件总线：`openviking/observability/events.py`、`openviking/observability/usage_audit/`
- 贡献流程：[CONTRIBUTING_CN.md](CONTRIBUTING_CN.md)

---

## 1. 级别语义

一条日志应该落在哪一级，只取决于**这条记录的作用**，不取决于「看起来严不严重」。

| 级别 | 唯一职责 | 允许出现的内容 | 禁止出现的内容 |
| --- | --- | --- | --- |
| `CRITICAL` | 进程级不可继续 | 启动致命失败、关键状态无法恢复 | 一般业务错误、可重试失败 |
| `ERROR` | 需要人介入的失败 | 未处理异常、依赖不可用导致请求失败、数据一致性风险、后台任务重试耗尽、审计/安全机制自身失效 | 预期内的业务拒绝（4xx 记 `INFO`）、重试中的单次失败、已降级成功、性能偏慢、脏数据跳过 |
| `WARNING` | **仅限审计类事件** | 权限与角色变更、账号成员操作、密钥生命周期、认证失败、配置与数据删除等安全敏感动作，以及 `api_key` 模式下 ROOT 身份的任何调用（见 3.5） | 兜底降级、重试退避、依赖缺失、慢调用、跳过/忽略、脏数据、缓存未命中、观测侧通道失败 |
| `INFO` | 兜底逻辑、状态迁移与请求拒绝 | 重试与退避、降级与回退、熔断打开/恢复、主备切换、启动/关闭、一次性迁移、汇总统计、4xx 业务拒绝（参数错误、未找到、未授权） | 成功请求的逐条业务细节、循环内逐条明细、payload 内容 |
| `DEBUG` | 业务过程 | 请求入参与出参摘要、状态机迁移、检索与解析中间结果、分支决策、缓存操作细节 | 敏感值、超大对象、热循环内的高频逐条输出 |

补充约定：

- `ERROR` 和 `CRITICAL` 都不允许在正常控制流里出现。如果一次失败能通过重试、降级或前端提示解决，它就不是 `ERROR`。
- `WARNING` 是**受控级别**，语义边界由本文件第 3 节锁定。任何「我觉得值得注意」的记录都不能进 `WARNING`，那是 `INFO`。
- `INFO` 承载「系统自己扛住了」的信号。兜底、重试、退避、熔断的状态变化必须可观测，但不需要人立刻处理。
- `DEBUG` 是默认的业务过程出口。写业务代码时，先假设它属于 `DEBUG`，再判断是否命中第 3 节的审计清单或第 5 节的兜底与拒绝清单。
- 无论落在哪一级，**能写什么字段**由第 4 节的日志安全规则决定。级别合规不代表内容合规。
- 4xx 固定记 `INFO`：不升级为 `ERROR`（不是故障），也不下沉到 `DEBUG`（否则默认级别下看不到调用方误用）。
- ROOT 调用是级别规则的单点例外：接口本身该走 `INFO` 或 `DEBUG`，但只要是 `api_key` 模式下的 ROOT 身份发起，就按 `WARNING` 留审计（见 3.5）。dev 与 trusted 模式不计。

### 判定顺序

```text
这次记录是不是安全敏感的用户/管理员动作？
  是 → WARNING（审计，见第 3 节）
  否 ↓
请求/任务最终失败且需要人介入吗？
  是 → ERROR
  否 ↓
这次记录描述的是「系统自己兜住了」的过程吗（重试、降级、熔断、回切）？
  是 → INFO
  否 ↓
DEBUG
```

### 容易误判的边界

| 场景 | 结论 | 理由 |
| --- | --- | --- |
| 客户端参数错误、未找到资源、未授权（4xx） | `INFO` | 预期结果，不是故障；但反映调用方误用，需要可观测 |
| 认证失败、越权尝试（401/403 的安全面） | `INFO` 响应日志 + 审计 `WARNING` | 两条记录目的不同，见 3.1 |
| `api_key` 模式下 ROOT 身份调用任意接口（含只读） | `WARNING` | 超级用户调用一律留审计，不以接口的业务级别为准；见 3.5 |
| 第一次调用失败、第二次成功 | `INFO`（记录重试） | 系统已恢复 |
| 重试次数耗尽、请求最终失败 | `ERROR`（只记一条） | 需要人介入 |
| 主 provider 失败切到备 provider 并成功 | `INFO` | 兜底成功 |
| 切到备 provider 后结果质量下降/字段缺失 | `INFO` + 明确字段标记，不升级为 `WARNING` | 仍属兜底，不是审计 |
| 依赖不可用但走了默认值（如摘要为空） | `INFO` | 兜底 |
| 依赖不可用且无兜底、功能整体不可用 | `ERROR` | 故障 |
| 慢调用超阈值 | `INFO`（需要长期观测时）或 `DEBUG` | 不是错误 |
| 观测/指标写入自身失败 | `INFO`，首次 `ERROR` | 旁路通道，不得污染业务级别 |
| 缓存锁抢不到、回源数据库 | `INFO` | 兜底 |
| 循环里 N 条数据里有 M 条脏数据 | 一条 `INFO` 汇总 + 明细 `DEBUG` | 控制噪声 |
| 配置里出现未知字段、非法值被忽略 | `INFO` | 兼容兜底 |
| 请求头 `X-Request-ID` 非法、已自动重建 | `INFO` | 客户端卫生问题，非安全事件 |

---

## 2. 为什么不是按「严重程度」分级

按严重程度分级会让级别随开发者主观判断漂移：同一类失败，A 觉得是 `WARNING`，B 觉得是 `ERROR`。本仓库当前就呈现了这个结果——`WARNING` 成了使用最多的级别（见第 7 节）。

按**作用**分级后，级别是可判定的客观属性：

- 需要人处理的 → `ERROR`，可以直接接告警。
- 需要留痕追责的 → `openviking.audit` 的 `WARNING`，按审计事件字段接审计管道。
- 系统自愈的过程、调用方被正常拒绝的结果 → `INFO`，可以直接接容量/稳定性看板。
- 其余细节 → `DEBUG`，只在排障时打开。

自有代码可以据此选择消费链路；混合输出中的第三方日志必须结合 logger 和事件字段路由，不能仅凭 `WARNING` 判定为审计。

---

## 3. 审计类日志（自有代码中 `WARNING` 的唯一合法用途）

### 3.1 必须覆盖的事件

以下动作发生时必须有审计记录。当前仓库在多数位置缺失（见 7.3），新增这些记录属于纯增量改动。

**账号与成员**

- 创建/删除账号，账号状态变更（启用、停用、迁移）
- 邀请、加入、移除成员；成员角色变更（`set_role`）
- 账号级设置的重置与覆盖（含 `reset_memory_template` 一类动作）

**权限**

- ACL 授予、撤销、删除（`grant_acl` / `revoke_acl` / `delete_acl`）
- 跨账号、跨用户的资源授权
- 管理员、ROOT 权限的授予与回收

**密钥与凭证**

- API Key 创建、重新生成、吊销、迁移
- OAuth/OIDC 身份绑定、解绑、受信身份批量注册
- 密钥/凭证明文导出

**认证结果**

- 登录失败、Token 校验失败、签名校验失败
- 越权访问被拒绝（角色不满足、跨租户访问）
- 认证健康检查从通过变为异常

需要注意：这一组是审计记录，与请求返回 4xx 时的 `INFO` 响应日志不是同一条。响应日志记「这次请求被拒了」，审计记录记「谁在什么时候尝试了什么」。两者的 logger、字段、消费者都不同，允许并存；但普通 4xx（参数错误、资源不存在）只记 `INFO`，不进审计。

**配置与数据**

- 服务端配置、用户配置、Runtime 配置的写入与回滚
- 账号数据删除、任务删除、快照删除、批量导出（ovpack）
- 隐私条目的写入与清理

### 3.2 必填字段

一条审计记录至少包含（字段取值同样受第 4 节约束，`peer_id`、密钥、正文不得进入审计）：

| 字段 | 说明 |
| --- | --- |
| `ts` | UTC 时间戳，ISO 8601，固定毫秒精度，格式见 8.10.1 |
| `event` | 已登记的稳定事件名，统一以 `audit.` 开头，如 `audit.account.create`、`audit.acl.grant` |
| `result` | `success` / `failure` |
| `actor_id` / `actor_role` / `actor_type` | 操作者身份；已知 `actor_type` 取 `user`、`admin`、`root`、`service`。认证失败等无法确认身份的场景，相应字段为 `null`，不能把输入的用户名当已认证主体 |
| `account_id` | 被操作对象所属账号；无账号范围的 ROOT 调用为 `null`，不能填虚构的默认账号 |
| `target_type` / `target_id` | 对象类型与安全标识，如 `user`、`api_key`、`config`、`http_route`；不得把密钥或原始 URI 当标识 |
| `action` | 由 helper 从 `event` 的末段生成 |
| `request_id` / `trace_id` | 从执行上下文取得；没有对应上下文时为 `null` |
| `source_ip` / `user_agent` | 经 4.2 处理的入口信息；不可用或不能安全提取时为 `null` |
| `error_code` | 失败时必须是稳定错误码，成功时为 `null`；不放异常消息或堆栈 |

以上字段必须存在，但明确允许为空的字段使用 `null`，文本出口按 8.10 渲染。`actor_role` 是实际生效的角色，`actor_type` 是主体类型；后台服务使用 `service`，不新增 `system` 这一别名。ROOT 调用还必须包含 3.5 的 HTTP 字段。

### 3.3 输出形式

当前范围：日志规范改造只调整现有日志的级别、内容、格式和初始化。已有 Usage/Audit 存储会保存用量及 HTTP 请求记录，但尚未处理本节的 `audit.*` 安全操作事件。对应的辅助函数、事件覆盖、schema、落库、查询和保留期管理记为独立功能待办，本轮日志改造不新增这些能力。

审计需要**双写**，不能只有其中一路：

1. **结构化事件**：目标是经事件总线发出，再由 `usage_audit` 投影持久化，形成可查询、可保留期管理的主通道。现有 `try_publish_event()` 是 best-effort，现有投影也尚未处理这些 `audit.*` 事件；落地时必须补齐事件识别、持久化和失败反馈，不能把「调用发布函数」等同于「审计已落盘」。
2. **日志副本**：写入专用 logger `openviking.audit`，级别 `WARNING`。这是给日志采集、SIEM、离线取证用的旁路副本。

日志副本的采集条件必须同时检查 `logger=openviking.audit` 和明确的审计 `event` 字段，并校验必填字段。不能把整个进程的 `WARNING` 都送入审计管道；第三方告警的边界见 8.10.6。

所有调用点统一使用待实现的 `log_audit()`。以下代码是目标接口示例，**不是当前可导入的 API**。helper 的职责固定为：

- 调用方显式传入 `event`、`result`、操作者、账号、目标和 `error_code`；不从任意请求参数猜测身份或目标。
- helper 捕获事件时间 `ts`、生成 `action`，从执行上下文提取 `request_id`、`trace_id`、`source_ip`、`user_agent`；没有上下文时保留上述字段并填 `null`。
- 按 4.2 校验、转换字段后，将**同一份完整记录**送往两个出口。日志副本使用固定消息 `"audit event"`，业务字段全部放在 `extra`，由 8.10 的 formatter 输出；采集端不需要解析消息正文来寻找事件名或身份。

例如 ACL 授权完成后：

```python
log_audit(
    event="audit.acl.grant",
    result="success",
    actor_id=actor_id,
    actor_role=actor_role,
    actor_type="admin",
    account_id=account_id,
    target_type="user",
    target_id=target_user_id,
    error_code=None,
)
```

示例假定调用方是已认证管理员；其他主体传入真实的 `actor_type`。失败时使用 `result="failure"` 和对应的稳定 `error_code`，其他字段保持同一 schema。

约束：

- 审计日志**先执行后记录**，记录实际结果（成功与失败都要写），不要只记成功。
- 字段校验、发布或持久化失败由审计设施在失败边界记录一条安全的 `ERROR`，并且不能回滚业务事务；不能吞掉失败后宣称双写成功。
- 审计记录不得被业务层的 `except Exception` 静默吞掉。
- 审计消息里禁止出现明文密钥、密码、Token、完整请求体。




### 3.4 反例

```python
# 反例：账号迁移写成 INFO，审计管道拿不到
logger.info("Migrated API key for user %s in account %s", user_id, account_id)

# 正例：由后台迁移服务执行，log_audit 为 3.3 的目标接口
log_audit(
    event="audit.api_key.migrate",
    result="success",
    actor_id="api-key-migrator",
    actor_role=service_role,
    actor_type="service",
    account_id=account_id,
    target_type="api_key",
    target_id=user_id,
    error_code=None,
)
```

这里 `service_role` 是服务实际使用的角色，`target_id` 用密钥所属用户的稳定 ID，不是密钥值。无请求上下文的后台操作，其关联字段由 helper 填 `null`。

### 3.5 ROOT 调用

ROOT 是最高权限身份。**在 `api_key` 鉴权模式下，任何以 ROOT 身份发生的调用都必须留一条 `WARNING` 审计**，即使这次调用只读、即使它对业务毫无影响。

理由：ROOT 被误用或泄露时，事后唯一能还原的就是轨迹；只读调用同样可能跨租户读取数据，其危害不比写入小。ROOT 调用普通业务接口时本该落 `INFO`，此时以 `WARNING` 为准。

#### 触发条件

两个条件同时成立才记录：

1. 认证模式为 `api_key`（`AuthMode.API_KEY`）；
2. 经认证解析出的 `identity.role == Role.ROOT`，即持有 root API key（`server/api_keys/key_mgr.py:131`）。

#### 不触发的情况

| 情况 | 原因 |
| --- | --- |
| `dev` 模式（`auth/plugins/dev.py:41`） | 无认证，所有请求被合成 ROOT，不是真实超级用户 |
| `trusted` 模式（`auth/plugins/trusted.py:183`） | ROOT 由网关注入（admin path、`/health` control probe），不代表真实操作者 |
| 自定义 auth 模式 | 语义未知，默认不记；需要审计的模式应在自己插件里调用 `log_audit()` |
| 未认证路由（`/health`、`/ready`、`/metrics`） | 不产生 ROOT 身份 |
| 进程内构造的 ROOT context（TTL、semantic processor、viking_fs、system_assets、pack_service、core） | 不经过认证入口，天然不触发 |

#### 实现位置

`_build_request_context()`（`server/auth/__init__.py:113`）是唯一同时能看到 `identity.role`、认证模式和 `request.state` 的收口点。不要把它散进 38 处 `Role.ROOT` 引用里（其中 18 处是等值判断）。

推荐「入口打标、完成输出」，保证一条记录里既有调用方也有结果：

```python
# _build_request_context()：只打标，不输出
if ctx.role == Role.ROOT and _auth_mode(request) == AuthMode.API_KEY.value:
    request.state.root_audit = {
        "actor_id": identity.user_id or "root-key",
        "account_id": identity.account_id,
    }
```

```python
# 请求完成阶段：输出唯一一条
if (root_audit := getattr(request.state, "root_audit", None)) is not None:
    log_audit(
        event="audit.root.invoke",
        result="success" if status_code < 400 else "failure",
        actor_id=root_audit["actor_id"],
        actor_role="root",
        actor_type="root",
        account_id=root_audit["account_id"],
        target_type="http_route",
        target_id=http_route,
        http_route=http_route,
        http_method=method,
        http_status_code=status_code,
        error_code=None if status_code < 400 else error_code,
    )
```

`http_route` 必须是匹配后的路由模板，不能使用带路径参数或 query 的原始 URL；失败的 `error_code` 来自响应分类，未分类时用固定的 `HTTP_REQUEST_FAILED`。调用发生在执行上下文清理前，由 helper 注入请求关联与入口字段。

`_auth_mode()` 已有实现（`server/auth/__init__.py:32`），不要自己再读一次 config。注意它在拿不到 config 时会回落到 `api_key`，单元测试里要显式注入认证模式，否则测试请求会被当成真实 ROOT。

#### 必填字段

包含 3.2 的全部字段，其中 `event=audit.root.invoke`、`action=invoke`、`actor_role=root`、`actor_type=root`、`target_type=http_route`、`target_id` 为路由模板；另外包含 `http_method`、`http_route`、`http_status_code`。

- root API key 没有账号与用户，`actor_id` 写 `root-key`，不要写成 `default/default`。
- 失败同样记录，`result=failure` 并带 `error_code`。
- 高危动作（ROOT 读取密钥明文、跨账号读写）额外标 `sensitive=true`，便于单独告警。

#### 约束

- 一条请求一条，不重复。入口与完成阶段不要各记一次。
- 不采样。审计采样等于放弃审计；ROOT 轮询（Studio 任务轮询、SDK 健康检查）造成量大时，降低轮询频率或改用非 ROOT 身份，而不是在服务端丢样本。
- 记录内容仍受第 4 节约束：不写请求体、不写路径参数原文、不写上下文内容。

---

## 4. 日志安全规则

日志文件、trace 属性、审计存储是三条独立出口，任一条泄露都算泄露。本节按**字段**规定什么能出来。

### 4.1 白名单，而不是黑名单

默认拒绝。只有 4.2 表中「允许」一栏的字段可以原样输出；其余字段必须转换（哈希、截断、脱敏、取相对路径），或降级为聚合量（长度、计数、耗时、布尔）。判断不了归属时按禁止处理。

### 4.2 字段分级

| 分级 | 字段 | 处理方式 |
| --- | --- | --- |
| 允许 | 日志框架生成的 `ts`、`level`、`logger`、`pid` | 原样输出；logger 使用固定模块名或组件名，不拼接用户输入 |
| 允许 | `account_id`、`user_id`、`request_id`、`trace_id`、`span_id`、`operation`、`http_method`、`http_route`、`http_status_code`、`duration_ms`、`role`、`task_id`、`task_type`、`task_status`、`task_stage`、重试次数、队列深度、`error_code`、`error_type`、`file_type` | 使用已验证的身份/关联标识、固定枚举、异常类型名或数值；`http_route` 只使用路由模板 |
| 允许 | `event`、`action`、`result`、`actor_role`、`actor_type`、`target_type` | 使用已登记事件及固定枚举；按 3.2 校验，不能由用户自由文本生成 |
| 允许 | `degraded`、`sensitive`、`truncated`、`payload`、`total`、`failed`、`skipped`、`original_length` | 布尔值或非负计数，不携带正文 |
| 允许但需校验/转换 | `actor_id`、`target_id` | 来自已认证主体、固定服务标识或已解析对象的稳定 ID；若含路径、URI、peer 段则按 4.4–4.7 转换；不能使用密钥、凭证或任意请求原文。`target_type=http_route` 时仅使用路由模板 |
| 允许但需校验/转换 | `source_ip` | 只保留传输层或受信代理解析并验证的单个 IP 地址；不原样记录转发头，不可确定时为 `null` |
| 允许但需校验/转换 | `user_agent` | 只保留经白名单解析的产品族与版本摘要，去掉其他自由文本并限制为 256 字节；不能安全解析时为 `null`，不保留原始请求头 |
| 允许但需转换 | 本地路径、`viking://` URI、`resource_id`、异常消息 | 按 4.4–4.8 处理 |
| 禁止 | `peer_id`、密钥与凭证、上下文内容、绝对路径、完整请求/响应体、模型输入输出 | 永不输出 |

任务元信息（`task_id`、`task_type`、状态、阶段、耗时、重试与错误计数、队列深度）可以直接打印，这是排障最常用的维度。任务的 `meta` 是例外：只有在确认其值不含 URI、路径、密钥后才可整体打印，否则只打印键名，不打印值。

`resource_id` 只允许作为不透明部署标签打印；一旦它来自环境变量且可能是路径或 URL，按 4.7 处理。

### 4.3 已有的注入基线

`_get_log_context()` 目前只注入 `trace_id`、`span_id`、`request_id`、`operation`、`account_id`、`user_id`；`RootSpanAttributes.to_log_fields()` 也刻意排除了 `url_path` 与 `url_query`。这是本节的既有实现基线，新增注入字段同样受 4.2 约束。

但 `ObservabilityContext.extra` 会无条件合并进日志字段，因此 `extra` 不是法外之地。

### 4.4 `peer_id`

禁止出现在日志、trace 属性、审计字段中，包括四种间接形式：

1. `viking://user/{user_id}/peers/{peer_id}/...` 一类 URI 内嵌 peer_id；
2. `RequestContext.actor_peer_id`；
3. 序列化对象时被带出（`%r`、`%s`、`asdict()`、`model_dump()`）；
4. 任务 `meta` 中的资源定位信息。

替代写法：

```python
# 反例：完整 URI 暴露 peer
logger.info("rebuild failed uri=%s", "viking://user/u1/peers/p9/memories")

# 正例：截断到 user 级，peer 只留关联哈希
logger.info("rebuild failed user=%s peer=%s", user_id, short_hash(peer_id))
```

需要跨日志关联同一 peer 时，用不可逆短哈希（`sha256(peer_id)[:8]`）。哈希只在同一部署内具备关联意义，不要在跨环境分析中复用结论。

### 4.5 密钥与凭证

以下值一律禁止，且禁止任何足以还原的派生形式（前缀、后缀、长度组合）：

- API Key、User Key、`submission_token`、`submission_hash`
- OAuth access/refresh token、JWT、OIDC 断言
- Cookie、`Authorization`、签名头、预签名 URL
- 数据库/Redis 连接串中的凭证
- 加密密钥、`ov privacy` 管理的明文条目

唯一例外：诊断输出必须展示 key 时使用 `mask_api_key()`（仅露末端 4 位），且仅在显式诊断开关下启用。

`repr=False`（`RequestContext.api_key`、`TaskRecord.auth`）只挡 `repr()`，挡不住 `asdict()`、`model_dump()`、`__dict__`。因此规则是**禁止把整个对象塞进日志**，而不是依赖 `repr=False`。

### 4.6 上下文内容

「上下文内容」指任何由用户、模型或外部资源产生、且未被等价剪裁的文本：

- prompt、system prompt、messages、tool call 参数与结果
- 会话正文、记忆正文、摘要正文、Rule 正文、技能正文
- 文档/网页/代码正文与片段、检索命中的 chunk 文本
- 用户输入、模型输出

允许输出的替代量：字符数、token 数、条目数、语言、文件类型、`sha256[:8]`、是否命中。

现有 `log_payloads` 是反例：`models/rerank/jev_rerank.py:191`、`service/rule_jev.py:76` 在开关打开时用 **`WARNING`** 打印完整请求与响应 payload，query 与文档正文全部落盘。整改方向：默认关闭（当前已是默认）、开启时降为 `DEBUG`、强制标注 `payload=true` 并经过脱敏，且只在排障环境启用。

### 4.7 路径

禁止打印绝对路径。它泄露部署目录、主机用户名，以及 `workspace/{account_id}/{user_id}/peers/{peer_id}/` 这样的目录结构——等于把 peer_id 和租户布局一起暴露。

规则：

- 只打印 workspace 内的**相对路径**、纯文件名或扩展名；
- 用户可控路径（zip 条目名、markdown 图片路径、上传文件名、WebDAV 路径）禁止原样打印，它们可能含 `../`、绝对路径或 peers 段；
- 需要定位时用 `相对路径 + sha256[:8]`；
- `viking://` URI 与本地路径同等对待，peer 段按 4.4 处理；
- 异常消息中的路径同样适用；无法可靠提取并转换时不输出异常原文，改记异常类型与稳定错误码。现有 `sanitize_error()` 不处理路径，不能据此认定内容安全。

当前有 34 处日志直接打印 `file_path` / `path_str` / `resolved_path` 一类变量（如 `parse/parsers/markdown.py:845`、`parse/parsers/text_encoding.py:74`），属于待整改范围。

### 4.8 异常与错误消息

异常字符串经常内嵌触发值（`InvalidArgumentError("bad uri: ...")`），所以 `logger.warning("...: %s", e)` 本身就是一条泄露通道。

- 默认只输出 `type(exc).__name__`、由程序确定的稳定错误码及明确允许的结构化字段；不能把 `str(exc)` 当作错误码。
- `task_domain.sanitize_error()` 目前只替换部分凭证前缀并截断长度，不处理绝对路径、peer URI 或普通 `password=` 值。它不是通用日志脱敏器，调用它不构成输出异常文本的许可。
- `sanitize_public_http_error()` 处理已知凭证模式与大小限制，也不保证移除路径、peer 或上下文正文。现有工具只能作为局部防护，不能替代字段白名单。
- 只有经场景明确的安全提取、脱敏与验证后才可输出异常摘要；这项能力落地前省略异常原文。完整堆栈只在 `ERROR` 且显式本地排障时输出，并先处理异常消息、源码行、路径等可能携带的敏感内容；不能直接用 `exc_info=True` 绕过此要求。
- 不输出 `repr(exc.args)` 或未经处理的异常对象。

### 4.9 三个出口同时成立

| 出口 | 现有防护 | 要求 |
| --- | --- | --- |
| 日志文件/stdout | 公共 formatter 与 `log_safety.py` 字段白名单 | 调用点仍负责消息内容安全；formatter 无法脱敏任意文本 |
| OTel trace 属性 | `to_log_fields()` 已排除 `url_path`、`url_query` | 新增 span 属性同样受本节约束；`url.path` 只在确有需要时保留 |
| 审计存储 | `sanitize_public_http_error()`、`sanitize_error()`、`SENSITIVE_TASK_KEYS` 的局部防护 | 仍须按本节校验/转换字段；不能假定现有函数覆盖路径、peer 与正文 |

### 4.10 建议的实现与检查

- 使用 `openviking/observability/log_safety.py`：`short_hash()`、`safe_uri_for_log()`、`safe_path_for_log()`、`safe_log_fields()`；为异常摘要定义按场景提取允许字段的接口，不承诺用通用正则清洗任意异常文本。
- `repr=False` 只作兜底，不能当防线。
- CI：用 AST 扫描 logger 调用，命中禁用标识符（`peer_id`、`api_key`、`token`、`prompt`、`content`、`messages`、绝对路径变量）先告警，稳定后阻断。
- 测试：脱敏函数补单测；审计路径与 `log_payloads` 路径各补一条失败用例。

### 4.11 安全评审清单

- [ ] 新增日志字段都在 4.2 白名单内，或已按 4.4–4.8 转换。
- [ ] 没有打印 `peer_id`，也没有打印会内嵌 peer 段的完整 URI。
- [ ] 没有打印密钥、凭证、token、Cookie、连接串。
- [ ] 没有打印 prompt、messages、正文、chunk、模型输出。
- [ ] 没有打印绝对路径；用户可控路径已转换。
- [ ] 没有用 `%r` / `%s` / `asdict()` / `model_dump()` 打印整个对象。
- [ ] `extra` 字段同样合规。

---

## 5. 兜底、重试与 4xx 拒绝（`INFO`）

`INFO` 的读者是运维和值班同学：他们需要知道「系统正在自己挣扎」或者「调用方被正常拒绝」，但不需要被叫醒。兜底类记录必须满足三个条件：**有状态迁移、有次数、有结果**。

### 5.1 重试与退避

```python
logger.info(
    "retry operation=%s attempt=%d/%d delay=%.2fs error=%s",
    operation_name,
    attempt + 1,
    retry_limit,
    delay,
    type(exc).__name__,
)
```

- 每次重试一条 `INFO`，**不要用 `WARNING`**。
- 重试耗尽后的最终失败再打一条 `ERROR`，且只打一条。
- 批量重试按批汇总，不要每条数据一条日志。
- 重试中的异常默认只放类型和稳定错误码；摘要与最终失败的堆栈仍须满足 4.8 的显式排障和安全处理条件。

### 5.2 降级与兜底

```python
logger.info(
    "vlm unavailable; using empty summary file_type=%s",
    file_type,
)
```

- 兜底分支必须记录**触发了哪个兜底**和**降级后的行为**，否则排障时无法区分「本来就是这样」和「悄悄降级了」。
- 兜底导致结果不完整时（如摘要为空、字段缺失），在消息里显式写出 `degraded=true` 一类的标记，让下游能筛。
- 不允许把兜底写成 `WARNING`，否则 `WARNING` 会重新变成「需要注意」垃圾桶。

### 5.3 熔断与状态迁移

`INFO` 记录状态机的每一次跃迁：`CLOSED → OPEN`、`OPEN → HALF_OPEN`、`HALF_OPEN → CLOSED`，以及主备切换与回切。仓库里 `openviking/utils/circuit_breaker.py` 已经是这个写法，可以当作范例。

### 5.4 启动、关闭、迁移

进程启动/关闭、一次性数据迁移、缓存预热、后台任务的启停，统一 `INFO`。单次请求的生命周期不要用 `INFO` 铺满。

### 5.5 4xx 业务拒绝

客户端参数错误、资源不存在、未授权等 4xx 结果固定记 `INFO`。理由有三：

- 它不是故障，所以不能是 `ERROR`，否则告警通道会被调用方误用淹没；
- 它反映真实的调用方问题（SDK 用法错误、权限配置缺失、URI 拼错），排障时有用，所以不能沉到 `DEBUG`；
- 它按请求产生，量级可控，不构成 `INFO` 噪声。

```python
logger.info(
    "request rejected status=%d error_code=%s route=%s",
    400,
    code,
    http_route,
)
```

约束：

- 一条请求一条拒绝日志，不要在 service 层和 router 层各记一次。
- 4xx 不产生 `ERROR`，也不写堆栈。
- 401/403 中属于安全事件的（认证失败、越权尝试）另外产生一条审计 `WARNING`，见 3.1；普通 4xx（参数错误、404）不进审计。
- 4xx 的响应日志与审计记录是两条不同用途的记录，允许并存，但不要用同一条日志冒充两者。

---

## 6. 业务过程日志（`DEBUG`）

默认出口。这里放的是**成功路径**上的业务过程；被拒绝的请求走 5.5 的 `INFO`。写作要求：

- 记录**决策依据**，不记录无信息量的进度。`"start to process"` / `"done"` 这类没有价值。
- 摘要化大对象：URI、数量、长度、哈希、耗时；不要整段文档、整段 prompt、整个响应体。
- 热路径先判断级别再拼装：

```python
if logger.isEnabledFor(logging.DEBUG):
    logger.debug("retrieval candidates=%s", summarize(candidates))
```

- 中间结果只在 DEBUG 打，且单条有长度上限（建议 ≤ 2 KB，超出截断并标注 `truncated=true`）。

---

## 7. 现状盘点

以下数据取自当前 `main`（统计范围 `openviking/`、`openviking_cli/`、`openviking_gateway/`，不含 `tests/`、`benchmark/`、`bot/`）。

### 7.1 级别分布

| 包 | DEBUG | INFO | WARNING | ERROR | EXCEPTION | CRITICAL |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `openviking/` | 266 | 385 | **471** | 247 | 88 | 4 |
| `openviking_cli/` | 5 | 6 | 17 | 2 | 0 | 0 |
| `openviking_gateway/` | 1 | 0 | 5 | 0 | 8 | 0 |
| 合计 | 272 | 391 | **493** | 249 | 96 | 4 |

`WARNING` 是使用最多的级别，比 `INFO` 多 26%，比 `DEBUG` 多 81%。这与「`WARNING` 仅限审计」的目标完全相反。

### 7.2 `WARNING` 的实际内容

493 条 `WARNING` 中：

- 含 `fail` / `error` / `unavailable` / `invalid` / `unable` / `exception` 等失败语义：约 190 条
- 含 `fallback` / `retry` / `degrade` / `skip` / `ignor` / `disabled` / `missing` 等兜底语义：约 41 条
- 含 `account` / `permission` / `role` / `acl` / `api_key` / `revoke` / `grant` 等审计语义：按关键词匹配约 **7 条**，且这几条都是失败路径（如「受信身份批量注册失败，将重试」），不是审计事件

也就是说，`WARNING` 里约 98% 的内容按新规范应该下沉到 `INFO` 或 `DEBUG`，而真正该在 `WARNING` 的审计事件基本不存在。

典型反例（按新规范应降级）：

| 位置 | 当前 | 应改为 |
| --- | --- | --- |
| `openviking/utils/model_retry.py:491` | 可重试错误后 `WARNING`，附退避时间 | `INFO` |
| `openviking/models/embedder/base.py:422` | 慢调用（≥3s）`WARNING` | `INFO` |
| `openviking/server/account_stores/redis_mysql.py:485` | 锁不可用、回源 MySQL `WARNING` | `INFO` |
| `openviking/server/account_stores/file.py:248` | 账号存储重载失败、将重试 `WARNING` | `INFO` |
| `openviking/storage/queuefs/semantic_processor.py:1357` | VLM 不可用、用空摘要 `WARNING` | `INFO` |
| `openviking/observability/http_observability_middleware.py:557` | 指标写入失败 `WARNING` | `INFO`（首次可 `ERROR`） |
| `openviking/server/request_id.py:77` | `X-Request-ID` 非法已重建 `WARNING` | `INFO` |

### 7.3 审计缺口

| 模块 | 日志调用数 | 说明 |
| --- | ---: | --- |
| `openviking/server/api_keys/key_mgr.py` | 0 | 账号创建、用户注册、密钥重生成、角色设置、删除全流程无日志 |
| `openviking/server/routers/acl.py` | 0 | ACL 查询/设置/删除/授予/撤销全部无日志 |
| `openviking/server/routers/admin.py` | 6 | `create_account`、`delete_account` 无审计；6 条全部是清理阶段的异常 |
| `openviking/server/routers/system.py` | 2 | 身份解析失败用 `WARNING`，属兜底 |

结论：不是「`WARNING` 用错了地方」这么简单，而是**审计能力本身缺失**。这是优先级最高的一项。

### 7.4 分模块改造前的实现基线

以下清单保留 PR #7769 和日志基础批次之前的基线。基础批次已将默认级别改为 `INFO`，将 tracer 迁到 stdlib logging，统一 Python/root 与 Rust/C++ 输出，并关闭独立服务器重复的 Uvicorn 访问日志；bootstrap 状态提示保留直接控制台打印。安全操作审计能力记为独立功能待办，剩余现有日志调用点整改继续按后续批次推进。

1. **默认级别压制了 `INFO`**：`LogConfig.level` 默认 `WARNING`（`openviking_cli/utils/config/log_config.py:12`），而 `_load_log_config()` 的兜底值是 `INFO`（`openviking_cli/utils/logger.py:684`）。按新规范把兜底/重试放进 `INFO` 后，默认部署将完全看不到这些信号。
2. **惰性格式化违规**：启用 ruff 的 `G` 规则可扫出 480 处，其中 `G004`（f-string 日志）449 处、`G201`（`.error(..., exc_info=True)` 应为 `.exception()`）27 处、`G001` 4 处。当前 `pyproject.toml` 只启用了 `E/W/F/I/C/B`，这些问题不会被 CI 拦住。
3. **两套日志栈**：主体是 stdlib `logging` + `get_logger()`，但 `openviking/telemetry/tracer.py` 仍用 `loguru`，其独立输出未纳入统一 formatter。
4. **多运行时各配一套**：Rust binding 通过 `openviking/utils/agfs_utils.py` 接收 `log.level` 和 `log.output`，C++ 引擎通过 `openviking/storage/vectordb/utils/logging_init.py` 转换 Python 格式；共享配置源并未统一最终输出。Rust 仍使用独立的 tracing formatter，且 `CRITICAL` 配置映射为 `ERROR`，格式与级别映射缺少统一校验。
5. **访问日志重复**：`openviking.observability.http` 对每个请求打一条完成日志（`openviking/server/request_id.py:86`），`uvicorn.access` 也会打一条；当 `log.level=INFO` 时同一请求出现两条。
6. **`print()` 残留**：库代码中 334 处 `print(`，主要集中在 `openviking/server/bootstrap.py` 的启动路径，绕过级别控制与脱敏。
7. **只有纯文本格式**：`_build_standard_handler()` 只挂了 `logging.Formatter(format_string)`，没有 JSON 选项；`request_id` 是拼进消息体的（`openviking_cli/utils/logger.py:489`），采集侧无法按字段切分。
8. **循环内无收敛**：`local_collection.py`（18 条 `WARNING`）、`semantic_processor.py`（11 条）等在逐条处理场景中直接打日志，批量失败会刷屏。
9. **同一输出流有多种格式**：Python 默认时间没有时区、以逗号分隔毫秒；Rust 输出 UTC、微秒和 `WARN`；loguru 使用另一套分隔符；启动 `print()` 没有日志头。把这些输出交给 systemd 或容器采集并不会自动统一应用日志格式。
10. **第三方接管 root logger**：当前 MCP 依赖的 `FastMCP` 构造过程调用 `logging.basicConfig()`，root 尚无 handler 且 Rich 可用时会安装 `RichHandler`。MCP、APScheduler 等传播到 root 的记录因此使用短日期、分栏、自动折行与重复时间省略。`_MANAGED_LOGGER_ROOTS` 当前只覆盖 `openviking`、`openviking_cli`、`uvicorn`，未覆盖这一出口。

---

## 8. 补充规范（原原则之外需要补齐的部分）

原原则只定义了「级别 → 用途」的映射，落地还需要下面这些约定。

### 8.1 一条日志只记一次

由**决定最终处理结果的边界**记录一次：HTTP 响应边界、后台任务终态或启动入口负责判断结果与级别；中间层只传播或包装异常，不提前记录最终失败。捕获并完成降级的层记录 `INFO`；一次重试记录的是尝试与退避，不是最终 `ERROR`。若中间层返回错误对象，仍由消费该对象并决定最终结果的边界记录。

```python
# 反例：中间层提前记录，边界处理时又记录一次
except Exception as exc:
    logger.exception("compile tag validation failed")
    raise OpenVikingError(...) from exc

# 正例：底层纯抛出，边界层统一记录
except (ConfigPatchError, ValueError) as exc:
    raise InvalidArgumentError(str(exc)) from exc
```

边界按第 1、5 节记录 4xx 拒绝、兜底或最终失败；即使框架要求边界继续抛出，也要明确后续记录者不再重复记录同一失败。异常字段与堆栈仍受 4.8 约束。

### 8.2 惰性格式化

Python stdlib 日志使用 `%s` 等占位符，禁止 f-string 和 `.format()`：

```python
# 反例
logger.warning(f"Failed to resolve identity: {e}")
# 正例
logger.info("identity resolution failed error_type=%s", type(e).__name__)
```

占位符避免级别关闭时的字符串格式化开销，也保留原始消息模板供聚合使用。惰性格式化不等于脱敏，禁止值无论用 f-string 还是 `%s` 都不能输出；非 Python 运行时使用各自的惰性或结构化日志接口。

### 8.3 结构化字段优先

需要被检索、聚合、告警的维度放 `extra`，不要只拼进消息：

```python
logger.info(
    "HTTP request completed method=%s route=%s status=%d duration_ms=%.1f",
    method, route, status_code, duration_ms,
    extra={"http_method": method, "http_route": route,
           "http_status_code": status_code, "duration_ms": duration_ms},
)
```

约定字段名：`request_id`、`trace_id`、`account_id`、`user_id`、`operation`、`http_route`、`duration_ms`、`result`、`degraded`；审计字段使用 3.2 的完整 schema，不另设 `actor`、`account`、`route` 等别名。

### 8.4 敏感信息

字段级规则见第 4 节，那里是唯一权威口径。`mask_api_key()` 仅用于 4.5 允许的显式诊断展示；`sanitize_public_http_error()` 与 `sanitize_error()` 的局限见 4.8，不能把调用这些函数视为可输出任意异常文本的依据。日志、trace 和审计落盘均按同一字段规则处理。

### 8.5 噪声控制

- 循环/批量场景：一条汇总 + 明细 `DEBUG`，汇总里带 `total` / `failed` / `skipped`。
- 同一最终失败只保留决策边界的一条 `ERROR`，重复诊断细节按需放 `DEBUG`；不能因为去重就把审计 `WARNING` 或兜底 `INFO` 升为 `ERROR`。
- 第三方库的阈值在 server 日志初始化入口统一配置，不要靠业务调用方自己 `setLevel`；不得为降低噪声批量改写其级别语义，见 8.10.6。
- 高频路径（每请求、每 chunk、每候选）默认 `DEBUG`，且先 `isEnabledFor`。

### 8.6 单条日志体积

单条消息建议 ≤ 2 KB，结构化字段值 ≤ 1 KB。超长时截断并标注 `truncated=true`、`original_length=<n>`。禁止直接 `%r` 一个大对象或整个异常上下文。

### 8.7 文案风格

- 统一英文，便于跨团队 grep 与英文文档对应。
- 小写开头，不加句号，不写「Oops」「Something went wrong」这类无信息量措辞。
- 来源统一由日志头的 `logger` 表达，不再要求消息手写 `[模块名]`；存量前缀可以逐步清理，稳定事件名或操作名仍可保留。
- 同一条消息的模板保持稳定，变量只放在占位符位置，否则聚合时会产生大量离散值。

### 8.8 不记录的东西

- 不做「函数进入/退出」式埋点（用 tracing 或 profiler）。
- 不为日志而日志：一条日志要能回答「发生了什么、影响谁、下一步看什么」中的至少一个。
- 不在日志里输出业务逻辑判断结果作为唯一证据；关键分支要落到指标或事件总线。

### 8.9 测试

- 用 `caplog` 断言**级别与结构化字段**，不要断言完整文案。
- 审计路径必须有针对性测试，至少覆盖成功与失败两种结果。
- 不为「日志是否被调用」写 mock 断言。
- 格式与接入测试必须检查最终输出流，覆盖第三方和原生组件；`caplog` 不能代替 8.10.7 的验收。

### 8.10 统一输出格式与初始化

Bootstrap 的启动进度、依赖检查、启动结果及失败提示使用直接 `print(..., flush=True)`，确保不受 `log.level` 和文件日志输出配置影响。错误写 stderr，其余状态写 stdout。控制台消息仍遵守第 4 节内容边界，并与格式化运行日志分开验收。

以下是 server 日志的目标规范，覆盖 Python、Rust、C++、TypeScript、第三方依赖和并入 server 日志的子进程输出。独立运行的 CLI/SDK 的宿主边界见 8.10.4–8.10.5。格式转换必须在初始化与适配层完成，不能让业务代码手工拼日志头。

#### 8.10.1 时间

- 统一 UTC、ISO 8601，固定格式 `YYYY-MM-DDTHH:mm:ss.SSSZ`，例如 `2026-11-13T22:56:01.028Z`。先转换到 UTC，再输出 `Z`；禁止直接给本地时间加 `Z`。
- 使用事件产生时间，进入异步队列前捕获；不要以写盘或采集时间替代。原生高精度时间在输出层统一到毫秒。
- 启动、运行、关闭阶段均适用；不受主机时区、locale 或终端宽度影响。不混用逗号毫秒、短日期、无时区时间，连续事件也不能省略时间。

#### 8.10.2 级别名称与映射

最终输出只使用大写 `DEBUG`、`INFO`、`WARNING`、`ERROR`、`CRITICAL`。**配置阈值映射与输出名称归一化是两件事，必须分别验证**：接受 `WARNING` 配置不代表输出已经不是 `WARN`。

| 原生级别或别名 | 输出名称 | 约束 |
| --- | --- | --- |
| `debug` / `DEBUG` | `DEBUG` | 统一大小写 |
| `info` / `INFO` | `INFO` | 统一大小写 |
| `warn` / `WARN` / `warning` / `WARNING` | `WARNING` | 不保留 `WARN` 缩写 |
| `error` / `ERROR` | `ERROR` | 不改变事件语义 |
| `fatal` / `FATAL` / `critical` / `CRITICAL` | `CRITICAL` | 仅映射原本的致命级别，不能把普通错误升级 |
| `trace` / `TRACE` | `DEBUG` | 原生 TRACE 仅在显式启用时归一化，不因这张表默认开启 |

- 配置层统一校验、归一化后再传给各运行时；支持的配置别名必须明确记录，未知值报配置错误，不允许各组件自行回退到不同级别。
- Rust tracing 没有原生 `CRITICAL`；当前把 `CRITICAL` 阈值映射到 `ERROR` 是有损兼容，无法只筛选致命事件，必须记录这一限制。进程不可继续的事件应由能表达 `CRITICAL` 的边界层记录；不能把所有 Rust `ERROR` 重命名为 `CRITICAL`。
- 输出名称归一化不重分类第三方事件，见 8.10.6。

#### 8.10.3 日志头与字段

文本输出的固定顺序为：`ts level logger pid request_id trace_id message`，其余结构化字段追加在后。例如：

```text
2026-11-13T22:56:01.028Z INFO openviking.server.bootstrap pid=452394 request_id=- trace_id=- message="server started"
```

- `logger` 使用完整 Python logger 名、Rust target 或稳定的组件名；`pid` 是产生事件的进程。来源不能只藏在消息前缀中。
- 可空字段在文本中写 `-`，结构化事件和未来 JSON 输出使用 `null`；`request_id`、`trace_id` 不可用时也遵守这一规则。后台和启动日志不能编造请求标识。
- `message` 及追加字段中的字符串值按 JSON 字符串规则引用和转义；数字、布尔值保持类型。换行、回车和控制字符不能破坏记录边界，所有值仍须先经过第 4 节的安全处理。
- 关联字段只由统一适配层注入，不再向消息正文重复拼接。代码文件与行号如需输出，按 4.2 显式登记为可选字段，路径仍受 4.7 约束。
- 当前适配器只接受公共格式，并在加载时归一旧内建格式；其他自定义格式校验失败。未来支持的自定义 `log.format` 必须保留必需字段及时间、级别约定；不支持的跨运行时转换应明确报错，不能静默退回组件默认值。未来 JSON formatter 使用相同字段和语义。

#### 8.10.4 初始化与输出归属

- server 入口拥有日志配置，在加载会配置日志的依赖、创建组件之前安装最小统一输出；读取配置后沿用同一格式契约。bootstrap 配置加载失败直接通过 `print(..., flush=True)` 写 stderr。
- Python root 与受管 logger 统一接入，至少覆盖 Uvicorn、MCP、APScheduler。明确 handler 与 `propagate` 归属，一条事件在每个目标出口只出现一次；审计双写与 OTel 导出是不同出口。
- 禁止业务模块另装 `basicConfig`、RichHandler 或独立 formatter。对依赖的初始化副作用由集成层处理，不能任由后加载的组件改变输出格式或阈值。
- 自有 loguru 调用迁到 `get_logger()`；暂时保留时，只能经单向桥接进入统一出口，并移除重复 sink。Rust/C++ 在原生适配层实现同一契约，不能仅替换 `%l` / `%(levelname)s` 就宣称格式一致。
- 多 worker 的每个进程分别初始化；重复初始化与配置重载不得累积 handler、重复输出或丢失关联字段。不能热更新的原生配置要明确要求重启，不能只更新 Python 后声称全局生效。
- 被管理的子进程保留自身 PID 和组件名。并入同一日志流时须遵守同一契约；尚未适配的输出保留在明确标识的独立日志文件，不冒充父进程事件。
- 作为 SDK 嵌入其他应用时，不能擅自清空或重配宿主 root logger；全局接管仅属于独立 server 入口，嵌入场景由宿主显式选择接入方式。

#### 8.10.5 非交互输出与 `print()` 边界

- server 的 stdout/stderr、文件、systemd 和容器采集输出统一 UTF-8，不使用 ANSI 颜色、Rich 分栏、自动折行、进度条或重复时间省略。一条事件占一条物理行；查看器自行换行不属于应用输出。
- 允许输出的异常堆栈先按 4.8 脱敏，再把换行转义到同一记录中；不得产生无法归属事件的裸续行。JSON 模式同样使用一行一个对象。
- Bootstrap 的启动进度、依赖检查、启动结果及其管理的进程提示保留直接 `print(..., flush=True)`；其他运行日志经过统一 logger。控制台消息仍须保证内容安全，不打印原始凭证、异常全文或子进程正文。
- 交互式 CLI 的帮助、命令结果、进度展示可使用专用终端渲染；这不属于 server 运行日志。机器可读结果和 stdio 协议占用 stdout 时，诊断日志必须写 stderr，不能污染协议流。

#### 8.10.6 第三方语义与审计隔离

- 第 1–3 节的级别用途约束适用于自有代码，不能要求第三方依赖把 `WARNING` 改成审计专用；例如 APScheduler 的任务错过执行时间告警仍是运行告警。
- 第三方日志保留原有级别语义与 logger 名称，只统一时间、名称拼写、字段和输出方式；不能为了满足审计约定，把所有第三方 `WARNING` 批量降到 `INFO`。
- 噪声通过集中配置的 logger 阈值和明确的定向规则控制；任何会隐藏第三方故障的规则都应有针对性验证。不得通过关闭第三方日志来通过格式验收。
- 审计日志以专用 logger 和明确的审计事件字段识别，遵守 3.2–3.3；第三方 `WARNING` 不进入审计，也不能替代必须由自有代码产生的审计记录。

#### 8.10.7 验收

对日志初始化与适配层的改动，捕获实际 stdout/stderr 或日志文件，至少验证：

- 在 UTC 与 Asia/Shanghai 环境下，固定事件时间都输出同一 UTC 时间戳，精度和格式一致。
- 正常请求、异常、关闭及 Python、MCP/APScheduler、Rust/C++ 的格式化运行日志满足必填字段和单行约定。bootstrap 启动/配置失败提示单独验收：高日志阈值和文件输出下仍直接显示在 stdout/stderr。
- 非 TTY 重定向下无 ANSI、Rich 分栏、隐去的时间或裸续行；各组件遵守 `INFO`、`WARNING` 等阈值，原生级别映射的限制有独立用例。
- 重复初始化、配置重载和多 worker 下，同一事件在每个目标出口恰好一次，PID 正确，请求关联字段不串到其他请求。
- 一条第三方 `WARNING` 保留为运行告警，一条 `openviking.audit` 事件进入审计，两者不混淆。

断言字段、格式和记录数量，不锁定完整业务文案；原生组件与子进程需要实际出口验证，不能只断言 Python logger 被调用。

---

## 9. 落地路线

每批合入后再开始下一批，开始前同步上游，减少文件重叠和冲突。

| 批次 | 范围 | 状态 |
| --- | --- | --- |
| 1 | 模型日志及安全 payload 摘要 | 已合入 #7769 |
| 2 | 日志初始化、观测日志、Python/Rust/C++ 输出 | 已实现；不新增审计能力 |
| 3 | 认证、账号、ACL、ROOT 相关现有日志 | 第二批合入后 |
| 4 | 业务服务现有日志 | 第三批合入后 |
| 5 | 解析、导入、连接器 | 第四批合入后 |
| 6 | 存储与队列 | 第五批合入后 |
| 7 | 检索、核心与工具 | 第六批合入后 |
| 8 | 会话、记忆、编译、CLI、Gateway | 第七批合入后 |

以下优先级清单保留原始规范待办。现有日志整改按上述模块批次落实；新增审计能力不纳入本轮改造。

| 优先级 | 事项 | 影响面 | 说明 |
| --- | --- | --- | --- |
| P0 | 补齐审计事件（账号、成员、角色、ACL、API Key、配置、删除） | 新增代码，不改现有日志 | 对应 7.3 的缺口，收益最大、风险最低 |
| P0 | 把 `log_payloads` 的完整 payload 输出从 `WARNING` 降为 `DEBUG` 并强制脱敏 | 2 处（`jev_rerank.py`、`rule_jev.py`） | 当前开关一开就把 query 与正文写进 WARNING，违反第 4.6 节 |
| P0 | ROOT 调用审计：`_build_request_context` 打标 + 完成阶段输出一条 `WARNING` | 1 个收口点 | 仅 `api_key` 模式；dev/trusted 与进程内构造天然不计，见 3.5 |
| P0 | 明确默认日志级别 | 1 个字段 + 文档 | 若坚持 `WARNING` 默认值，必须在文档中说明「兜底/重试默认不可见」 |
| P0 | 引入 `openviking.audit` logger 与 `log_audit()` 帮助函数 | 审计接入的前置能力 | 统一 schema、字段转换、事件投影与双写失败反馈，验证实际持久化后再接入业务事件 |
| P1 | 启用 ruff `G` 规则 | CI | 先 `per-file-ignores` 圈定存量，再逐步清零 480 处 |
| P1 | 新增 `log_safety.py`（`short_hash`、`safe_uri_for_log`、`safe_path_for_log`、`safe_log_fields`） | 新增模块 | 给 4.4–4.8 的转换提供唯一入口 |
| P1 | 统一 server 日志初始化与最终输出 | Python/root、Uvicorn、MCP/APScheduler、tracer、Rust/C++ | 落实 8.10；包含 bootstrap 控制台提示可见性、tracer loguru 迁移/桥接和实际输出验收，不等待全仓整改 |
| P2 | 按 7.2 清单把 `WARNING` 下沉到 `INFO`/`DEBUG` | ~190 处 | 建议按模块分批，每批附一条回归测试 |
| P2 | 循环内日志收敛 | ~30 个文件 | 汇总 + 明细 |
| P2 | 整改路径类日志（34 处）与 `peer_id` 间接泄露 | ~34 处 | 改为相对路径 + 短哈希 |
| P3 | 访问日志去重 | 2 处 | 明确由 `uvicorn.access` 还是 `observability.http` 承担 |
| P3 | 清理其余运行日志的 `print()` | 全仓剩余调用点 | bootstrap 控制台提示保留；交互式 CLI 展示与协议输出按 8.10.5 区分，不机械替换 |
| P4 | JSON 格式化输出选项 | 1 个 handler | 便于采集侧按字段切分 |

### PR 评审清单

提交前自查：

- [ ] 新增的每条日志都能对上第 1 节的级别语义，没有把兜底/重试写成 `WARNING`。
- [ ] 安全敏感动作走 `log_audit()`，成功与失败都记录，字段齐全。
- [ ] 没有 f-string 或 `.format()` 日志。
- [ ] 最终失败由决策边界记录一次；中间层没有提前记录或重复堆栈，兜底与重试按 `INFO` 处理。
- [ ] 审计字段已按 4.2 分类，示例与调用使用 3.2 的完整 schema；没有把任意异常文本交给现有脱敏函数后直接输出。
- [ ] 没有把密钥、Token、正文、完整 payload 写进日志。
- [ ] 没有打印 `peer_id`、内嵌 peer 段的完整 URI、绝对路径（第 4.4、4.7 节）。
- [ ] ROOT 可达的接口已确认会走 3.5 的审计，而不是只打 `INFO`。
- [ ] 安全字段自查已过一遍第 4.11 节清单。
- [ ] 循环/批量路径有汇总，没有逐条刷屏。
- [ ] 新增日志文案是英文、模板稳定，来源由日志头的 logger 表达。
- [ ] server 输出满足 8.10 的 UTC 毫秒时间、统一级别名称、必填字段和单行约定。
- [ ] 没有新增独立 handler、Rich 输出或运行日志 `print()`；初始化与重载不会重复输出。
- [ ] 第三方日志保留语义，审计按专用 logger 和事件字段路由，不按 `WARNING` 一刀切。
- [ ] 日志初始化或跨运行时适配有实际输出验收，覆盖 8.10.7。

---

## 10. 快速参考

以下级别用途针对自有代码；第三方语义与统一输出契约见 8.10。

```text
审计（权限/账号/密钥/配置/删除）      → WARNING  openviking.audit
需要人介入的失败                     → ERROR
重试、退避、降级、熔断、主备切换      → INFO
4xx 业务拒绝（参数/未找到/未授权）    → INFO
api_key 模式下 ROOT 的任何调用       → WARNING  openviking.audit
业务过程、中间结果、成功请求细节      → DEBUG
进程无法继续                         → CRITICAL
```

```text
可打印：account_id、user_id、request_id、trace_id、task_*、耗时、计数、错误码
需转换：本地路径 → 相对路径；URI → 去掉 peer 段；异常 → 类型 + 稳定错误码 + 允许字段
禁打印：peer_id、密钥凭证、上下文内容、绝对路径、完整 payload
```

```python
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

logger.debug("retrieval candidates=%d level=%s", len(candidates), level)   # 业务过程
logger.info("retry operation=embed attempt=%d/%d delay=%.2fs", n, limit, d) # 兜底
logger.error("ingest task failed after retries task_id=%s error_code=%s", task_id, error_code)
```
