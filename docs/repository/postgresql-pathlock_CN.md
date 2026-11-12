# PostgreSQL 路径锁：改动与使用说明

公开文档站的配置入口见 [PostgreSQL PathLock 指南](../zh/guides/25-postgresql-pathlock.md)。

本次为 OpenViking 新增 `postgres` PathLock Provider，让连接同一个 PostgreSQL
主库的 OpenViking 进程共享路径锁。实现参考现有 SQLite 锁，复用已有的 holder
冲突检查与变更逻辑，并接入现有 PathLockManager、Rust 构建器和 Python 配置链路。

当前分支：`feat/postgresql-pathlock`。代码位于
`/Users/bytedance/Code/OpenViking-Office` 社区仓库。

本次范围是 **PG lock**。PG 文件系统、pgvector 索引库和存储写入 fencing 不在本次实现中。
使用 PG 锁无需安装 PostgreSQL 扩展，也无需启动 Redis。

## 1. 改动了哪些文件

路径均相对于 `/Users/bytedance/Code/OpenViking-Office` 仓库根目录。
加上本中文文档，共 14 个变更文件。

| 文件 | 类型 | 作用 |
| --- | --- | --- |
| `crates/ragfs/src/lock/postgres_provider.rs` | 新增 | PG 锁存储；建表、事务互斥、获取/续租/释放、数据库时间、断线重连 |
| `crates/ragfs/src/lock/manager.rs` | 修改 | 新增 `postgres_dsn_env` 配置；PG Provider 实现社区版本已有的 `replace_holder` 恢复接口 |
| `crates/ragfs/src/lock/mod.rs` | 修改 | 在 `postgres` feature 下注册、导出 PG Provider |
| `crates/ragfs/src/core/builder.rs` | 修改 | 根据 `provider = "postgres"` 创建 Provider，在 blocking worker 中初始化数据库连接 |
| `crates/ragfs/src/lock/cache_provder.rs` | 修改 | 仅为既有测试中的完整配置结构补充新字段，未改变 Redis 锁逻辑 |
| `crates/ragfs/Cargo.toml` | 修改 | 新增可选 PG/TLS 依赖和 `postgres` feature；`full` 包含 PG 支持 |
| `Cargo.lock` | 修改 | 更新 PG 依赖及其传递依赖的锁定结果 |
| `crates/ragfs-python/Cargo.toml` | 修改 | Python 原生绑定启用 `ragfs/postgres` |
| `crates/ragfs-python/src/lib.rs` | 修改 | 原生配置解析接受 `postgres`、`postgres_dsn_env`，使用顶层 `service_name` 作为 namespace；增加配置测试 |
| `openviking_cli/utils/config/agfs_config.py` | 修改 | Python 配置增加 PG Provider 和环境变量名校验 |
| `crates/ragfs/tests/postgres_pathlock.rs` | 新增 | 真实 PG 集成测试：并发连接、跨进程、租约、回滚、重连及完整构建器接入 |
| `tests/misc/test_config_validation.py` | 修改 | 验证 PG 配置透传、缺失/非法字段，以及不依赖 Redis 的配置 |
| `docs/repository/postgresql-pathlock.md` | 新增 | 英文配置、语义、限制和测试说明 |
| `docs/repository/postgresql-pathlock_CN.md` | 新增 | 本中文说明 |

## 2. 如何启用

### 第一步：重建包含 PG 支持的 Python 绑定

在本分支的仓库根目录 `/Users/bytedance/Code/OpenViking-Office` 执行。首次开发环境安装参考
[贡献指南](CONTRIBUTING_CN.md)，需要 Python 3.10+、Rust 1.91.1+ 及相应的原生构建工具。

```bash
# 首次配置开发环境
uv sync --all-extras

# 修改原生绑定后，重新安装并构建
uv pip install -e . --force-reinstall
```

已有开发环境可以直接执行重建命令。仅修改配置、继续使用未包含本次改动的旧版
Python 原生扩展，会出现不支持 `postgres` Provider 或不认识配置字段的问题。

