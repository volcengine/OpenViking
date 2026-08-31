# RAGFS 缓存

RAGFS 缓存是 OpenViking 的可选读缓存层，用于加速文件全量读取和目录读取。它只作为加速层，不作为事实数据源；数据仍以 backend filesystem 为准。

适用前提：

- 只有一个 OpenViking / RAGFS 进程写入同一 namespace。
- 文件和目录变更都经过 RAGFS。
- backend 不被外部绕过 RAGFS 直接修改。
- 缓存 Provider 的同一 key 写入或删除成功后，后续读取不会返回旧值。

## 快速开始

首次配置仍建议先完成基础配置：

```bash
openviking-server init
openviking-server doctor
```

然后在 `~/.openviking/ov.conf` 中配置顶层 `cache` Provider，并在 `storage.agfs.cachefs` 选择 `backend=cache`：

```json
{
  "cache": {
    "provider": "redis",
    "params": {
      "mode": "standalone",
      "endpoints": ["redis://127.0.0.1:6379"],
      "pool_size": 32,
      "connect_timeout_ms": 1000,
      "command_timeout_ms": 1000,
      "default_ttl_seconds": 3600
    }
  },
  "storage": {
    "workspace": "./data",
    "agfs": {
      "backend": "local",
      "cachefs": {
        "backend": "cache",
        "namespace": "openviking",
        "max_file_size_bytes": 1048576,
        "bypass_prefixes": ["/queue", "/tmp"]
      }
    }
  }
}
```

启动 Redis 和 OpenViking：

```bash
redis-server
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
| `dynamic` | YuanRong、Mooncake 或闭源缓存系统 | 本期未实现，配置后返回 UnsupportedProvider |

`MemoryMockProvider` 只用于单元测试和 smoke test，不是生产配置项。

## 配置破坏性变更

旧缓存配置不再兼容，升级前需要完成迁移：

| 已删除配置 | 标准替代配置 |
|-----------|-------------|
| `storage.agfs.cache` | 顶层 `cache.provider` + `cache.params`，并设置 `storage.agfs.cachefs.backend="cache"` |
| `storage.agfs.queuefs.backend="redis"` 和 `queuefs.redis` | `storage.agfs.queuefs.backend="cache"`，并复用顶层 `cache` 配置 |
| Redis `mode="singleton"` | `mode="standalone"` |
| `tls_enabled=true` | endpoint 使用 `rediss://` |
| `read_from_replica` | 已删除，所有读命令统一访问主节点 |
| Redis Provider `key_prefix` | CacheFS 使用 `cachefs.namespace`；QueueFS 使用 `queuefs.cache_key_prefix` |

OpenViking 会对已删除字段直接返回迁移错误，不再静默转换。

## 后续 DynamicProvider

本期标准 OpenViking wheel 只内置 RedisProvider。DynamicProvider、`.so` 加载器和版本化 C ABI 放在后续阶段实现；当前配置 `provider=dynamic` 会在启动阶段返回 UnsupportedProvider。

动态库必须导出以下版本化入口：

```text
openviking_cache_provider_v1
```

Provider 发布物应注明 ABI 版本、目标 OS/CPU、最低 glibc 版本、外部 SDK 版本、动态依赖和 SHA256。依赖外部原生库时，由 Provider 发布方通过 RPATH、`LD_LIBRARY_PATH` 或部署说明保证动态链接器能够找到依赖。

外部 Provider 可以独立升级，不需要重新构建默认 OpenViking wheel；只有 DynamicProvider ABI 不兼容时，才需要同步升级 OpenViking。

## 配置项

顶层 `cache` 与 `storage` 并列：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `provider` | str | 无 | Provider 名称，本期支持 `redis` |
| `params` | object | `{}` | Provider 自有参数 |

`storage.agfs.cachefs` 支持以下业务配置：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `backend` | str | `"local"` | `local` 沿用原逻辑；`cache` 启用 CachedFileSystem |
| `namespace` | str | `"openviking"` | 缓存命名空间，用于隔离不同部署或租户 |
| `max_file_size_bytes` | int | `1048576` | 允许进入缓存的最大完整文件大小 |
| `traversal_mode` | str | `"backend"` | 递归 API 使用 backend 遍历或 `cached_traversal` |
| `bypass_prefixes` | list[str] | `[]` | 强制绕过缓存的路径前缀 |

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
| `default_ttl_seconds` | `3600` | 默认 TTL；`0` 表示不设置 TTL |
| `tls_insecure_skip_verify` | `false` | 跳过 `rediss://` 证书校验，仅用于受控测试环境 |

所有 Redis 读命令均发送到主节点，避免 QueueFS 读取到延迟的队列状态。需要传输加密时使用 Redis TLS，CacheRuntime 不额外增加应用层数据加密格式。

未来 DynamicProvider 配置结构：

`cache.params` 完全由外部 Provider 定义，下面的字段只用于说明配置传递方式，实际配置以 Provider 发布说明为准。

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

