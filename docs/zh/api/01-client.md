# 客户端

OpenViking 客户端是所有操作的主入口。

## 部署模式

| 模式 | 说明 | 使用场景 |
|------|------|----------|
| **嵌入式** | 本地存储，单例实例 | 开发环境、小型应用 |
| **服务** | 远程存储服务，多实例 | 生产环境、多进程 |

## API 参考

### OpenViking()

创建 OpenViking 客户端实例。

**签名**

```python
def __init__(
    self,
    path: Optional[str] = None,
    vectordb_url: Optional[str] = None,
    agfs_url: Optional[str] = None,
    user: Optional[str] = None,
    config: Optional[OpenVikingConfig] = None,
    **kwargs,
)
```

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| path | str | 否* | None | 本地存储路径（嵌入式模式） |
| vectordb_url | str | 否* | None | 远程 VectorDB 服务 URL（服务模式） |
| agfs_url | str | 否* | None | 远程 AGFS 服务 URL（服务模式） |
| user | str | 否 | None | 用户名，用于会话管理 |
| config | OpenVikingConfig | 否 | None | 高级配置对象 |

*必须提供 `path`（嵌入式模式）或同时提供 `vectordb_url` 和 `agfs_url`（服务模式）。

**示例：嵌入式模式**

```python
import openviking as ov

# 使用本地存储创建客户端
client = ov.OpenViking(path="./my_data")
client.initialize()

# 使用客户端...
results = client.find("测试查询")
print(f"找到 {results.total} 个结果")

client.close()
```

**示例：服务模式**

```python
import openviking as ov

# 连接远程服务
client = ov.OpenViking(
    vectordb_url="http://vectordb.example.com:8000",
    agfs_url="http://agfs.example.com:8001",
)
client.initialize()

# 使用客户端...
client.close()
```

**示例：使用配置对象**

```python
import openviking as ov
from openviking.utils.config import (
    OpenVikingConfig,
    StorageConfig,
    AGFSConfig,
    VectorDBBackendConfig
)

config = OpenVikingConfig(
    storage=StorageConfig(
        agfs=AGFSConfig(
            backend="local",
            path="./custom_data",
        ),
        vectordb=VectorDBBackendConfig(
            backend="local",
            path="./custom_data",
        )
    )
)

client = ov.OpenViking(config=config)
client.initialize()

# 使用客户端...
client.close()
```

---

### initialize()

初始化存储和索引。必须在使用其他方法前调用。

**签名**

```python
def initialize(self) -> None
```

**参数**

无。

**返回值**

| 类型 | 说明 |
|------|------|
| None | - |

**示例**

```python
client = ov.OpenViking(path="./data")
client.initialize()  # 任何操作前必须调用
```

---

### close()

关闭客户端并释放资源。

**签名**

```python
def close(self) -> None
```

**参数**

无。

**返回值**

| 类型 | 说明 |
|------|------|
| None | - |

**示例**

```python
client = ov.OpenViking(path="./data")
client.initialize()

# ... 使用客户端 ...

client.close()  # 清理资源
```

---

### wait_processed()

等待所有待处理的资源处理完成。

**签名**

```python
def wait_processed(self, timeout: float = None) -> Dict[str, Any]
```

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| timeout | float | 否 | None | 超时时间（秒） |

**返回值**

| 类型 | 说明 |
|------|------|
| Dict[str, Any] | 每个队列的处理状态 |

**返回结构**

```python
{
    "queue_name": {
        "processed": 10,      # 已处理数量
        "error_count": 0,     # 错误数量
        "errors": []          # 错误详情
    }
}
```

**示例**

```python
import openviking as ov

client = ov.OpenViking(path="./data")
client.initialize()

# 添加资源
client.add_resource("./docs/")

# 等待处理完成
status = client.wait_processed(timeout=60)
print(f"处理完成: {status}")

client.close()
```

---

### reset()

重置单例实例。主要用于测试。

**签名**

```python
@classmethod
def reset(cls) -> None
```

**参数**

无。

**返回值**

| 类型 | 说明 |
|------|------|
| None | - |

**示例**

```python
# 重置单例（用于测试）
ov.OpenViking.reset()
```

---

## 调试方法

系统健康监控和组件状态相关内容，请参阅 [调试 API](./07-debug.md)。

**快速参考**

```python
# 快速健康检查
if client.is_healthy():
    print("系统正常")

# 通过 observer 访问组件状态
print(client.observer.vikingdb)
print(client.observer.queue)
print(client.observer.system)
```

---

## 单例行为

嵌入式模式使用单例模式：

```python
# 返回相同实例
client1 = ov.OpenViking(path="./data")
client2 = ov.OpenViking(path="./data")
assert client1 is client2  # True
```

服务模式每次创建新实例：

```python
# 不同实例
client1 = ov.OpenViking(vectordb_url="...", agfs_url="...")
client2 = ov.OpenViking(vectordb_url="...", agfs_url="...")
assert client1 is not client2  # True
```

## 错误处理

```python
import openviking as ov

client = ov.OpenViking(path="./data")

try:
    client.initialize()
except RuntimeError as e:
    print(f"初始化失败: {e}")

try:
    content = client.read("viking://invalid/path/")
except FileNotFoundError:
    print("资源未找到")

client.close()
```

## 相关文档

- [资源管理](resources.md) - 资源管理
- [检索](retrieval.md) - 搜索操作
- [会话管理](sessions.md) - 会话管理
- [配置](../configuration/configuration.md) - 配置选项