### 第二步：准备 PostgreSQL 数据库

使用 PostgreSQL 13+、服务端编码为 UTF8 的可写主库，例如数据库 `openviking`、连接角色 `openviking`。
初始化会自动创建：

- `public.ov_pathlock_tokens`：存储 namespace、逻辑路径、owner、时间戳和 holder token。
- `public.ov_pathlock_expiry`：过期记录扫描索引。
- `public.ov_pathlock_lookup`：namespace 和路径摘要的非唯一查找索引。
- `public.path_prefix_2048(text)`：返回路径 UTF8 编码的前 2048 **字节**，类型为 bytea。
- `public.ov_pathlock_subtree_prefix_2048`：`(md5(namespace), path_prefix_2048(path))` 非唯一范围索引。

主键为数据库生成的 UUID；完整 namespace 和路径保留在 TEXT 字段。
查找索引仅保存 `md5(namespace), md5(path)`，过期索引仅保存
`md5(namespace), time_ns`，避免不可压缩长路径超过 B-tree 索引项大小限制。
摘要只用于筛选，查询仍比较完整 namespace 和路径；摘要碰撞不会覆盖或混用锁。
写入在 namespace 事务锁内先更新，未匹配完整路径时再插入。

前缀长度按字节计算，可在多字节字符中间截断；结果始终作为 bytea 比较，不解码。
该有界索引适用于标准 8 KiB PostgreSQL B-tree 页面，不依赖路径可压缩性。
辅助函数在 UTF8 数据库中执行 UTF8 到 UTF8 转换，与客户端编码无关；
初始化会拒绝非 UTF8 数据库。

```sql
CREATE FUNCTION public.path_prefix_2048(text) RETURNS bytea
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
AS 'SELECT pg_catalog.substring(pg_catalog.convert_to($1, ''UTF8''), 1, 2048)';
CREATE INDEX ov_pathlock_subtree_prefix_2048
ON public.ov_pathlock_tokens(md5(namespace), public.path_prefix_2048(path));
```

已有 UUID/摘要表会在初始化时补建前缀索引，租约记录保持不变。
建索引受事务和 DDL 超时约束；大表可由迁移角色提前完成。

从旧版 `PRIMARY KEY(namespace,path)` 升级时，先停止全部旧版实例。
初始化会在一个事务内为已有记录分配 UUID、替换主键和过期索引，并保留租约 token。
迁移角色需要拥有锁表，也可由表所有者预先完成迁移后再启动运行实例。
旧版二进制无法写入新结构，因此不支持新旧版本滚动混用。
迁移失败或 DDL 超时会整体回滚，保留旧表结构及记录。

首次自动建表时，连接角色需要数据库 CONNECT 权限及 `public` schema 的 CREATE
权限；运行时需要锁表的 SELECT、INSERT、UPDATE、DELETE 权限。
也可以由迁移角色预先创建表、函数和索引，再授予运行角色相应权限及函数 EXECUTE 权限。
Provider 初始化仍会执行 `CREATE ... IF NOT EXISTS`，只在辅助函数不存在时创建它。
预建函数须使用上面的定义；运行实例不会覆盖迁移角色的函数。

如果数据库和角色已经存在，首次允许自动建表的示例为：

```sql
-- 由数据库管理员在 openviking 数据库中执行
GRANT CONNECT ON DATABASE openviking TO openviking;
GRANT USAGE, CREATE ON SCHEMA public TO openviking;
```

### 第三步：通过环境变量提供连接串

远端数据库示例：

```bash
export OV_POSTGRES_DSN='host=pg.example.com port=5432 dbname=openviking user=openviking password=YOUR_PASSWORD sslmode=require'
```

本地开发数据库示例：

```bash
export OV_POSTGRES_DSN='host=127.0.0.1 port=5432 dbname=openviking user=openviking sslmode=disable'
```

支持 PostgreSQL URL 和 `key=value` 两种连接串格式。远端连接使用
`sslmode=require`，当前 native TLS 会按系统信任库校验服务端证书和主机名。
本地示例需要数据库认证配置允许该连接；需要密码时在连接串中补充。

