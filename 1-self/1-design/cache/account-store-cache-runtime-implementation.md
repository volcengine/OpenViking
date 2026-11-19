# Account Store CacheRuntime Implementation

## 变更摘要

将 `redis_mysql` Account Store 的认证缓存接入 RAGFS 命名
CacheRuntime，删除其独立的 `redis-py` 连接与事件循环。

## 改动预估

预计代码改动不超过 480 行，不含中英文文档。初始估计为 420 行；审查后增加了
命名 Runtime 句柄稳定性和配置文件优先级两个回归边界。范围包括 Python 源码、测试和
`pyproject.toml`；不修改 Rust 源码、MySQL DDL 或迁移脚本。

## 影响范围

| 范围 | 影响 |
| --- | --- |
| Account Store | `redis_mysql` 使用注入的 CacheRuntime。 |
| 配置 | Redis 连接参数迁移到顶层 `cache.instances`。 |
| RAGFS 启动 | Account Store 成为 Runtime 启动消费者。 |
| Python 依赖 | 删除 `redis-mysql` extra，复用 `mysql` extra。 |
| 兼容性 | Account Store 使用当前 `cache.instance` 配置 schema。 |
| 数据 | MySQL 表、API 和缓存 key 格式不变。 |
| 部署 | Gateway、expiry 和其 Redis 依赖不变。 |

## 代码扫描证据

### 搜索关键词和命中

| 关键词 | 命中文件 | 关键符号或结论 |
| --- | --- | --- |
| `redis_mysql` | `/Users/bytedance/project/OpenViking/openviking/server/account_stores/redis_mysql.py` | `RedisMySQLAccountStore` 当前创建 `redis-py` client。 |
| `build_account_store` | `/Users/bytedance/project/OpenViking/openviking/server/store_assembly.py` | 根据 provider 创建 Account Store。 |
| `build_api_key_manager` | `/Users/bytedance/project/OpenViking/openviking/server/auth/plugins/api_key.py`、`trusted.py` | 两个认证模式都传入 `service.viking_fs`。 |
| `cache_runtime` | `/Users/bytedance/project/OpenViking/openviking/pyagfs/async_client.py`、`crates/ragfs-python/src/lib.rs` | AGFS binding 可按实例名返回同进程 Runtime 句柄。 |
| `runtime_enabled` | `/Users/bytedance/project/OpenViking/openviking/utils/agfs_utils.py` | QueueFS、PathLock、coordination 和多写决定是否创建 Runtime。 |
| `QueueFS` / `PathLock` | `/Users/bytedance/project/OpenViking/openviking_cli/utils/config/agfs_config.py`、`crates/ragfs/src/core/builder.rs` | 业务模块选择 `instance`，顶层 `cache.instances` 保存 Provider 参数。 |
| `redis-mysql` | `/Users/bytedance/project/OpenViking/pyproject.toml` | 当前存在与 `mysql` 重复的额外依赖。 |

### 调用链

```text
ov.conf
  -> ServerConfig
  -> create_app() registers ServerConfig
  -> OpenVikingConfig
  -> OpenVikingService._build_ragfs_binding_config()
  -> build_runtime_ragfs_binding_config()
  -> RagfsBindingConfig.to_binding_dict()
  -> RAGFSBindingClient creates named CacheRuntime
  -> ApiKeyAuthPlugin.initialize() / TrustedAuthPlugin.initialize()
  -> build_api_key_manager()
  -> build_account_store()
  -> RedisMySQLAccountStore(cache_runtime=...)
```

### 现有接口和数据

