# PostgreSQL PathLock

当多个 OpenViking 进程或主机需要共享 Exact/Tree 路径锁、且不希望依赖 Redis 时，
可使用 PostgreSQL PathLock。PostgreSQL 保存锁记录并协调获取、续租、释放和
handoff，无需安装 PostgreSQL 扩展。

这是 PathLock 后端。业务文件、向量数据、CacheFS 和 QueueFS 保留各自的后端与并发要求。

## 选择 Provider

| Provider | 协调范围 | 要求 |
| --- | --- | --- |
| `filesystem` | 访问同一批锁文件的参与者 | 文件后端提供适合的加锁语义 |
| `memory` | 单进程 | 不提供共享锁状态 |
| `sqlite` | 同一主机上的进程 | 共享同一个实际的本地磁盘数据库文件，不支持 EFS/NFS/SMB |
| `postgres` | 多进程或多主机 | 同一可写 PostgreSQL 主库和锁 namespace |
| `cache` | 多进程或多主机 | 共享 Redis，通过顶层 `cache` 配置 |

保护同一批文件的副本必须使用相同的 Provider、顶层 `service_name` 和
`lock_expire_secs`。PostgreSQL 参与者还必须使用同一数据库。
不同 `service_name` 会创建相互独立的锁 namespace。

## 准备 PostgreSQL

使用 PostgreSQL 13+，服务端编码为 UTF8。连接必须指向可写主库；
锁操作不能发送到只读副本。

首次启动时，Provider 创建 `public.ov_pathlock_tokens`、
`public.path_prefix_2048(text)` 辅助函数，以及查找、过期和子树索引。
初始化角色需要 `public` schema 的 CREATE 权限；运行角色需要锁表的
SELECT、INSERT、UPDATE、DELETE 权限和辅助函数的 EXECUTE 权限。
也可由迁移角色预建这些对象，并在启动前授予运行角色所需权限。

预建辅助函数时使用下面的定义：

```sql
CREATE FUNCTION public.path_prefix_2048(text) RETURNS bytea
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
AS 'SELECT pg_catalog.substring(pg_catalog.convert_to($1, ''UTF8''), 1, 2048)';
```

初始化只在函数不存在时创建它；已有函数应使用上述定义。
从早期 `(namespace,path)` 主键升级时需要表所有者权限，迁移前应停止全部旧版实例。

使用包含 PostgreSQL 支持的 Python native binding。
源码安装升级时，应先重建 binding，再选择此 Provider。
Rust 接入启用 `ragfs/postgres` feature。

## 配置 `ov.conf`

将下面的片段合入现有服务端配置，保留模型、文件存储、向量数据库和队列配置：

```json
{
  "service_name": "production",
  "storage": {
    "agfs": {
      "pathlock": {
        "provider": "postgres",
        "postgres_dsn_env": "OV_POSTGRES_DSN",
        "lock_expire_secs": 30.0
      }
    }
  }
}
```

| `storage.agfs.pathlock` 下的字段 | 含义 |
| --- | --- |
| `provider` | 设置为 `postgres` |
| `postgres_dsn_env` | 必填、非空；保存连接 DSN 的服务进程环境变量名 |
| `lock_expire_secs` | 租约过期时长，不得小于 `1.0` 秒，默认 `30.0` |

在每个服务和 worker 启动前设置对应环境变量：

```sh
export OV_POSTGRES_DSN='host=pg.example.com port=5432 dbname=openviking user=openviking password=YOUR_PASSWORD sslmode=require'
```

支持 PostgreSQL URL 和 key/value DSN。`postgres_dsn_env` 填环境变量名，
不直接填连接字符串。DSN 在初始化时读取，轮换凭据后需重启参与进程。

远程连接使用 `sslmode=require`，native TLS connector 使用系统信任库校验
证书和主机名。`sslmode=disable` 仅用于可丢弃的本地测试数据库。

PostgreSQL PathLock 不要求顶层 `cache` 配置。
如果其他模块选择 Redis cache 后端，仍需为这些模块配置 Redis。

## 验证协调

启动两个使用同一主库、service name 和过期时长的参与进程。
第一个进程持有 Tree lease 时，第二个进程在该根目录下申请 Exact lease，
应在前者释放或达到过期接管条件前发生冲突。
此步骤验证 PathLock 协调，不代表所有存储后端都支持多写者。

Provider 使用主库时钟生成锁时间戳并检查过期。
批量获取全成功才提交，否则整体回滚。各 holder 独立续租和释放，
过期 holder 在接管后不能续租。

Provider 能对不存在的逻辑路径加锁，无需创建业务目录。
当前快照 API 仍会跳过 PostgreSQL 的缺失目标，因为选锁代码只在 `cache` 和
`sqlite` 模式下为缺失目标申请 Tree 锁。详见[显式路径快照表](15-snapshot.md)。

## 部署与 Provider 切换

切换 Provider（包括 filesystem → PostgreSQL、Redis ↔ PostgreSQL）前，
先停止全部写入者和续租任务。各 Provider 独立维护锁状态，PostgreSQL 不检查
已有 filesystem、SQLite 或 Redis token。保留持久化队列，
恢复业务前验证 consumer 已在目标 Provider 中重新取得覆盖范围。
详见[Provider 切换说明](../concepts/09-transaction.md#switching-to-postgresql)。

选择 PostgreSQL PathLock 不会改变：

- QueueFS：默认仍为 SQLite，不应在 EFS/NFS/SMB 上跨主机共享 WAL 数据库。
- CachedFileSystem：仍要求单写者。
- 本地向量存储：workspace 进程锁和并发限制仍然存在。
- 租约失效后的业务写入：PathLock 不提供存储侧 fencing。

同一 namespace 内的操作串行执行，应按目标并发量测量加锁和续租表现。
使用单一可写主库、防止 split brain，并考虑异步复制故障切换时丢失已提交锁记录的风险。

## 排查问题

| 现象 | 检查项 |
| --- | --- |
| 不识别 `postgres` Provider | 重建或升级 native binding |
| 缺少 DSN | 配置 `postgres_dsn_env`，并在实际服务和 worker 环境中导出该变量 |
| 初始化 schema 失败 | 检查 UTF8 编码、schema 权限、对象所有权和迁移耗时 |
| 参与者之间不冲突 | 核对主库、数据库、service name、Provider 和过期配置 |
| 数据库 `Busy` | 检查数据库争用和负载，与逻辑路径冲突区分处理 |

全部配置字段见[服务端配置](../configuration/01-server.md#storage)，
租约与恢复行为见[路径锁与崩溃恢复](../concepts/09-transaction.md)。
