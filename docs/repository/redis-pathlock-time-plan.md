# Redis PathLock 计时修复分阶段方案

状态：阶段 1 已实现并通过本地验证；阶段 2/3 尚未实现。分为两个交付阶段，另有可选的阶段 3；范围是 Redis PathLock，保留其他 provider 的现有计时协议。

## 1. 问题与修复目标

修复前，`crates/ragfs/src/lock/cache_provder.rs` 接收客户端计算的 `now_ns`、`stale_before_ns` 和 HASH TTL。`manager.rs` 使用 `SystemTime` 计算前两项。阶段 1 的 Redis 实现已忽略客户端时间参数，脚本只接收租期。

这带来两个独立问题：

- A 刚获取 30 秒 Tree 锁，B 的时钟快 31 秒，B 就能把 A 的新锁判为过期并删除；慢时钟则可能让过期锁长期阻塞。
- 同一 `service_name` 下的实例若配置不同的 `lock_expire_secs`，会按不同阈值抢锁，还会通过 acquire、refresh、release 等操作覆盖整个 scope HASH 的 TTL。即使两实例访问的是不相交路径，也可能提前删除另一实例仍有效的锁。

原设计还把租期规范化、服务配置闸门、重入/回滚确认语义、adopt 原子校验、本地错误传播与停写迁移绑定在一起。这些工作不必作为一次完整协议升级交付。

| 阶段 | 交付目标 | 升级方式 |
| --- | --- | --- |
| 1：立即 | Lua 使用 Redis TIME；服务器生成 token 时间和过期阈值；Lua 计算固定 `2 × lease` TTL | 保留编码与锁空间，允许滚动升级，接受短暂混跑的既有风险 |
| 2：后续 | 配置 HASH、租期一致性、初始化及配置错误传播 | 协调停写迁移 |
| 3：可选 | adopt 原子校验与续租，结合 handoff claim 设计 | 单独评审认领协议与迁移要求 |

阶段 1 只修时钟源，不承诺解决租期不一致、adopt 跨进程原子性、过期 token 复活或存储侧 fencing。“解决 80% 问题”是交付优先级判断，不是已验证的故障覆盖率。

### 阶段 1 的范围与不变量

- 所有需要当前时间的 Redis 脚本每次只读一次 `TIME`，不再使用客户端 `now_ns`。
- `stale_before_ns` 由 Lua 根据服务器时间与本次传入的租期计算。
- 新建或更新确认时间的 token 由服务器写入 `time_ns`；客户端传入的时间不能作为新确认时间写入。
- HASH TTL 由 Lua 固定计算为 `2 × lease`，不再接受独立的客户端 TTL。
- 保留 `owner:time_ns:E/T` 编码、现有 key 与单 key 执行方式，不加配置 HASH，不改 `execute()` 签名。
- 保留现有冲突、重入、回滚、过期比较边界和续租成功条件；不在本阶段新增确认语义或过期续租限制。
- 保留本地 `Instant` 安全窗口与不可逆失效机制。跨 manager adopt 的本地年龄检查暂时保留，属于明确的剩余问题。

## 2. 阶段 2：服务级不可自动变更的配置记录

本节不属于阶段 1。阶段 1 稳定后，新增同 slot 的持久配置 HASH：

```text
{<service_name>}:ov:pathlock:config
  protocol_version = 2
  clock_source     = redis_time
  lease_ms         = <规范化整数>
  gc_ttl_ms        = 2 * lease_ms
  state           = ready
```

它与现有 `{<service_name>}:ov:pathlock:scope:…:tokens` 共享 Cluster hash tag。业务脚本显式传入两者作为 `KEYS`，不能通过未声明的动态 key 访问配置。