连接串应设置在实际运行 OpenViking 的进程环境中。Docker/Kubernetes 部署时也需传入
容器环境。配置文件只保存环境变量名；连接串在初始化时读取，轮换后需重启实例。

### 第四步：修改已有 `ov.conf`

把以下字段合并到现有配置中，保留 embedding、模型、workspace 等其他配置。
这是启用 PG 锁的配置片段，不是完整的服务配置。

```json
{
  "service_name": "community-workspace",
  "storage": {
    "agfs": {
      "pathlock": {
        "provider": "postgres",
        "postgres_dsn_env": "OV_POSTGRES_DSN",
        "lock_expire_secs": 30
      }
    }
  }
}
```

| 字段 | 含义 |
| --- | --- |
| 顶层 `service_name` | PG 锁的 namespace；保护同一份文件的实例必须一致 |
| `storage.agfs.pathlock.provider` | 设置为 `postgres` |
| `storage.agfs.pathlock.postgres_dsn_env` | 必填，非空的环境变量名，例如 `OV_POSTGRES_DSN` |
| `storage.agfs.pathlock.lock_expire_secs` | 未续租锁的过期时间；默认 30 秒，必须有限且不小于 1 秒；参与实例必须一致 |

PG 锁不使用 `sqlite_path`。Python 配置中的 `lock_timeout_secs` 已弃用，
自动路径锁等待时间固定为 0，不需要配置该字段。
数据库事务自身的等待超时是独立设置的，见下文。

### 第五步：启动服务

在设置了上述环境变量的终端中执行：

```bash
uv run openviking-server --config /absolute/path/to/ov.conf
```

业务继续通过原有文件操作和 PathLock 接口使用锁，无需直接操作 PG 锁表。
多个实例保护同一份文件时，必须使用同一个数据库主库、相同 `service_name`、
相同 Provider 和相同过期时间。不同 namespace 的锁记录相互隔离。
底层文件系统仍需支持共享访问；PG lock 本身不会让一个独占的本地存储目录支持多实例。

## 3. Rust 调用方如何使用

直接依赖 `ragfs` 时启用 feature：

```toml
[dependencies]
ragfs = { path = "../ragfs", features = ["postgres"] }
```

通过标准构建器初始化：

```rust
use ragfs::core::builder::{build_default_stack, RagfsConfig};
use ragfs::lock::PathLockConfig;

// 在 Tokio async 上下文中执行；环境变量需在进程启动前设置。
let stack = build_default_stack(RagfsConfig {
    pathlock: PathLockConfig {
        provider: "postgres".into(),
        service_name: Some("community-workspace".into()),
        postgres_dsn_env: Some("OV_POSTGRES_DSN".into()),
        ..PathLockConfig::default()
    },
    ..RagfsConfig::default()
})
.await?;
```

后续使用 `stack.pathlock_manager` 或已挂载文件系统的 `stack.top`。
`ragfs` 默认 feature 不变；Python 绑定已启用 PG，`ragfs/full` 也包含 PG。

## 4. 锁语义与当前限制

与现有 SQLite holder 协议一致，支持 Exact/Tree 锁、重入、holder 级续租和释放、
锁升级、handoff/adopt。批量获取全成功才提交，冲突时整体回滚，包括过期记录清理。
路径范围按目录段边界处理，`/a` 不会误匹配 `/a-backup`，支持根目录和 UTF-8 路径。
子树查询先用 namespace 摘要与二进制前缀范围筛选，再比较完整 namespace 和路径范围。
前缀上界使用 `<=`，完整路径上界仍用 `<`，避免深层目录上下界截断成同一前缀时漏查。
前 2048 字节相同的路径仍需逐条复核，部署前需评估这类长公共前缀的查询耗时。

