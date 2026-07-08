# openviking-sdk

OpenViking 的轻量级 Python HTTP SDK。

`openviking-sdk` 面向只需要通过 HTTP 调用现有 OpenViking 服务的用户。它避免了主包 `openviking` 中较重的本地运行时、服务端和 CLI 依赖。

## 安装

```bash
pip install openviking-sdk
```

要求：

- Python 3.10+
- 一个可访问的 OpenViking HTTP 服务，例如 `http://127.0.0.1:1933`

## 包名与导入名

- PyPI 包名：`openviking-sdk`
- Python 导入名：`openviking_sdk`

```python
from openviking_sdk import AsyncHTTPClient, SyncHTTPClient
```

## 配置来源

SDK 支持三种配置方式，优先级从高到低如下：

1. 显式构造参数
2. 环境变量，例如 `OPENVIKING_URL`、`OPENVIKING_API_KEY`、`OPENVIKING_ACCOUNT`、`OPENVIKING_USER`、`OPENVIKING_ACTOR_PEER_ID` 和 `OPENVIKING_TIMEOUT`
3. `ovcli.conf`，来源可以是 `OPENVIKING_CLI_CONFIG_FILE` 指定的路径，或者默认路径 `~/.openviking/ovcli.conf`

这意味着之前依赖 `ovcli.conf` 的配置方式，在 SDK 拆分之后仍然可以继续使用。

## 认证模型

大多数部署场景使用 API Key 认证。

常见客户端字段：

- `url`：OpenViking 服务的基础 URL
- `api_key`：root key 或 user key
- `account`：可选的 account 覆盖，通常只在使用 root key 时需要
- `user`：可选的 user 覆盖，通常只在使用 root key 时需要
- `user_id`：`user` 的兼容旧别名
- `actor_peer_id`：可选的 actor peer 覆盖
- `agent_id`：`actor_peer_id` 的兼容旧别名

兼容性说明：

- 旧调用方仍然可以使用 `user_id` 和 `agent_id`
- `actor_peer_id` 和 `agent_id` 不能同时传入

示例：

```python
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(
    url="http://127.0.0.1:1933",
    api_key="your-user-or-root-key",
)
```

如果你使用的是 root key，并且希望以某个租户用户身份执行：

```python
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(
    url="http://127.0.0.1:1933",
    api_key="your-root-key",
    account="demo-account",
    user="demo-user",
)
```

## 快速开始：同步客户端

```python
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(
    url="http://127.0.0.1:1933",
    api_key="your-user-key",
)

healthy = client.health()
print("health:", healthy)

session = client.create_session("demo-session")
print("session:", session)

client.session("demo-session").add_message("user", "hello from sdk")
context = client.session("demo-session").get_session_context(token_budget=4096)
print("context:", context)
```

## 快速开始：异步客户端

```python
import asyncio

from openviking_sdk import AsyncHTTPClient


async def main() -> None:
    client = AsyncHTTPClient(
        url="http://127.0.0.1:1933",
        api_key="your-user-key",
    )

    healthy = await client.health()
    print("health:", healthy)

    session = await client.create_session("demo-session-async")
    print("session:", session)

    session_client = client.session("demo-session-async")
    await session_client.add_message("user", "hello from async sdk")
    context = await session_client.get_session_context(token_budget=4096)
    print("context:", context)

    await client.close()


asyncio.run(main())
```

## 常见操作

### 创建 Session

```python
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(url="http://127.0.0.1:1933", api_key="your-user-key")
result = client.create_session("demo-session")
print(result)
```

### 从本地文件添加资源

`add_resource` 会自动处理本地路径对应的文件上传。

```python
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(url="http://127.0.0.1:1933", api_key="your-user-key")

result = client.add_resource(
    "/path/to/notes.md",
    to="viking://resources/demo-notes",
    reason="knowledge import",
    wait=True,
)
print(result)
```

### 文件系统操作

```python
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(url="http://127.0.0.1:1933", api_key="your-user-key")

client.mkdir("viking://resources/demo-dir")
print(client.ls("viking://resources"))
print(client.read("viking://resources/demo-dir/example.md"))
```

### 检索

```python
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(url="http://127.0.0.1:1933", api_key="your-user-key")

result = client.find("hello", limit=5)
print(result)
```

## 管理员操作

如果你使用 root key 连接，SDK 也暴露了管理员 API，例如：

- `admin_create_account`
- `admin_register_user`
- `admin_list_accounts`
- `admin_list_users`
- `admin_regenerate_key`
- `admin_delete_account`

示例：

```python
from openviking_sdk import SyncHTTPClient

root_client = SyncHTTPClient(
    url="http://127.0.0.1:1933",
    api_key="your-root-key",
)

result = root_client.admin_create_account(
    account_id="demo-account",
    admin_user_id="demo-admin",
    seed="demo-admin-seed",
)
print(result)

root_client.admin_register_user(
    account_id="demo-account",
    user_id="alice",
    role="user",
    seed="alice-seed",
    user_config={
        "add_targets": {
            "resource_uri": "viking://user/resources/project-a",
            "skill_uri": "viking://user/skills",
        }
    },
)

root_client.admin_regenerate_key(
    account_id="demo-account",
    user_id="alice",
    seed="alice-new-seed",
)
```

`admin_create_account` 也接受同样结构的 `user_config`。这些字段用于初始化服务端用户配置；普通添加调用仍然只需省略 `to` / `parent` / `target_uri`，由服务端解析默认值。
传入 `seed` 时，返回的 API Key 会基于 `sha256(user_id + "\0" + seed)` 生成；省略时仍使用随机生成逻辑。

## 错误处理

SDK 会把服务端错误码映射为 Python 异常。

```python
from openviking_sdk import OpenVikingError, SyncHTTPClient

client = SyncHTTPClient(url="http://127.0.0.1:1933", api_key="your-user-key")

try:
    print(client.read("viking://resources/not-exists.md"))
except OpenVikingError as exc:
    print(type(exc).__name__, exc)
```

## 与 `openviking` 的关系

在以下场景中使用 `openviking-sdk`：

- 只需要 HTTP 客户端
- 希望依赖体积尽可能小
- 作为业务应用侧集成包使用

在以下场景中使用 `openviking`：

- 需要完整 Python 主包
- 需要本地运行时集成
- 需要服务端入口
- 需要重新导出 HTTP client 的兼容导入路径

## 开发

从源码安装：

```bash
cd sdk/python
pip install -e .
```

构建发行包：

```bash
cd sdk/python
python -m build
```

SDK 版本号来自以下格式的 git tag：

```text
python-sdk@0.1.3
```

这个 tag 命名空间独立于主包的发布 tag，例如：

```text
v0.3.26
```

## 发布

仓库已经配置为支持通过 SDK 专用 tag 触发 SDK 发布。

典型流程：

1. 合并 SDK 相关改动
2. 创建并推送类似 `python-sdk@0.1.3` 的 tag
3. GitHub Actions 构建 `sdk/python`
4. GitHub Actions 将 `openviking-sdk` 发布到 PyPI