- 把 `lock_expire_secs` 在 Rust 边界统一转换为 `ceil(secs * 1000)` 毫秒；要求有限、至少 1000ms，并设定产品允许的明确上限。Lua 只接收并比较有界整数配置，不接收客户端当前时间或过期阈值。
- 管理器的续租间隔、`0.8 * lease` 安全窗口和清理窗口全部基于同一规范化租期。避免 provider 与 manager 分别舍入，也避免可独立构造的两者使用不同租期。
- 增加 provider 租期能力/初始化校验；manager 激活前确认配置一致。Redis 校验是异步操作，不能只依赖目前同步的 `RedisPathLockProvider::new()`。接入现有异步初始化链路，并在失败时阻止锁服务进入可用状态。
- 初始化检查用于快速报错；每次业务脚本仍校验记录的版本、状态及租期，不能信任进程内缓存。
- 配置不存在、损坏、非 ready 或不匹配时返回明确的错误，例如 `ProtocolMismatch`、`LeaseConfigMismatch`、`LockNamespaceNotReady`。不能自动降级到客户端计时。
- 配置记录不设置 TTL。任何正常业务脚本都不创建、重建或改写它。即使 scope HASH 都自然消失，也不能由下一位客户端重新选择租期。
- 新服务空间通过显式初始化操作创建记录。部署方必须保证其为新空间或已完成下述停写迁移；“配置不存在”本身不能证明没有旧 token。

同一服务内的错误配置实例会在访问任何 scope 时失败。不同服务可以使用不同租期，但访问同一数据集的参与者必须约定同一锁空间，不能通过换 service_name 绕过冲突。

## 3. 阶段 1：Redis TIME 与 token 编码

保留 `owner:time_ns:E/T` 的 Rust token 表示和 wire 格式。新版本写入的确认时间来自 Redis；旧 token 仍是可读取、可 CAS 比较的纳秒 epoch。

provider 将本实例的 `lock_expire_secs` 转为有界整数 `lease_ms`，通过现有 `args` 传入；阶段 1 不校验不同实例的租期一致性，也不要求 manager/provider 初始化协议改造。每次需要计时的脚本只读取一次 `TIME`，同一批次共享该时间：

```text
TIME -> [seconds, microseconds]
server_now_ns = decimal(seconds) || microseconds左补齐6位 || "000"
stale_before_ns = server_now_ns - lease_ms * 1_000_000
gc_ttl_ms = 2 * lease_ms
```

实现要求：

- 使用精确十进制字符串减法和现有字符串比较，处理减法下溢并校验输入。不要将纳秒 epoch 转成 Lua double。
- `seconds` / `microseconds` 来自 Redis 返回的字符串；不得经科学计数法转换后拼接。租期及 TTL 的整数运算必须有范围限制，保证处于 Lua 精确整数范围内。
- 实现采用 `ceil(secs * 1000)`，验证租期有限且至少 1 秒，并限制 `lease_ms <= 2^52 - 1`，使 Lua 中租期与两倍 TTL 都能精确表示；规范化后最多增加不足 1ms。跨实例租期一致性及统一初始化仍留给阶段 2。
- 新 token 精度为微秒，保留纳秒字段兼容旧表示；比较旧 token 时不能截断其纳秒精度。
- 保留当前脚本的过期边界：`token.time_ns < stale_before_ns` 才视为过期。本次不改为 `<=`，也不顺带统一 adopt 的 `0.8 * lease` 门槛。
- 旧客户端快时钟可能留下大于 Redis 当前时间的 token。本阶段保留现有保守占锁行为，不新增“未来 token 一律拒绝续租”的协议；不能据此删除 token。
- 同一 epoch 编码允许新旧 token 混用。新实例成功更新确认时间后，该 token 转为服务器时间；不需要停写清空旧 token。

不要把 `LockToken.time_ns` 当作可跨 provider 比较的全局时间。为保持公共 provider trait 兼容，Redis 实现可以继续接收但忽略时间参数；generic provider 仍使用原参数。现有 adopt 本地时间判断暂不替换，详见阶段 3。

## 4. 阶段 1：全部 Lua 入口的具体变化

公共逻辑复用服务器时间、精确十进制减法与 TTL 计算。Redis Lua 脚本发生错误不会自动回滚已执行命令，因此参数与时间运算校验必须放在写入之前。没有时间判断或时间写入的只读/CAS 删除脚本无需为了统一形式额外调用 `TIME`。

