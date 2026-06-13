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

然后在 `~/.openviking/ov.conf` 的 `storage.agfs.cache` 中启用缓存。下面是 Redis 示例，适合快速验证：

```json
{
  "storage": {
    "workspace": "./data",
    "agfs": {
      "backend": "local",
      "cache": {
        "enabled": true,
        "provider": "redis",
        "namespace": "openviking",
        "max_file_size_bytes": 1048576,
        "bypass_prefixes": ["/queue", "/tmp"],
        "redis": {
          "mode": "standalone",
          "endpoints": ["redis://127.0.0.1:6379"],
          "pool_size": 32,
          "connect_timeout_ms": 1000,
          "command_timeout_ms": 20,
          "key_prefix": "ragfs-cache",
          "default_ttl_seconds": 3600,
          "read_from_replica": false
        }
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
| `memory` | 本地验证、测试 | 进程内缓存，重启后丢失 |
| `redis` | 快速落地、普通网络环境 | 当前支持 standalone；建议只从 primary 读取 |
| `yuanrong` | 近计算缓存、共享内存或异构多级缓存 | 需要 Yuanrong worker 和 native feature |
| `mooncake` | 远程内存池、RDMA/TCP 数据面 | 需要 Mooncake 服务和 native feature |

如果运行包没有编译对应 Provider，启动时会返回类似 “requires the ... feature” 的错误。

## 原生 Provider 构建

标准 OpenViking wheel 适用于 `memory` 和 `redis` Provider。`yuanrong` 和
`mooncake` Provider 依赖平台相关的原生 SDK，需要针对目标部署环境单独构建。

先安装 wheel 构建工具：

```bash
python -m pip install "maturin[patchelf]"
```

### Yuanrong

安装 Yuanrong DataSystem C++ SDK，并导出头文件和库目录：

```bash
export YUANRONG_SDK_INCLUDE=/path/to/yuanrong/include
export YUANRONG_SDK_LIB_DIR=/path/to/yuanrong/lib
# 可选，默认值为 "datasystem"。
export YUANRONG_SDK_LIB_NAME=datasystem
export LD_LIBRARY_PATH="$YUANRONG_SDK_LIB_DIR:${LD_LIBRARY_PATH:-}"
```

构建并安装 wheel：

```bash
maturin build --release \
  --manifest-path crates/ragfs-python-native/Cargo.toml \
  --features yuanrong-native

python -m pip install --force-reinstall target/wheels/ragfs_python-*.whl
```

OpenViking 启动时，`storage.agfs.cache.yuanrong` 配置的 Yuanrong worker
必须可用。

### Mooncake

检出 `crates/ragfs-cache-mooncake/Cargo.toml` 使用的 Mooncake revision，
然后构建启用 Rust 支持的 Mooncake Store：

```bash
cmake -S /path/to/Mooncake -B /path/to/Mooncake/build \
  -DWITH_STORE=ON \
  -DWITH_STORE_RUST=ON \
  -DCMAKE_BUILD_TYPE=Release

cmake --build /path/to/Mooncake/build \
  --target build_mooncake_store_rust mooncake_master -j
```

导出 Mooncake 官方 Rust binding 所需路径：

```bash
export MOONCAKE_BUILD_DIR=/path/to/Mooncake/build
export MOONCAKE_STORE_LIB_DIR="$MOONCAKE_BUILD_DIR/mooncake-store/src"
export MOONCAKE_STORE_INCLUDE_DIR=/path/to/Mooncake/mooncake-store/include
export LD_LIBRARY_PATH="$MOONCAKE_BUILD_DIR/mooncake-common:\
$MOONCAKE_BUILD_DIR/mooncake-common/src:\
$MOONCAKE_BUILD_DIR/mooncake-store/src:\
$MOONCAKE_BUILD_DIR/mooncake-store/src/cachelib_memory_allocator:\
$MOONCAKE_BUILD_DIR/mooncake-transfer-engine/src:\
$MOONCAKE_BUILD_DIR/mooncake-transfer-engine/src/common/base:\
${LD_LIBRARY_PATH:-}"
```

构建并安装 wheel：

```bash
maturin build --release \
  --manifest-path crates/ragfs-python-native/Cargo.toml \
  --features mooncake-native

