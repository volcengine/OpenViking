# RAGFS 缓存

RAGFS 缓存是 OpenViking 的可选查询结果缓存层，用于加速 `stat`、`read_dir`、`tree_directory`、`glob_directory` 和 `grep`。它只作为加速层，不作为事实数据源；数据仍以 backend filesystem 为准。

CachedFileSystem 适用前提：

- 只有一个 OpenViking / RAGFS 进程写入同一 cache namespace。
- 文件和目录变更都经过 RAGFS。
- backend 不被外部绕过 RAGFS 直接修改。
- 缓存 Provider 的同一 key 写入或删除成功后，后续读取不会返回旧值。

这些前提只适用于读缓存层。QueueFS 和 PathLock 也复用 CacheRuntime，但有各自的一致性规则。

## 快速开始

首次配置仍建议先完成基础配置：

```bash
openviking-server init
openviking-server doctor
```

然后在 `~/.openviking/ov.conf` 中配置顶层 `cache` Provider，并在 `storage.agfs.cachefs` 选择 `backend=cache`：

```json
{
  "service_name": "openviking-server",
  "cache": {
    "provider": "redis",
    "params": {
      "mode": "standalone",
      "endpoints": ["redis://127.0.0.1:6379"],
      "pool_size": 32,
      "connect_timeout_ms": 1000,
      "command_timeout_ms": 1000,
      "default_ttl_seconds": 300
    }
  },
  "storage": {
    "workspace": "./data",
    "agfs": {
      "backend": "local",
      "cachefs": {
        "backend": "cache"
      },
      "pathlock": {
        "provider": "cache",
        "lock_expire_secs": 30.0
      }
    }
  }
}
```

启动 Redis 和 OpenViking：

```bash
redis-server
```

在另一个终端启动 OpenViking：

```bash
openviking-server --config ~/.openviking/ov.conf
```

如果配置文件在默认路径 `~/.openviking/ov.conf`，也可以直接运行：

```bash
openviking-server
```

可用 Provider：

| Provider | 适用场景 | 备注 |
|----------|----------|------|
| `redis` | 默认交付、普通网络环境 | 内置于 RAGFS，支持 standalone、Cluster 和 Sentinel |
| `dynamic` | YuanRong、Mooncake 或闭源缓存系统 | 通过版本化 C ABI 从外部动态库加载 |

`MemoryMockProvider` 只用于单元测试和 smoke test，不是生产配置项。

### Cache PathLock

将 `storage.agfs.pathlock.provider` 设为 `cache`，即可通过共享 Redis
CacheRuntime 协调多个 OpenViking 进程的路径锁。OpenViking 会从顶层
`service_name` 生成 PathLock Redis namespace。独立部署应使用不同
`service_name`。Cache PathLock 只支持内置 Redis Provider；分布式路径锁不会解除 CachedFileSystem 的单写者前提。

Redis HASH key 按逻辑路径 scope 拆分：

```text
{service_name}:ov:pathlock:global:tokens
{service_name}:ov:pathlock:scope:_system:tokens
{service_name}:ov:pathlock:scope:account:{account}:tokens
```

`{service_name}` 是 Redis Cluster hash tag，因此同一部署的 PathLock key
仍位于同一 slot。Tree 冲突检查只扫描请求所属 scope 的 HASH。`/` 和
`/local` 使用 global HASH，但不会扫描 account 或 `_system` HASH。
跨 scope batch 会被拒绝。

不要混部仍使用旧单 HASH key 的版本和使用 scope key 的版本。先停止旧版本
写入，至少等待 `2 * lock_expire_secs` 让旧 key 过期，再启动新版本。

## 配置破坏性变更

旧缓存配置不再兼容，升级前需要完成迁移：

| 已删除配置 | 标准替代配置 |
|-----------|-------------|
| `storage.agfs.cache` | 顶层 `cache.provider` + `cache.params`，并设置 `storage.agfs.cachefs.backend="cache"` |
| `storage.agfs.queuefs.backend="redis"` 和 `queuefs.redis` | `storage.agfs.queuefs.backend="cache"`，并复用顶层 `cache` 配置 |
| Redis `mode="singleton"` | `mode="standalone"` |
| `tls_enabled=true` | endpoint 使用 `rediss://` |
| `read_from_replica` | 已删除，所有读命令统一访问主节点 |

## DynamicProvider