本地 release 压测（PG 17.6、热缓存、串行每组 200 样本）：
10 万活跃锁加其他 namespace 的 10 万锁时，空子树 SQL P50 从纯摘要版本的
7.122 ms 降至 0.227 ms，Tree 加锁 P99 从 12.681 ms 降至 5.692 ms。
但 1 万条长路径共享前 2048 字节时，深层近邻未命中仍扫描全部 1 万条候选，
SQL P50 从 15.424 ms 增至 23.750 ms。两轮分开执行，结果不代表生产延迟保证。
额外索引的持续写入与空间成本尚未压测。

每次操作使用一个事务，并取得 namespace 级事务 advisory lock，保护不存在的路径、
祖先和子树条件检查。事务显式使用 Read Committed，等待锁后读取最新已提交状态。
持有 namespace 锁期间，事务内缓存完整路径的 token 读取结果，包括不存在的记录。
成功写入和删除后同步缓存；过期清理实际删除记录后才使受影响路径的缓存失效。
缓存随事务结束销毁，后续操作读取最新已提交状态；冲突与 holder 规则保持不变。
锁记录的获取、续租、过期检查和 handoff 恢复使用主库 `clock_timestamp()`；
客户端提供租约时长。已过期 holder 不能续租，接管后旧 owner 不能续租或释放新锁。

底层 `try_create_token`、`compare_and_write_token` 和 legacy `refresh_token`
也使用主库时钟。创建时转换所有 holder 时间；CAS 转换新增或续租 holder，
保留同 owner 下未变化的时间，包括仅修改锁类型和 legacy 引用转换；变更 owner
时转换所有引用。CAS 先比较原始快照，再归一化写入；后续比较必须重新读取持久化 token。

每个 Provider 使用一个连接，在 Tokio blocking worker 上执行数据库操作。
连接建立超时为 5 秒、SQL statement timeout 为 5 秒、SQL lock timeout 为 1 秒。
数据库争用返回 `Busy`，路径所有权冲突返回 `Conflict`。这些设置限制连接建立和
服务端等待，不代表网络黑洞或 worker 饱和时端到端请求一定在该时间内结束。

路径业务冲突时，只有显式回滚成功且连接仍未关闭才保留连接。其他错误，包括 SQL、
提交或回滚失败，仍会丢弃连接，后续独立操作可以重连。提交响应丢失时事务结果可能未知，
因此不会自动重放写操作；遗留租约通过过期机制释放。

当前有三项需要在部署时考虑的边界：

1. **同一 namespace 内串行执行锁事务。** 本地压测不能据此承诺生产高并发性能；
   namespace 哈希碰撞只会增加无关 namespace 间的等待，不会混用记录。
2. **租约丢失后的存储写入没有 fencing。** 旧 worker 在锁失效后继续写本地文件或 S3
   的问题，需由后续 SQLFS 事务或存储侧版本/fencing 校验解决。
3. **必须连接单一可写主库。** 不使用只读副本；异步复制可能丢失已提交的锁记录，
   故障切换需要防止双主。本次未认证 HA 切换安全性。

## 5. 如何验证

使用可丢弃的本地测试数据库，避免在生产数据库上运行故障测试。
测试会创建独立 namespace；正常结束会清理 token，失败可能留下测试记录。
断线测试会终止测试角色自己的一条连接，要求该角色具备相应权限，并使用本地非 TLS DSN。

```bash
export OV_TEST_PG_DSN='host=127.0.0.1 port=5432 dbname=postgres user=test sslmode=disable'

# 真实 PG 集成测试；必须显式指定 --ignored 才会执行
cargo test -p ragfs --features postgres --test postgres_pathlock --locked -- --ignored

# 既有路径锁回归
cargo test -p ragfs --features postgres --lib lock:: --locked
```

### 本仓库验证记录

2026-11-11，在 `/Users/bytedance/Code/OpenViking-Office` 的
`feat/postgresql-pathlock` 分支重新执行，基线提交为 `5c8be8852`。
构建和测试直接使用本仓库源码；复用 Cargo 构建缓存。
测试数据库使用独立的数据目录，验证结束后已关闭。