| 位置 | 当前接口或数据 |
| --- | --- |
| `RedisMySQLAccountStore.__init__` | 接收 `service_name`、`params`、可选 `mysql_store`、必填 `cache_runtime`。 |
| `params.cache` | 只接受 `instance`，选择顶层命名 Runtime。 |
| `CacheRuntimeHandleProtocol` | 提供 `ping()`、`register_script()`、`execute_script()`、KV、Hash、Set、ZSet 和 List 操作。 |
| `RagfsBindingConfig.to_binding_dict` | 通过 `runtime_enabled` 请求 RAGFS 创建命名 Runtime。 |
| `tests/server/test_redis_mysql_account_store.py` | 真实 MySQL/Redis 合约测试，通过 RAGFS CacheRuntime 访问 Redis。 |
| `tests/misc/test_config_validation.py` | 命名 Runtime 和 Account Store 选择配置测试。 |

未找到 Account Store 对 RAGFS CacheRuntime 的现有消费者。已搜索关键词：
`RedisMySQLAccountStore`、`redis_mysql`、`cache_runtime`、
`build_account_store`、`runtime_enabled`。

## 配置结构

### `server.account_store.params.cache`

用途：选择 Account Store 使用的命名 Runtime。
所有者：`RedisMySQLAccountStore`。
生命周期：Server 启动时读取，进程生命周期内不可热更新。
存储位置：`ov.conf` JSON。

| 字段 | 类型 | 必填 | 默认值 | 约束 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `instance` | string | 是 | `"default"` | `[A-Za-z0-9_-]+`，必须存在于 `cache.instances` | 选择 RAGFS Runtime。 |

`cache.instances.<instance>.params.default_ttl_seconds` 是认证 payload 的
TTL。`redis_mysql` 要求该值为正整数。generation key 不设置 TTL。
Account Store 直接固定 `v2` key 前缀为 `"ov:account-cache"`，不提供配置覆盖。

完整示例：

```json
{
  "cache": {
    "instances": {
      "default": {
        "provider": "redis",
        "params": {
          "endpoints": ["redis://redis.example.com:6379"]
        }
      },
      "accountstore": {
        "provider": "redis",
        "params": {
          "endpoints": ["rediss://account-cache.example.com:6379"],
          "username": "openviking",
          "password_env": "OPENVIKING_ACCOUNT_REDIS_PASSWORD",
          "pool_size": 32,
          "connect_timeout_ms": 5000,
          "command_timeout_ms": 5000,
          "default_ttl_seconds": 60
        }
      }
    }
  },
  "server": {
    "account_store": {
      "provider": "redis_mysql",
      "params": {
        "mysql": {
          "host": "mysql.example.com",
          "user": "openviking",
          "password": "secret",
          "database": "openviking"
        },
        "cache": {
          "instance": "accountstore"
        }
      }
    }
  }
}
```

不新增数据库表、缓存表、SQL、DDL 或数据迁移。

## 接口改动

### `RedisMySQLAccountStore`

调用方：`build_account_store()`。
被调用方：`CacheRuntimeHandleProtocol`。

目标构造签名：

```python
def __init__(
    self,
    *,
    service_name: str,
    params: dict[str, object] | None = None,
    mysql_store: MySQLAccountStore | None = None,
    cache_runtime: CacheRuntimeHandleProtocol | None = None,
) -> None:
```

输入约束：

- `params.mysql` 保持现有 MySQL 参数要求。
- `params.cache` 必须为 object，且只允许 `instance`。
- `cache_runtime` 由 Server 注入；直接构造测试必须显式注入。
- `cache_runtime` 为空时拒绝启动，不回退到 `redis-py`。
- `cache_runtime.default_ttl_seconds` 必须是正整数秒；未配置或为零时拒绝启动。

输出和副作用：

- 构造期间注册三个命名 Lua 脚本。注册只在进程内保存
  `script_id -> Lua source`，不执行网络 I/O；同一 ID 重复注册相同脚本幂等，
  不同脚本报错。
- 后续业务调用只传 `script_id`、keys 和 bytes args，不重复传递 Lua source。
  Redis provider 首次执行该 ID 时 `SCRIPT LOAD` 并缓存 SHA，后续执行
  `EVALSHA`。Redis 重启、`SCRIPT FLUSH` 或节点缺脚本导致 `NOSCRIPT` 时，
  Runtime 重新加载脚本并重试一次。