OpenViking 已内置 DynamicProvider 加载器和版本化 C ABI。默认 wheel 不携带第三方 SDK 或 Provider 动态库；需要接入外部缓存系统时，独立部署 Provider 动态库并配置 `provider=dynamic`。

动态库必须导出以下版本化入口：

```text
openviking_cache_provider_v1
```

C 接口契约定义在 `crates/ragfs/include/openviking_cache_provider_v1.h`。Provider 返回的数据必须使用 Host allocator 分配，遵守文档中的内存所有权和关闭语义，禁止异常穿过 C ABI，并保证 handle 可以安全并发调用。

Provider 发布物应注明 ABI 版本、目标 OS/CPU、最低运行时版本、外部 SDK 版本、动态依赖和 SHA256。依赖外部原生库时，由 Provider 发布方通过 RPATH、`LD_LIBRARY_PATH` 或部署说明保证动态链接器能够找到依赖。

外部 Provider 可以独立升级，不需要重新构建默认 OpenViking wheel；只有 DynamicProvider ABI 不兼容时，才需要同步升级 OpenViking。

## 配置项

顶层 `service_name` 默认是 `"openviking-server"`，会为 CacheFS、QueueFS cache backend、
Cache PathLock 和 cache-backed 多写 metadata 提供 Redis namespace。

顶层 `cache` 与 `storage` 并列：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `provider` | str | 无 | Provider 名称，支持 `redis` 和 `dynamic` |
| `params` | object | `{}` | Provider 自有参数 |

`storage.agfs.cachefs` 支持以下业务配置：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `backend` | str | `"local"` | `local` 沿用原逻辑；`cache` 启用 CachedFileSystem |

`storage.agfs.pathlock` 控制 PathLock 存储：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `provider` | str | `"filesystem"` | `filesystem`、`memory` 或 `cache` |
| `lock_expire_secs` | float | `30.0` | 锁 stale 超时；不得小于 `1.0` |

Redis 配置：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `mode` | `"standalone"` | Redis 部署模式 |
| `endpoints` | `["redis://127.0.0.1:6379"]` | Redis 连接地址；`redis://` 使用明文传输，`rediss://` 使用 TLS |
| `username` | `""` | Redis ACL 用户名 |
| `password_env` | `""` | 存放 Redis 密码的环境变量名 |
| `pool_size` | `32` | 命令并发数 |
| `connect_timeout_ms` | `1000` | 连接超时 |
| `command_timeout_ms` | `20` | 命令超时 |
| `default_ttl_seconds` | `300` | CacheFS query bucket 使用的 runtime 默认 TTL；`0` 表示不设置 TTL |
| `tls_insecure_skip_verify` | `false` | 跳过 `rediss://` 证书校验，仅用于受控测试环境 |

所有 Redis 读命令均发送到主节点，避免 QueueFS 读取到延迟的队列状态。需要传输加密时使用 Redis TLS，CacheRuntime 不额外增加应用层数据加密格式。

DynamicProvider 配置：

`cache.params.library` 由 OpenViking 用于加载动态库，其余字段由外部 Provider 定义并作为 JSON 传入 `create`。实际参数以 Provider 发布说明为准。

```json
{
  "cache": {
    "provider": "dynamic",
    "params": {
      "library": "/opt/openviking/providers/libopenviking_cache_provider.so",
      "endpoint": "127.0.0.1:31501",
      "request_timeout_ms": 5000
    }
  }
}
```

## 整体架构

RAGFS 将 Provider 访问和各业务消费者分开：

- `CachedFileSystem`：实现文件系统语义，包括 cache hit/miss、backend 回源、回填、失效、request 校验和指标。
- `CacheRuntime`：向业务层提供统一基础操作，启动时绑定内置 RedisProvider 或外部 DynamicProvider。
- `QueueFS` 和 `RedisPathLockProvider`：复用共享 CacheRuntime 存储队列和分布式锁。RedisPathLockProvider 要求使用内置 RedisProvider。

调用关系：

```text
OpenViking
  -> RAGFS / MountableFS
       |-> CachedFileSystem ------\
       |-> QueueFS cache backend --+-> shared CacheRuntime -> RedisProvider
       `-> RedisPathLockProvider --/                     `-> DynamicProvider
       `-> Backend FileSystem