| 验证项 | 结果 |
| --- | --- |
| 默认不启用 PG：`cargo check -p ragfs --locked` | 通过 |
| 启用 PG：`cargo check -p ragfs --features postgres --locked` | 通过 |
| 既有路径锁回归 | 93 项通过，1 项忽略 |
| PostgreSQL 17.6 集成测试 | 10 项通过；包含子进程入口及实际跨进程冲突验证 |
| PG Provider 非法过期时间校验 | 1 项通过，不连接数据库 |
| Python PG/SQLite 配置测试 | 10 项通过 |
| 原生绑定路径锁配置测试 | 4 项通过 |
| Python 原生扩展实际调用 PG | 两个客户端的路径冲突、续租、释放和再次获取均通过；未启用 Redis |

原生配置测试命令：

```bash
cargo test -p ragfs-python --no-default-features --locked pathlock_config
```

Python 配置测试可在已安装开发依赖的环境中执行：

```bash
python -m pytest -p no:cacheprovider --override-ini addopts='' \
  --confcutdir=tests/misc tests/misc/test_config_validation.py \
  -k 'postgres_pathlock or sqlite_pathlock' -q
```

集成覆盖独立连接竞争、祖先/子树冲突、路径边界、namespace 隔离、批量回滚、
CAS、客户端时钟偏差、过期接管、续租、handoff、断线重连、数据库 Busy，
以及两个完整构建的实例在不启用 cache runtime 时共享 PG 锁。
新版 `replace_holder` 的专项测试还验证：恢复时不会覆盖已经续租的 source holder，
不会因客户端时间偏差误删仍有效的 holder，也不会重新插入已经过期的 source。

长路径专项测试覆盖 3216 字节不可压缩路径、长 namespace、批量成功和失败回滚、
续租/释放、CAS、根目录和祖先/子树边界，以及超过全局清理上限的过期子树清理。
建表和旧表迁移测试使用会话内临时表，验证记录保留与重复初始化：

```bash
cargo test -p ragfs --features postgres --lib \
  schema_initialization_and_legacy_migration --locked -- --ignored
```

底层写入时钟专项覆盖客户端时间 `0`、`1`、`u128::MAX`，v1/v2 创建、legacy
续租、CAS 快照不匹配、独立 holder 时间保留、owner 变更和 legacy 引用转换。
过期记录通过测试专用 SQL 注入，不再依赖正常写 API 持久化旧时间。

2026-11-12 长路径、前缀索引及 DB 时钟修复验证：PostgreSQL 17.6 集成文件串行运行 17 项全部通过
（`--include-ignored --test-threads=1`，含非法过期时间校验及子进程入口）；
既有锁回归 93 项通过、2 项忽略，迁移专项另行运行 1 项通过。
迁移专项同时验证新表初始化、旧租约保留、重复初始化及迁移失败回滚。
Python PG/SQLite 配置测试 10 项通过。本地 PostgreSQL 保持运行。
前缀专项覆盖相同 2048 字节前缀、UTF8 字符中间截断、客户端编码独立性，
以及在已有 UUID/摘要结构上补建索引。

## 6. 常见问题

| 现象 | 检查项 |
| --- | --- |
| 不支持 `postgres` 或不认识 `postgres_dsn_env` | 确认使用本分支代码，并重新构建、安装 Python 原生绑定 |
| 提示缺少 `postgres_dsn_env` | 配置值应为非空环境变量名 |
| 提示连接环境变量未设置 | 检查变量是否传入实际服务进程或容器；不要只在另一个终端设置 |
| 建表或访问表失败 | 检查连接角色的 schema 权限、锁表权限及预建索引情况 |
| 返回 `Busy` | 检查 namespace 下的数据库锁等待和数据库负载 |
| 多实例互相看不到锁 | 检查数据库、namespace、Provider、过期时间是否一致 |

错误信息仅保留 SQLSTATE 或通用连接错误，避免输出服务端可能携带的参数和凭证。
进一步定位时结合数据库端日志与 `pg_stat_activity` 检查。

英文说明见 [PostgreSQL PathLock](postgresql-pathlock.md)。