| 入口 | 新行为 |
| --- | --- |
| acquire batch | 用 Redis 时间计算阈值，判断 target、Tree 祖先和相关后代是否过期。新建、Exact→Tree 等既有时间写入使用服务器时间；保留重入不更新时间等现有行为。 |
| refresh / refresh batch | 保留现有 owner 校验及逐路径成功/失败语义，成功路径写入服务器时间并保留 kind。本阶段不新增“过期即拒绝续租”的条件。 |
| is_locked | 由服务器判断年龄；保留 `ignore_stale` 语义。调用者无需传 `stale_before_ns`。 |
| create | 不接收客户端 token 时间，按 owner/kind 由服务器构造 token。保留内部 create 语义及既有 scope 限制；业务获取仍必须走完整冲突检查。 |
| CAS write / kind mutation | 完整 raw token 比较保持不变。仅改变 kind 的操作保留现存时间；需要写入新确认时间的操作由 Redis 生成，不能直接写入 replacement 的客户端时间。 |
| compare remove / release | 保留完整 token CAS，不生成确认时间；若重设 TTL，由 Lua 使用本次租期计算固定值。 |
| rollback batch | 保留现有严格 CAS 与恢复 previous token 的语义；恢复已保存的历史时间不视为新确认，不使用客户端当前时间。 |
| read many / scan descendants | 保持只读返回原始 token，不加配置检查；原始时间仍用于诊断/CAS。 |

修改过参数或返回语义的脚本 ID 升版，旧脚本保留以支持短暂混跑。审计所有入口，包括 CRUD、批量续租、回滚，确保没有遗漏客户端新确认时间或独立 TTL 注入路径。读取、删除等未改变的脚本不要求同步升版。

阶段 1 实现将 acquire 升为 v3，rollback、compare write/remove、refresh/batch、
is_locked、create 升为 v2；read/scan 保持 v1。新实例注册新脚本 ID，
旧实例继续注册和使用旧 ID，两者操作同一锁 key；无需新实例额外注册旧脚本。

### 返回值与 CAS 配套调整

服务器生成时间后，Rust 不能再把客户端构造的 token 当作 Redis 实际写入值：

- acquire/upgrade 返回实际写入的 replacement，供后续严格 CAS 与回滚使用。
- 审计 create/CAS 的调用方；凡依赖写入后 raw token 的路径，必须使用服务器实际值。需要时调整内部脚本返回解析，不引入配置 key，也不改 `execute()` 签名。
- 保留现有 rollback 恢复 previous 的行为，允许恢复脚本返回并保存的历史 token；不借此开放任意客户端时间写入。若现有通用 CAS 同时承载历史恢复和新确认，需要区分这两种意图。
- 已被续租或接管改变的 replacement 仍导致回滚 CAS 失败，不覆盖新值。

实现审计结果：Redis manager 的获取走 atomic acquire，回滚走 atomic rollback；
两者已经携带服务器实际 token。create 和通用 CAS 的现有 Redis 调用方不依赖
客户端 replacement 作为后续 CAS 快照，因此保留 `()`/`bool` 返回类型，
不修改 manager、trait 或 `execute()`。通用 CAS 在同 owner 且时间字段未变时
保留现存时间，否则生成新的服务器确认时间；历史恢复仅走 rollback。

重入确认窗口、过期 token 不可复活及回滚保留新确认时间等语义改造从本次范围移出，另行评审；阶段 2/3 也不自动包含这些改造。

## 5. 阶段 3（可选）：adopt 原子校验与 handoff claim

当前跨 manager adopt 先 read，再用本地 `now_ns` 检查年龄，随后只按 owner refresh。阶段 1 仅使其中 Redis refresh 写入服务器时间，保留这条调用链及其原子性限制；因此不能宣称阶段 1 已消除所有客户端时钟依赖。

阶段 3 再考虑新增 provider capability，例如 `validate_and_refresh_for_adopt(paths, owner, expected_kinds)`：