- `load()` 调用 MySQL `load()` 和 CacheRuntime `ping()`。
- 条件写入从 `cache_runtime.default_ttl_seconds` 读取 TTL，并作为 Lua
  `SET ... EX` 的参数传入。`execute_script()` 不会自动给 Lua 内部写入加
  Runtime 默认 TTL。
- `close()` 只取消 Store 自己的 fill task 并关闭 MySQL；不得关闭
  CacheRuntime。既有私有 Python loop 仅保留为跨 event loop 的单飞和关闭协调。
- Redis 失败继续走 MySQL；MySQL 成功后的缓存失效仍为 best-effort。

幂等与并发：

- 重复脚本注册仅接受相同脚本文本。
- generation 校验和条件写入继续由 Lua 脚本在一个 Redis slot 原子执行。
- 同一进程内使用现有 single-flight 映射合并相同 cache fill。

### 伪代码

```text
function build_runtime_ragfs_binding_config(config):
    selector = none
    if config.server.account_store.provider == "redis_mysql":
        cache = config.server.account_store.params["cache"]
        validate cache is object
        selector = cache.get("instance", "default")

    return RagfsBindingConfig(
        agfs=config.storage.agfs,
        cache=config.cache,
        account_store_cache_instance=selector,
        ...
    )

function RagfsBindingConfig.to_binding_dict():
    uses_runtime = existing_queuefs_or_pathlock_or_coordination_or_backup_consumer
    if account_store_cache_instance is not none:
        selected = require_cache_instance(cache, account_store_cache_instance, "Account Store")
        if selected.provider != "redis":
            raise ValueError("redis_mysql requires ... provider='redis'")
        uses_runtime = true

    if uses_runtime and cache is none:
        raise ValueError("top-level cache config is required ...")
    return binding_dict(cache.runtime_enabled=uses_runtime)

function build_account_store(..., viking_fs, params):
    if provider != "redis_mysql":
        preserve existing branches
    selector = parse_account_store_cache(params).instance
    runtime = viking_fs.agfs.cache_runtime(selector)
    return RedisMySQLAccountStore(
        service_name=service_name,
        params=params,
        cache_runtime=runtime,
    )

function RedisMySQLAccountStore(..., cache_runtime):
    require cache_runtime.default_ttl_seconds is a positive integer
    key_prefix = "ov:account-cache"
    register accountstore.v2 scripts

function set_if_generations(...):
    ttl = cache_runtime.default_ttl_seconds
    execute accountstore.v2.set_if_generations with ttl in ARGV
    -- Lua keeps generation keys persistent and writes only payload with SET EX ttl

function RedisMySQLAccountStore.load():
    await mysql.load()
    await cache_runtime.ping()

function read_generations(...):
    result = await cache_runtime.execute_script(
        "accountstore.v2.read_generations",
        generation_keys + [payload_key],
        encoded_tokens_and_flags,
    )
    validate and decode result
    return result

function close():
    cancel and await local fill tasks
    await mysql.close()
    do not close cache_runtime
```

## 变更点

### 1. RAGFS Runtime 启动选择

- 变更类型：修改。
- 目标文件与符号：
  `/Users/bytedance/project/OpenViking/openviking/utils/agfs_utils.py`，
  `RagfsBindingConfig`、`RagfsBindingConfig.to_binding_dict()`、
  `build_runtime_ragfs_binding_config()`。
- 当前实现：只由 CacheFS、QueueFS、PathLock、coordination 和多写决定
  `runtime_enabled`。
- 目标实现：新增可选 `account_store_cache_instance`。该字段存在时复用
  `_require_cache_instance()`，要求 provider 为 `redis`，并启用 Runtime。
- 验证方式：配置测试断言仅启用 Account Store 时
  `binding["cache"]["runtime_enabled"] is True`；未知和 Dynamic 实例报错。

### 2. Account Store 装配

- 变更类型：修改。
- 目标文件与符号：
  `/Users/bytedance/project/OpenViking/openviking/server/store_assembly.py`，
  `build_account_store()`。