python -m pip install --force-reinstall target/wheels/ragfs_python-*.whl
```

OpenViking 启动时，`storage.agfs.cache.mooncake` 配置的 Mooncake metadata
service 和 Master 必须可用。原生 wheel 与平台相关，应在与目标部署环境兼容的
系统中构建。

生产 wheel 应使用仅在显式启用 ASan 时才链接 `libasan` 的 Mooncake revision。
构建后检查 release wheel 不包含且不依赖 `libasan`：

```bash
rm -rf /tmp/ragfs-python-wheel
python -m zipfile -e target/wheels/ragfs_python-*.whl /tmp/ragfs-python-wheel
readelf -d /tmp/ragfs-python-wheel/ragfs_python/ragfs_python.abi3.so \
  | grep libasan
find /tmp/ragfs-python-wheel -name 'libasan*'
```

两项检查都应无输出。

## 配置项

`storage.agfs.cache` 支持以下通用配置：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | bool | `false` | 是否启用 RAGFS 缓存 |
| `provider` | str | `"memory"` | `memory`、`redis`、`yuanrong` 或 `mooncake` |
| `namespace` | str | `"openviking"` | 缓存命名空间，用于隔离不同部署或租户 |
| `max_file_size_bytes` | int | `1048576` | 允许进入缓存的最大完整文件大小 |
| `bypass_prefixes` | list[str] | `[]` | 强制绕过缓存的路径前缀 |

Redis 配置：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `mode` | `"standalone"` | Redis 部署模式 |
| `endpoints` | `["redis://127.0.0.1:6379"]` | Redis 连接地址 |
| `username` | `""` | Redis ACL 用户名 |
| `password_env` | `""` | 存放 Redis 密码的环境变量名 |
| `pool_size` | `32` | 命令并发数 |
| `connect_timeout_ms` | `1000` | 连接超时 |
| `command_timeout_ms` | `20` | 命令超时 |
| `key_prefix` | `"ragfs-cache"` | Redis 侧 key 前缀 |
| `default_ttl_seconds` | `3600` | 默认 TTL；`0` 表示不设置 TTL |
| `read_from_replica` | `false` | standalone 模式下必须为 `false` |

Yuanrong 配置：

```json
{
  "storage": {
    "agfs": {
      "cache": {
        "enabled": true,
        "provider": "yuanrong",
        "yuanrong": {
          "host": "127.0.0.1",
          "port": 31501,
          "connect_timeout_ms": 5000,
          "request_timeout_ms": 5000,
          "sdk_concurrency": 4
        }
      }
    }
  }
}
```

Mooncake 配置：

```json
{
  "storage": {
    "agfs": {
      "cache": {
        "enabled": true,
        "provider": "mooncake",
        "mooncake": {
          "local_hostname": "127.0.0.1",
          "metadata_server": "http://127.0.0.1:8080/metadata",
          "master_server_addr": "127.0.0.1:50051",
          "protocol": "tcp",
          "device_name": "",
          "global_segment_size": 536870912,
          "local_buffer_size": 134217728,
          "replica_num": 2,
          "sdk_concurrency": 4,
          "operation_timeout_ms": 5000
        }
      }
    }
  }
}
```

## 整体架构

RAGFS 将缓存拆成两层：

- `CachedFileSystem`：实现文件系统语义，包括 cache hit/miss、backend 回源、回填、失效、generation 校验和指标。
- `CacheProvider`：只负责缓存对象的 `get`、`put`、`delete`、批量读写和关闭。

调用关系：

```text
OpenViking
  -> RAGFS / MountableFS
  -> CachedFileSystem
       |-> CacheProvider -> Memory / Redis / Yuanrong / Mooncake
       `-> Backend FileSystem
```

这种边界让文件、目录、rename、递归删除和写后失效逻辑只在公共层实现。Provider 不需要理解路径语义，只需要稳定存取 key-value 对象。

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

1. 本地用 `memory` 验证配置形态。
2. 用 `redis` 验证真实远程缓存收益。
3. 对高性能环境再接入 `yuanrong` 或 `mooncake`。
4. 先缓存摘要文件和 raw `read_dir`，再扩展到更多普通小文件。
5. 将锁、控制面和权限敏感路径加入 `bypass_prefixes`。

一句话总结：RAGFS 缓存负责“按文件系统语义正确失效”，Provider 负责“把缓存对象放在哪里”。只要 backend 是事实来源，缓存命中就必须先通过 envelope 和 generation 校验。