1. 若阶段 2 已实施，在 Lua 内校验配置。
2. 使用同一次 Redis TIME，完整校验批次每个 token 的存在、owner、kind。
3. 保留当前较严格的 adopt 门槛：年龄必须小于 `0.8 * lease`。
4. 所有 token 验证通过后才一起续期并返回成功；不能先续一半再发现后半批不合法。
5. manager 以请求开始时的 `Instant` 记录确认时间，确认响应到达时尚未越过本地安全窗口后才发布 lease。

正常 Redis 获取的 lease 已限于一个 scope；adopt 请求也验证这个约束。大批次采用明确上限，超过上限拒绝，不能用截断验证构造完整授权。

该阶段实施后，Redis 使用服务器原子校验；其他 provider 保留现有 fallback。

原子 validate/refresh 本身不能阻止同一 handoff 被多个进程重复认领。两者属于同一类跨进程原子性问题，可一起设计，但必须为 handoff 增加一次性 claim/消费代次，并明确重试、响应丢失和重放语义。最终接口与协议单独评审，不作为阶段 1/2 的交付依赖。

## 6. 本地授权窗口与阶段 2 错误传播

阶段 1 保留当前 `Instant` 安全窗口、请求耗时计入方式、明确失锁/不确定错误区分与不可逆失效设计，不重构共享 token health 或 acquisition 的确认语义。现有迟到响应、网络抖动与失效清理测试继续通过。

阶段 2 再增加以下约束：

- manager/provider 基于同一已验证的规范化租期计算续租和授权窗口。
- 配置/协议不匹配是确定的服务不可用状态，不能当作普通网络抖动长期继续授权。已有 lease 应撤销授权并报告原因；在修正配置前停止新获取。清理权限仍独立保留，但不绕过远端配置校验。
- 远端配置正常业务期间不可修改。要变更租期必须停写、清空在途授权后重新初始化，不能期待错误传播替代这一操作流程。

届时为全局配置错误保留结构化错误类型；目前 refresh batch 有将错误转换为 `Io` 字符串的路径，需要调整以便 manager 区分错误。

## 7. 阶段 1：HASH TTL 只做延迟回收

所有需要设置 TTL 的脚本由 Lua 计算 `gc_ttl_ms = 2 * lease_ms`，不再接受客户端单独传入 TTL。阶段 1 的 `lease_ms` 来自调用实例，阶段 2 才改为服务配置记录的统一值：

- 新建/成功续期后的 HASH 设置为本次请求租期的两倍，按 Redis 到期机制回收。
- 全部参与者使用相同租期且确认时间由服务器生成后，最后一次新建/续租不早于 HASH 中其他 token 的最后确认时间；固定两倍租期不会早于有效 token 的过期时间删除 HASH。混跑时旧 token 可能来自未来时间，这一论证不覆盖它。
- release、rollback、kind mutation 不生成新确认时间；若重设 TTL，也只能使用 Lua 计算的固定值。
- 不同实例租期不一致时，短租期实例仍可能缩短其他 token 所在 HASH 的 TTL。Lua 计算不能替代阶段 2 的一致性校验。
- HASH 为空时由 Redis 自然删除；阶段 1 没有配置记录。
- 不用 HASH 存在或 TTL 尚余多少来决定某个 token 是否有效。

不需要在本次修复新增全 scope 的最大过期时间扫描。它不能解决 Redis 时间跳变，且会引入性能成本。路径/过期索引优化另行实施；若已有索引，必须与 token 时间更新在同一脚本原子维护。

## 8. 分阶段升级与配置变更

### 阶段 1：允许滚动升级

旧 token 与新 token 都采用纳秒 epoch，owner/kind 编码及锁 key 不变，可以直接读写与 CAS 比较。不引入协议配置闸门，不要求停写迁移或清理旧 HASH。

滚动升级期间：

