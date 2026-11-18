# 流程观测

此能力复用现有 Usage/Audit 事件总线、后台 writer 和 SQLite／可选 PostgreSQL
存储，查询已保留的模型用量及阶段观测，不增加模型调用，也不视为完整账单。

## 配置

在 `server.observability.usage_audit` 中显式设置：

```json
{
  "enabled": true,
  "performance_enabled": true,
  "performance_retention_days": 14,
  "backend": "postgres",
  "postgres": {
    "dsn": "postgresql://user:password@localhost/openviking",
    "schema_name": "openviking_usage_audit"
  }
}
```

`performance_enabled` 默认关闭，详细记录默认保留 14 天（1–366），默认 backend
仍为 SQLite。既有模型、重试、并发、队列和统计保留期默认值不变。多实例应显式
使用同一 PostgreSQL 和独立 schema，安装 `postgres` extra；切换不自动迁移历史。
独立 Bot gateway 仅在显式启用时启动同一采集 owner，读取相同配置；其独立本地
SQLite 文件不会自动出现在另一服务器的 Console。Compile 复用认证连接与既有
Task ID，诊断上下文不提供权限。版本化新表不伪造历史调用或流程，切换采集方式
需要协调写者；旧版／关闭采集的写者不参与新收据协议。

## API

`GET /api/v1/console/performance`：`start`／`end` 为带时区 ISO 半开区间，最多
31 天；`view` 为 `summary`、`series`、`workflows` 或 `details`。
可按 `workflow_id`、`task_id`、`request_id`、`model`、`status` 精确过滤；
管理员可用 `user_id`，普通用户只能查自己。账户始终来自认证，流程 ID 不代表权限。
分页 `page_size` 为 1–100，默认 50；`cursor` 绑定主体与筛选条件。
详情可沿用列表返回的 `observed_before`。

使用按账户串行、单调的写入时钟冻结新增观测，晚到但事件时间较早的数据不会插入
既有分页。不向普通用户暴露全局序号。保留期或主动清除仍能移除记录，并非归档快照。
单次读取最多 20,000 项，`coverage=truncated` 时汇总仅包含该子集，请缩小范围。
`retention_limited` 表示请求早于 `retained_after`；`observed` 也不保证采集完整。

## 统计口径

Token 各字段分别返回已知小计 `known_tokens` 与 `unknown_events`。缺失不是零，
明确报告的零保留。缓存和 reasoning 是输入／输出子集，不再加到总数。
报告、估算、未确认用量分别返回，不能把已知小计当成provider报告账单；已有部分字段也保留报告不完整的事件计数。缓存命中率仅使用actual报告中同时有缓存与输入分母的记录，并返回覆盖事件数。
来源未经确认的旧观测与明确未知用量分别计数，不补造 provider 报告。
provider attempt 也可能尚未发出请求；adapter 调用不等于已计费物理 RPC。
共享 carrier 只计一次，不分配给计时器继承的首个 Task；未引入对应合批 owner 时
不虚构按任务的分摊。

`duration_by_kind` 分开 HTTP、Task、队列、模型与各阶段名，累计 span 秒数可能重叠，
不能把嵌套阶段相加当墙钟或关键路径。已知区间并集单独返回。新阶段事件只存安全
元数据，不带提示词、正文或错误回包。Studio 提供主体隔离的列表／详情、筛选分页、
缓存覆盖、未知提示与截断提示；切换连接不会复用旧主体的选择状态。

开启后，同一现有事务内领取事件收据、存详细观测并写原有投影。同一 ID／事实的
重复投递不重复增加用量，冲突 ID 使该批回滚；失败时收据也回滚，不新增重试循环。
收据接受期不短于各投影保留期；若用量或审计无限保留，收据也不按年龄过期。
持久水位防止时钟回退后重新接受已过期事件，较短的详情保留期不截断旧用量窗口。

`collection.producers` 是已观测生产者生命周期内的投递计数，不是选定区间内的
物理 RPC 数。记录接收、持久化、丢弃、失败、不确定、待处理与正常关闭。
首次成功 flush 前崩溃可能完全没有记录，诊断写失败、主体内存追踪达到上限也可能
缺失。因此 `unobserved_producers_possible=true`、`billing_complete=false`
始终保留。事件总线和队列仍是尽力模式，不承诺完整账单、模型执行严格一次或所有
provider／Embedding 分配都已覆盖。

队列等待仅在时钟可比较时以端到端耗时减处理耗时估算；时间戳缺失或回拨时保持未知。跨 Pod 时钟偏差会影响该估算，不能据此推断精确关键路径。
