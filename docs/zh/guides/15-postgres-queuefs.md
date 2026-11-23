# PostgreSQL QueueFS

QueueFS 可以将队列、消息和消费者租约持久化到 PostgreSQL，保留 `/enqueue`、`/enqueue_ext`、`/dequeue`、`/peek`、`/size`、`/status`、`/messages`、`/clear`、`/ack`、`/retry` 控制文件接口。多个实例连接同一个可写主库并使用相同 `service_name` 时，可以竞争消费同一队列。

## 初始化与配置

由管理员或 migration role 在目标数据库执行一次：

```sh
psql "$OV_QUEUE_PG_DSN" -v ON_ERROR_STOP=1 \
  -f crates/ragfs/src/plugins/queuefs/postgres_schema.sql
```

脚本创建版本表和三个业务表，运行时只检查版本及持久化属性，并执行 DML，不建表或迁移。初始化脚本不是可重复执行的迁移工具；已安装的版本无需重新执行。

为专用运行账号授权，替换下面的 `queue_runtime`：

```sql
GRANT USAGE ON SCHEMA public TO queue_runtime;
GRANT SELECT ON public.ov_queuefs_schema TO queue_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE
  ON public.ov_queuefs_queues, public.ov_queuefs_workers, public.ov_queuefs_messages
  TO queue_runtime;
GRANT USAGE ON SEQUENCE public.ov_queuefs_messages_sequence_seq TO queue_runtime;
```

在每个服务器或 worker 启动环境中配置 DSN，例如：

```sh
export OV_QUEUE_PG_DSN='host=pg.example.com port=5432 dbname=openviking user=queue_runtime password=YOUR_PASSWORD sslmode=require'
```

合并以下片段到 `ov.conf`：

```json
{
  "service_name": "production",
  "storage": {
    "agfs": {
      "queuefs": {
        "mode": "shared",
        "backend": "postgres",
        "postgres_dsn_env": "OV_QUEUE_PG_DSN",
        "postgres_lease_seconds": 60
      }
    }
  }
}
```

配置只保存环境变量名；连接信息和密码不写入插件配置或数据库错误信息。独立 Rust 构建需要启用 `ragfs` 的 `postgres` 或 `full` feature；Python binding 已启用 `postgres`。已有的本地消息不会随切换自动迁移，应先排空旧队列或另行迁移。

`service_name` 限制为 1–256 个 UTF-8 字节，队列名为 1–1024 字节。`recover_stale_sec`、`busy_timeout_ms` 和 `db_path` 仅作用于 SQLite。共享消费者应使用 `mode=shared`；`mode=worker` 的队列名仍按 worker 隔离，其他 worker 不会自动消费旧 worker 的队列。

## 消费、持久化与恢复

- `enqueue` 提交后才返回。事务使用 `synchronous_commit=on`；数据库必须开启 `fsync` 并使用可靠存储。异步复制故障切换能否保留已确认提交，取决于 PostgreSQL 复制配置。
- `dequeue` 用队列级事务 advisory lock 保护调度资格判断，再通过 `FOR UPDATE SKIP LOCKED` 领取消息，写入当前实例的随机 owner。按递增 schedule sequence 优先领取，不保证严格的完成顺序。
- `/enqueue_ext` 保留调度元数据：同一 `serial_key` 只能有一条消息 processing，后续消息需等前序 ACK；同一 `group_key` 可以同时 processing 多条，但全部属于同一消费者，最后一条 processing 消息 ACK 或 retry 后释放归属。
- `/retry` 只能由领取消息的存活实例执行，保留消息 ID、数据、时间戳及调度 key，将消息放回 pending 尾部。串行消息的后继仍需等待原消息 ACK。
- 消息一直保留到 ACK。ACK 必须由领取消息的同一个 QueueFS 实例提交，并校验实例租约与消息 owner；重复 ACK 或其他存活实例的 ACK 返回 false。
- 每个实例使用独立数据库连接维持心跳，间隔为租约秒数除以 3 后向下取整。业务任务可以运行超过租约时长，只要实例持续续约。
- 每次 `dequeue` 先检查该队列的 processing 消息，只恢复 owner 已消失或租约过期的消息。新实例启动不重置其他存活实例的 processing 消息。`peek` 和 `status` 不触发恢复。
- 租约过期的实例不能续约复活，也不能继续队列操作，必须重启。消费者正常关闭前应先排空业务 worker，关闭会删除其租约；直接 drop 或进程崩溃则等待租约过期。
- `status` 在一个 SQL 快照中返回 pending 和 processing；`size` 仅返回 pending。`clear`、删除队列会删除包括 processing 在内的所有消息，应在业务侧协调在途任务。

这是至少一次投递：业务写入完成后、ACK 前崩溃会导致重新投递。ACK 校验保护队列状态，不能撤销已发生的业务写入，也不能阻止失去租约的旧进程继续执行其他系统中的副作用。业务应使用消息 ID 做幂等控制。超时或断连可能发生在提交成功但响应丢失之后；后端不会自动重放这类操作。

存活消费者持有的 processing 消息不会仅因消息年龄而恢复。业务处理抛错、ACK 失败，或者 dequeue 已提交但调用方未收到响应时，消息可能继续留在该消费者名下；由原实例按业务结果执行 ACK 或 retry，或停止/重启原实例后等待租约恢复。重启另一台仍在共享同一队列的服务器，不会释放原实例的存活租约。

每个实例使用两个连接，数据库操作在阻塞线程中运行。连接建立超时为 5 秒，事务 statement timeout 为 5 秒、lock timeout 为 1 秒；这些不是端到端网络超时。不同实例应使用相同的租约配置。错误只报告 SQLSTATE，不包含 PostgreSQL detail、消息正文或 DSN。

过期 worker 行不影响恢复，但崩溃较多时可由维护任务清理：

```sql
DELETE FROM public.ov_queuefs_workers WHERE lease_expires_at <= clock_timestamp();
```

队列表按 `service_name` 隔离数据，这是协作实例的逻辑命名空间。运行账号具有这些表的 DML 权限，命名空间不是恶意客户端之间的数据库权限隔离；需要更强隔离时使用不同数据库及账号。后端要求普通 logged 表和禁用 RLS。

PG QueueFS 只解决队列持久化与竞争消费。部署多实例时还需分别验证 PathLock、业务文件存储、account store、向量库和进程内任务状态的并发语义。

## 验证

仓库提供临时数据库测试入口，不会连接已有业务实例：

```sh
PG_BIN=/path/to/postgresql/bin python3 scripts/test_postgres_queuefs.py
```

测试包括竞争领取、ACK/retry 所有权、串行顺序、group 归属、长任务心跳、过期与崩溃恢复、字节及纳秒时间戳保留、异常事务回滚、命名空间、DML-only 账号和未初始化数据库拒绝启动。完整日志保存到 `target/queuefs-postgres-test.log`。