- 所有实例继续使用同一 `service_name` 和锁 namespace；部署配置应保持相同 `lock_expire_secs`，但阶段 1 尚无服务端强制校验。
- 旧实例仍可能使用客户端时间判断过期、写入客户端时间及重设 TTL。混跑保留这些既有风险，直到旧实例全部退出。
- 时钟偏差可能导致提前接管，进而造成并发写入；这不是每次混跑必然发生的问题，也不能仅凭编码兼容称为无风险。
- 新实例会按服务器时间判断旧 token；旧快时钟 token 可能额外阻塞，旧慢时钟 token 可能被提前判为过期。全部实例升级后，旧时间影响随正常续租、释放或回收逐步消退。
- 新旧编码兼容，阶段 1 回退不需要格式迁移，但会重新引入客户端计时行为及其风险。

阶段 1 的部署决定是接受短暂混跑的既有风险，以换取可增量交付；并非完整的锁安全性保证。

### 阶段 2：协调停写迁移

旧版本（包括阶段 1）不会遵守配置 HASH 闸门，因此阶段 2 再执行协调迁移：

1. 停止接收新的锁相关任务，暂停可能重放任务的消费者。
2. 排空在途写入与 handoff；停止全部未遵守配置协议的锁参与者及续租任务，保证它们不会恢复并继续写。
3. 在这个停写条件下清理该服务的旧锁 HASH 及相关运行时索引；使用限定服务前缀、可核对的管理操作，避免影响其他服务。不要只按新版本 30 秒租期等待：旧客户端时间和旧配置可能不可信。
4. 初始化服务级 v2 配置，使用统一租期。
5. 启动全部新实例，确认初始化校验通过后恢复任务。

不得仅换 token key 的 namespace 就同时运行新旧实例；这会把同一数据集拆成互不冲突的两套锁。阶段 2 切回不遵守配置协议的版本也需要相同停写迁移，不能直接回滚混跑。

未来若要在线引入配置协议，需要先发布所有参与者都遵守的闸门版本，再设计状态切换；不属于阶段 1。

## 9. 分阶段测试与验收

真实 Redis 测试使用唯一 service_name 隔离。每阶段独立验收，不用阶段 2/3 的测试阻塞阶段 1。

### 阶段 1

| 场景 | 验收结果 |
| --- | --- |
| A、B 均为新实例且租期相同；B 客户端时钟 +31s / -31s / 更大偏差 | acquire/is_locked 的冲突结果不受客户端时间影响；失败获取不改 A token 或 TTL。 |
| acquire、refresh、create、需要新确认的 CAS 写入传入虚构客户端时间 | 实际 token 时间落在请求前后 Redis TIME 范围内，不使用客户端值。 |
| 十进制时间计算、借位、下溢、旧纳秒精度、租期舍入及范围 | 精确计算，保留当前 `<` 过期边界；不把纳秒 epoch 转为 double。 |
| 同 scope、相同租期；新建、续租、release、rollback、kind mutation | TTL 由 Lua 固定计算；不接受独立客户端 TTL；有效 token 不因这些操作提前消失。 |
| 重入、升级、发布失败回滚、CAS 竞争 | 保留现有语义；服务器实际 replacement 可用于严格 CAS，回滚不误覆盖变化后的 token。 |
| 网络延迟、响应丢失、迟到成功、后台续租未及时运行 | 现有失效安全窗口和不可逆失效测试继续通过。 |
| 新旧 token 与实例混跑、阶段 1 回退 | 编码/key/CAS 兼容；正常续租可转为服务器时间；偏差场景记录既有提前接管或额外阻塞风险。 |
| 跨 manager adopt | 现有功能回归通过；明确保留本地年龄判断与 read/refresh 间隙，不要求阶段 3 原子能力。 |

TTL “未被修改”需比较绝对到期时间或考虑自然递减的容差，不能要求两次 `PTTL` 数值完全相等。

现有依赖虚构 `now_ns` 或 CAS 制造旧时间的 Redis 测试，改为测试专用时间运算夹具或受控 Redis 数据准备；generic provider 测试保持原协议。生产脚本不暴露可替代 Redis TIME 的测试参数。

阶段 1 本地验证记录（2026-10-28）：