- 当前实现：`redis_mysql` 仅传入 `service_name` 和 `params`。
- 目标实现：解析 Account Store `cache.instance`，通过
  `viking_fs.agfs.cache_runtime(instance)` 取得句柄并注入。
- 验证方式：测试 fake `VikingFS.agfs` 返回命名 Runtime；缺失 VFS、缺失
  Runtime 或错误实例均失败。

### 3. Account Store CacheRuntime 适配

- 变更类型：修改。
- 目标文件与符号：
  `/Users/bytedance/project/OpenViking/openviking/server/account_stores/redis_mysql.py`，
  `RedisMySQLAccountStore.__init__()`、`load()`、`close()`、
  `_read_generations()`、`_set_if_generations()`、`_bump_generation()`。
- 当前实现：调用 `create_redis_client()`、私有操作 loop 和 `client.eval()`。
- 目标实现：注入 CacheRuntime，注册三个 `accountstore.v2.*` 脚本，用
  `execute_script()` 替代 `eval()`，保留私有操作 loop 的跨 event loop
  协调职责以及现有 payload 与 generation 解析。payload TTL 从
  `cache_runtime.default_ttl_seconds` 读取；key 前缀固定为
  `ov:account-cache`。
- 验证方式：真实 Redis 合约测试覆盖 cold read、TTL payload hit、用户 key
  轮换、删除失效和 Redis 失败回源。

### 4. Python Redis client 生命周期删除

- 变更类型：修改。
- 目标文件：
  `/Users/bytedance/project/OpenViking/openviking/server/account_stores/redis_mysql.py`。
- 当前实现：Account Store 创建、ping 和关闭独立 Python Redis client。
- 目标实现：删除 client、client handle 和连接配置；保留私有操作 loop 的
  单飞与关闭协调，但 Runtime 生命周期由 RAGFS 持有。
- 验证方式：搜索 `redis_mysql.py` 无 `create_redis_client`、
  `RedisClientHandle` 或 `redis.asyncio`；Account Store close 后共享 Runtime
  仍能 `ping()`。

### 5. 依赖、测试和文档

- 变更类型：修改。
- 目标文件：
  `/Users/bytedance/project/OpenViking/pyproject.toml`，
  `/Users/bytedance/project/OpenViking/tests/server/test_redis_mysql_account_store.py`，
  `/Users/bytedance/project/OpenViking/tests/server/test_store_assembly.py`，
  `/Users/bytedance/project/OpenViking/tests/server/test_account_services.py`，
  `/Users/bytedance/project/OpenViking/tests/misc/test_config_validation.py`，
  `/Users/bytedance/project/OpenViking/docs/zh/guides/01-configuration.md`，
  `/Users/bytedance/project/OpenViking/docs/en/guides/01-configuration.md`。
- 当前实现：`redis_mysql` 使用 `mysql` extra；测试和文档使用
  `cache.instances` + `cache.instance`，并在选中 Runtime 配置
  `default_ttl_seconds`。
- 验证方式：运行目标 pytest 测试，检查文档示例只声明当前配置字段。

## 实施步骤

1. 在 `tests/misc/test_config_validation.py` 写入失败用例。
   - 创建 `redis_mysql` 配置和 `accountstore` Redis 实例。
   - 断言 `runtime_enabled` 为 true。
   - 运行：

```bash
.venv/bin/python -m pytest tests/misc/test_config_validation.py -q
```

   - 预期：新用例因 Runtime 尚未被 Account Store 选择而失败。

2. 修改 `openviking/utils/agfs_utils.py`。
   - 向 `RagfsBindingConfig` 增加 selector。
   - 在 `to_binding_dict()` 按 PathLock 的方式校验 Redis 实例。
   - 重新运行步骤 1 命令，预期通过。

3. 在 `tests/server/test_store_assembly.py` 写入失败用例。
   - 构造 `redis_mysql` params 和有 `cache_runtime("accountstore")` 的 fake AGFS。
   - 断言装配后的 Store 保存同一 Runtime 句柄。
   - 运行：