```

CachedFileSystem 和 QueueFS 可以使用 RedisProvider 或 DynamicProvider。
RedisPathLockProvider 依赖 Redis Lua，因此只使用 RedisProvider。文件系统语义
保留在 CachedFileSystem；外部 Provider 只通过稳定 C ABI 提供 key-value
基础操作。

## 缓存对象

RAGFS 缓存只读查询结果。不再存储旧的路径对象缓存。

### Query Bucket

每个路径和操作对应一个 HASH bucket：

```text
<service_name>:ov:cache:v1:<path_key>:<op>
```

HASH field 是 `xxh3_128(canonical_request_json)`。value 是 `QueryCacheEnvelope`，包含：

- `request`
- `payload`

RAGFS 返回缓存前会比较已存 `request` 和当前请求。这样可以防止 hash 碰撞或请求编码变化导致误读。

支持的操作：

- `stat`
- `read_dir`
- `tree_directory`
- `glob_directory`
- `grep`

### Query Index

所有活跃 bucket 会注册到一个 ZSET：

```text
<service_name>:ov:cacheidx:v1
```

ZSET member 是 bucket key。score 是 `expire_at_ms`。写入发生时，RAGFS 通过这个 index 找到受影响 bucket，不扫描全部 Redis key。

## 一致性与失效

单写者场景下，RAGFS 不需要分布式写锁。每次写入后，RAGFS 物理删除受影响的 query bucket。

普通路径变更会在内存中构造受影响 bucket：

- 精确路径 bucket：`stat`、`read_dir`、`tree_directory`、`glob_directory`、`grep`
- 父目录 bucket：`read_dir(parent)`
- 祖先递归 bucket：`tree_directory`、`glob_directory`、`grep`

然后对 bucket key 发送一次批量 `UNLINK`，对 index 发送一次批量 `ZREM`。

对递归路径操作，如 `remove_all`、`rename`、`replace`，RAGFS 还会扫描 query index：

```text
ZSCAN <service_name>:ov:cacheidx:v1 MATCH '<service_name>:ov:cache:v1:<path_key>/*:*' COUNT 1000
```

命中的后代 bucket 会进入同一批量 `UNLINK` 和 `ZREM`。不保留 generation 兜底逻辑。

典型写入顺序：

```text
执行 backend 变更
-> 投递异步失效任务
-> 返回结果
后台任务 -> ZSCAN 获取后代 bucket
后台任务 -> 构造精确路径和祖先 bucket
后台任务 -> UNLINK bucket key 并 ZREM index member
```

如果 Provider 失败，RAGFS 会以当前请求的 backend 结果为准，记录缓存错误，并且不缓存这次结果。

## 请求合并

当多个请求同时读取同一个未缓存查询时，`CachedFileSystem` 会用进程内 inflight 表合并请求：

```text
第一个 miss 请求成为 leader，负责回源和回填。
后续相同 key 的请求成为 follower，等待 leader 结果。
请求完成后删除 inflight 条目。
```

这只减少同一 OpenViking 进程内的重复 backend 访问，不改变 Provider 的一致性边界。

## 缓存范围

CacheFS 只缓存 `stat`、`read_dir`、`tree_directory`、`glob_directory` 和 `grep` 的查询结果。缓存键由路径、操作类型和查询参数生成。写操作成功后会主动删除受影响的查询 bucket。`.path.ovlock`、`.exact.ovlock.*` 等 PathLock 内部文件不会进入查询缓存；直接查询这些文件时绕过 CacheFS。

## 故障与观测

缓存层不能影响文件系统正确性：

- `get` 失败：回源 backend。
- `put` 失败：记录错误，保留 backend 结果。
- `delete` 失败：记录错误，TTL 兜底。
- Provider 不可用：不返回旧缓存，以 backend 结果为准。

建议重点观察：

- query hit / miss
- provider get / put / delete 延迟
- cache set / delete 失败
- backend fallback 次数

## 推荐使用顺序

1. 关闭缓存验证 backend 基线行为。
2. 用内置 `redis` 验证真实远程缓存收益。
3. 对高性能或闭源缓存系统使用独立发布的 DynamicProvider `.so`。
4. 缓存 `stat`、`read_dir`、`tree_directory`、`glob_directory` 和 `grep` 查询结果。

RAGFS 缓存负责按文件系统语义失效，Provider 负责决定缓存对象放在哪里。backend 仍是事实来源，缓存命中必须先通过 request 校验才会返回。

启用后，将重复读取和文件更新后的失效行为与基线比较。确认返回内容保持最新，再判断命中率提升是否带来收益。