- 使用隔离的 Redis 7.2.7：`REDIS_URL=redis://127.0.0.1:16383 cargo test -p ragfs --features cache --lib lock:: --no-fail-fast`，118 passed、0 failed、2 ignored。
- 新增 `cache_time_tests.rs`，验证精确十进制减法、租期舍入/边界、客户端时钟偏差、服务器确认时间、批量共享时间、固定 TTL 与旧 epoch token 兼容。
- 现有重入、回滚、失锁、网络延迟恢复、adopt、filesystem/memory/SQLite 回归通过。
- `cargo check -p ragfs-python --no-default-features`、修改文件的 rustfmt 检查及 `git diff --check` 通过。
- 未配置 Cluster/Sentinel 测试环境；本次没有验证这些拓扑或 Redis 8.x，也没有模拟 Redis 节点墙钟跳变。

### 阶段 2

- A=30s、B=1s：同路径和同 scope 不相交路径都在修改前返回配置不匹配。
- acquire、refresh、remove、rollback、CAS、create 等入口均校验协议及租期；错误请求不改 token、索引或绝对到期时间。
- 配置缺失、损坏、非 ready、版本不匹配时拒绝授权，不自动初始化。
- manager/provider 租期及本地安全窗口一致；结构化配置错误能撤销已有授权。
- 停写迁移、配置初始化、恢复及回退演练通过。

### 阶段 3

- adopt 的服务器年龄、owner、kind 和完整批次校验；失败不部分续租。
- read/refresh 竞争、同时认领、重放、响应丢失及重试；一次性 claim 不产生重复授权。
- 请求开始 `Instant` 及迟到响应检查不允许发布过期授权。

阶段 1 主要涉及 `cache_provder.rs` 及 Redis 测试，必要时配套调整 `manager.rs` 的实际 token/CAS 处理；不要求修改 `execute()`、增加配置初始化或 adopt capability。阶段 2 再涉及 `provider.rs`、manager、构造入口及配置错误；阶段 3 再扩展 adopt/handoff。每阶段更新对应事务文档，现有目录 rename 修改保持独立，不扩大 mkdir 的自动锁范围。

## 10. Redis 时间与安全性边界

全部参与者升级后，Redis TIME 消除已迁移脚本中的客户端时钟偏差；阶段 1 的 adopt 本地年龄检查仍是例外。Redis TIME 本身仍是节点墙钟：

- Redis 时间向前跳变可能使 token 和 HASH TTL 提前过期。
- 向后跳变可能延迟回收；阶段 1 不新增未来 token 错误协议。未来可单独评估异常时间检测，但它不能消除所有时间异常。
- failover 后主节点时钟偏差、复制丢失、Redis 数据被清空/淘汰仍可能造成锁丢失。
- 即使本地授权及时撤销，已经通过检查的在途文件 I/O 仍不能被 Redis 撤回。

服务部署需要受控的 Redis 时钟、避免主动大幅校时、锁数据避免淘汰等运行约束。保存 `max_seen_time` 可以检测部分回拨，但无法可靠判断向前跳变，也不能修复复制丢失，不把它包装成完整安全保证。

若要求在这些故障下仍保证旧写入者无法落盘，最终需要存储端 fencing：授权代次校验与文件/元数据提交放在同一事务或原子条件写入中。

## 11. 建议实施顺序

1. **阶段 1 独立交付**：实现公共 Redis TIME 与十进制减法，替换所有脚本的客户端时间/阈值与独立 TTL 参数，接好服务器实际 token 返回值；完成回归与混跑验证后滚动升级。
2. **阶段 2 独立交付**：加入服务配置 HASH、统一租期、初始化校验和确定性配置错误传播；完成停写迁移演练后部署。
3. **阶段 3 可选交付**：把 adopt 原子校验与 handoff 一次性 claim 一起设计、评审和验证。

阶段 1 不能只替换 acquire 而遗漏 refresh/create/CAS 的新确认时间，但不因阶段 2 配置协议或阶段 3 adopt 原子性尚未实现而阻塞交付。