```bash
.venv/bin/python -m pytest tests/server/test_store_assembly.py -q
```

   - 预期：失败，因为装配层尚未注入 Runtime。

4. 修改 `openviking/server/store_assembly.py`。
   - 解析 `cache.instance`。
   - 从 `viking_fs.agfs` 获取 Runtime 并注入 Store。
   - 重新运行步骤 3 命令，预期通过。

5. 在 `tests/server/test_redis_mysql_account_store.py` 写入失败用例。
   - 通过 RAGFS binding 的 `cache_runtime("accountstore")` 构造两个 Store。
   - 验证 MySQL mutation 后另一个 Store 不再接受旧 API key。
   - 运行：

```bash
.venv/bin/python -m pytest tests/server/test_redis_mysql_account_store.py -q
```

   - 预期：失败，因为 Store 仍调用 `redis.asyncio`。

6. 修改 `openviking/server/account_stores/redis_mysql.py`。
   - 用注入 Runtime 替换 client、handle 和私有 loop。
   - 注册 `accountstore.v2.*` 脚本。
   - 将 Lua 参数转为 bytes 后调用 `execute_script()`。
   - `close()` 不关闭 Runtime。
   - 重新运行步骤 5 命令，预期通过。

7. 删除 `redis_mysql.py` 中的 Redis client 生命周期。
   - 搜索确认没有直接 Redis client 引用：

```bash
rg -n 'create_redis_client|RedisClientHandle|redis\.asyncio' \
  openviking/server/account_stores/redis_mysql.py
```

   - 预期：无命中；保留 `redis_runtime.py` 作为跨 event loop 协调器。

8. 修改 `pyproject.toml`。
   - 删除 `redis-mysql` extra。
   - `redis_mysql` provider 复用 `mysql` extra。

9. 修改测试和文档。
   - 更新 `test_account_services.py` 中构造参数。
   - 中英文配置示例迁移到 `cache.instances`。
   - 说明 `redis_mysql` 使用 RAGFS Redis Runtime 和 `mysql` extra。

10. 执行回归验证。

```bash
.venv/bin/python -m pytest tests/misc/test_config_validation.py \
  tests/server/test_store_assembly.py \
  tests/server/test_account_services.py -q
.venv/bin/python -m pytest tests/server/test_redis_mysql_account_store.py -q
```

   - 第二条命令需要 `OV_TEST_MYSQL` 和 `OV_TEST_REDIS`；未设置时记录 skip。

## 验证方案

| 场景 | 位置 | 断言 |
| --- | --- | --- |
| 缓存命中 | `test_redis_mysql_account_store.py` | MySQL 离线时可读取有效 payload。 |
| API key 轮换 | `test_redis_mysql_account_store.py` | generation bump 拒绝旧 key。 |
| Redis 不可用 | `test_redis_mysql_account_store.py` | 读取回源 MySQL，不写坏 cache。 |
| Store 关闭 | `test_redis_mysql_account_store.py` | Store 关闭后 Runtime 仍可用。 |
| 回滚 | 手工 | 回滚 Server 镜像和匹配的完整部署配置。 |

## 风险与回滚

| 风险 | 触发条件 | 监控或症状 | 应急处理 |
| --- | --- | --- | --- |
| Runtime 未创建 | 忘记配置 `cache.instances` | 启动失败，错误包含实例名 | 补齐顶层 cache 配置。 |
| 错误 Provider | `instance` 指向 DynamicProvider | 启动失败 | 指向 Redis 实例。 |
| 脚本返回转换错误 | Lua result bytes/int 解析不一致 | 鉴权回源增多或认证失败 | 回滚镜像；MySQL 仍是权威来源。 |

回滚步骤：

1. 将 Server 镜像和完整部署配置一起回滚到上一个已验证版本。
2. 重启 Server。
3. 不回滚 MySQL；本次不写 MySQL schema 或数据。