RAGFS 将缓存拆成两层：

- `CachedFileSystem`：实现文件系统语义，包括 cache hit/miss、backend 回源、回填、失效、generation 校验和指标。
- `CacheRuntime`：向业务层提供统一基础操作，本期在启动时绑定内置 RedisProvider；DynamicProvider 为后续扩展。

调用关系：

```text
OpenViking
  -> RAGFS / MountableFS
  -> CachedFileSystem
       |-> CacheRuntime -> RedisProvider
       |               `-> DynamicProvider（后续）
       `-> Backend FileSystem
```

这种边界让文件、目录、rename、递归删除和写后失效逻辑只在公共层实现。外部 Provider 不需要理解路径语义，只需要通过稳定 C ABI 提供 key-value 基础操作。

## 缓存对象

RAGFS 主要缓存三类对象。

### 文件缓存

文件 key 使用稳定命名空间和路径 hash：

```text
ragfs:v1:{namespace}:file:{hash(path)}
```

文件 value 是 `CacheEnvelope`，包含文件内容、对象类型、路径和 generation 快照。全量读取命中后，RAGFS 会先校验 envelope 和 generation，再返回内容。

默认策略会优先缓存 `.abstract.md` 和 `.overview.md` 这类摘要文件；超过 `max_file_size_bytes` 的文件不会进入缓存。非全量 range read 也会绕过缓存。

### 目录缓存

目录 key：

```text
ragfs:v1:{namespace}:dir:{hash(path)}
```

目录缓存保存 backend 原始 `read_dir` entries，而不是权限过滤后的最终结果。权限、角色和 agent context 仍在 OpenViking 上层实时处理。

这样同一份目录缓存可以服务 `ls`、`tree`、`glob`、`grep` 的文件收集阶段，以及删除或移动前的路径收集。

### 子树 Generation

子树 generation key：

```text
ragfs:v1:{namespace}:subtree:{hash(scope)}
```

`remove_all` 和目录 `rename` 可能让 Provider 中残留子孙 key。RAGFS 通过 bump subtree generation，让旧 envelope 的 generation 快照失效，后续真实读取会回源并重建缓存。

## 一致性与失效

单写者场景下，RAGFS 不需要分布式写锁。关键是按文件系统语义维护三类失效：

- 文件变更：删除或更新 `file_key(path)`，删除 `dir_key(parent)`。
- 目录变更：删除目录自身和父目录的 `dir_key`。
- 子树变更：对递归删除和目录 rename bump `subtree` generation。

典型写入顺序：

```text
获取进程内操作锁
-> 执行 backend 变更
-> 更新或删除相关 cache key
-> 必要时 bump subtree generation
-> 返回结果
```

如果 Provider 失败，RAGFS 会以 backend 为准，并让受影响路径进入短期 bypass，避免继续读取可能陈旧的缓存。

## 请求合并

当多个请求同时读取同一个未缓存的小文件或目录时，`CachedFileSystem` 会用进程内 inflight 表合并请求：

```text
第一个 miss 请求成为 leader，负责回源和回填。
后续相同 key 的请求成为 follower，等待 leader 结果。
请求完成后删除 inflight 条目。
```

这只减少同一 OpenViking 进程内的重复 backend 访问，不改变 Provider 的一致性边界。

## 缓存策略

RAGFS 会自动绕过不适合缓存的路径：

- 锁文件：`.path.ovlock`、`*.lock`、`*.lck`
- 控制文件：`enqueue`、`dequeue`、`peek`、`ack`
- 瞬时状态：`heartbeat`、`lease`、`cursor`、`offset`、`pid`
- 用户通过 `bypass_prefixes` 指定的路径前缀

权限敏感目录建议加入 `bypass_prefixes`。如果目录原始 entries 本身就依赖调用者权限，不应缓存该目录。

## 故障与观测

缓存层不能影响文件系统正确性：

- `get` 失败：回源 backend。
- `put` 失败：记录错误，路径进入 bypass。
- `delete` 失败：记录错误，路径或 scope 进入 bypass。
- Provider 不可用：不返回旧缓存，以 backend 结果为准。

建议重点观察：

- cache hit / miss / bypass
- stale generation
- provider get / put / delete 延迟
- cache set / delete 失败
- inflight leader / follower / backend saved
- backend fallback 字节数

## 推荐使用顺序

1. 关闭缓存验证 backend 基线行为。
2. 用内置 `redis` 验证真实远程缓存收益。
3. 对高性能或闭源缓存系统使用独立发布的 DynamicProvider `.so`。
4. 先缓存摘要文件和 raw `read_dir`，再扩展到更多普通小文件。
5. 将锁、控制面和权限敏感路径加入 `bypass_prefixes`。

一句话总结：RAGFS 缓存负责“按文件系统语义正确失效”，Provider 负责“把缓存对象放在哪里”。只要 backend 是事实来源，缓存命中就必须先通过 envelope 和 generation 校验。
