# 基础使用示例：OpenViking Python SDK

这个示例的目标很明确：用最短路径带你理解 OpenViking Python SDK 的核心工作流。
你会从初始化客户端开始，完成资源导入、`viking://` 文件系统浏览、上下文检索，
以及创建一个后续可以提交为长期记忆的会话。

它是一个典型的 SDK 入门示例。如果你要做生产部署、共享服务或者 MCP 集成，
请把它当作基础，然后继续看下方链接到的服务端和 MCP 文档。

## 这个示例覆盖什么

- 本地快速试用时的嵌入式 SDK 用法
- 服务端模式下的 HTTP 客户端用法
- 从远程 URL 导入资源
- 使用 `ls`、`tree`、`read` 浏览 `viking://` 文件系统
- 使用 `find`、`abstract`、`overview`、`grep` 做检索和加载
- 创建 session 并追加消息，为后续记忆提取做准备

## 先选对接入方式

目前 OpenViking 常见有三种接入路径：

| 模式 | 适合场景 | 是否推荐 |
|------|----------|----------|
| 嵌入式 SDK | 单进程、本地试用、快速验证 | 是，适合第一次上手 |
| HTTP 服务端 + SDK/CLI | 共享服务、多会话、多 Agent | 是，正式使用优先 |
| MCP | Claude Code、Cursor、Claude Desktop、OpenClaw 等 MCP 宿主 | 是，工具化集成优先 |

如果不是单进程本地 demo，而是要长期运行或多端接入，优先使用 HTTP 服务端模式。  
如果你是给 Claude Code、Cursor 这类客户端接入，请直接看 [MCP 集成指南](../../docs/zh/guides/06-mcp-integration.md)。

## 前置条件

1. Python 3.10+
2. 安装 OpenViking：

```bash
pip install openviking --upgrade --force-reinstall
```

3. 准备好 `~/.openviking/ov.conf`

## 快速开始

### 1. 运行示例脚本

```bash
git clone https://github.com/volcengine/OpenViking.git
cd OpenViking/examples/basic-usage
python basic_usage.py
```

脚本默认使用嵌入式模式：

```python
import openviking as ov

client = ov.OpenViking(path="./data")
client.initialize()
```

如果你想把同样的流程切到服务端模式，改成：

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933")
client.initialize()
```

服务端的推荐启动方式见 [快速开始：服务端模式](../../docs/zh/getting-started/03-quickstart-server.md)。

### 2. 脚本演示了什么

`basic_usage.py` 基本覆盖了大多数应用的第一条链路：

1. 初始化客户端并检查健康状态。
2. 从 URL 添加一个资源。
3. 查看生成的 `viking://resources/...` 树。
4. 等待语义处理完成。
5. 用 `abstract`、`overview`、`read` 加载 L0/L1/L2 上下文。
6. 用 `find` 做语义检索。
7. 用 `grep` 做字面内容检索。
8. 创建 session 并追加消息，为记忆提取做准备。

## 代码说明

### 初始化

本地首次试用建议先用嵌入式模式：

```python
import openviking as ov

client = ov.OpenViking(path="./data")
client.initialize()
```

如果 OpenViking 作为独立服务运行，则使用 HTTP 客户端：

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933")
client.initialize()
```

如果服务端启用了认证，普通数据访问请优先使用 `user_key`：

```python
client = ov.SyncHTTPClient(
    url="http://localhost:1933",
    api_key="<user-key>",
    agent_id="my-agent",
)
```

`root_key` 主要用于管理操作。它不能直接调用 `add_resource`、`find`、`ls` 这类租户级 API，
除非同时显式传入 `account` 和 `user`。详见
[认证文档](../../docs/zh/guides/04-authentication.md) 和
[快速开始：服务端模式](../../docs/zh/getting-started/03-quickstart-server.md)。

### 添加资源

你可以添加 URL、本地文件、目录：

```python
result = client.add_resource(
    path="https://example.com/docs",
    wait=False,
)

result = client.add_resource(path="/path/to/manual.pdf")

result = client.add_resource(
    path="/path/to/repo",
    instruction="这是一个 Python Web 应用",
)
```

脚本和 demo 可以直接用 `wait=True`。  
真正的服务里更常见的做法是异步导入，等到你确实需要结果时再调用 `wait_processed()`。

### 文件系统访问

OpenViking 的上下文统一组织在虚拟文件系统里：

```python
files = client.ls("viking://resources/")
tree = client.tree("viking://resources/my-project", level_limit=3)
content = client.read("viking://resources/my-project/README.md")
```

同样的 URI 模型也适用于记忆和技能：

- `viking://resources/`
- `viking://user/memories/`
- `viking://agent/memories/`
- `viking://agent/skills/`

### 检索

`find` 适合快速语义检索，`search` 适合更复杂的高级检索：

```python
results = client.find(
    query="认证逻辑是怎么做的",
    target_uri="viking://resources/my-project",
    limit=5,
)

results = client.search(
    query="数据库配置和故障处理",
    target_uri="viking://resources/",
    limit=10,
)
```

检索命中后，再按需做分层加载：

```python
uri = "viking://resources/my-project/docs/api.md"

abstract = client.abstract(uri)
overview = client.overview(uri)
content = client.read(uri)
```

如果你要的是字面匹配而不是语义检索，用 `grep`：

```python
result = client.grep("viking://resources/my-project", "Agent", case_insensitive=True)
matches = result.get("matches", [])
```

### Session 与长期记忆

示例脚本会创建一个 session 并追加消息：

```python
session_info = client.create_session()
session_id = session_info["session_id"]

client.add_message(session_id, "user", "我更喜欢 TypeScript 而不是 JavaScript")
client.add_message(session_id, "assistant", "明白了，在合适场景下我会优先使用 TypeScript。")
```

如果要把这段对话真正提取成长期记忆，需要提交 session：

```python
client.commit_session(session_id)
```

提交后，记忆可以通过正常检索接口再次找回：

```python
memories = client.find(
    query="用户编程偏好",
    target_uri="viking://user/memories/",
)
```

## 配置说明

创建 `~/.openviking/ov.conf`，至少需要存储、Embedding、VLM 配置。一个最小本地配置示例如下：

```json
{
  "server": { "host": "127.0.0.1", "port": 1933 },
  "storage": {
    "workspace": "~/.openviking/data"
  },
  "embedding": {
    "dense": {
      "provider": "openai",
      "api_key": "your-api-key",
      "model": "text-embedding-3-large",
      "dimension": 3072
    }
  },
  "vlm": {
    "provider": "openai",
    "api_key": "your-api-key",
    "model": "gpt-4o"
  }
}
```

你也可以使用火山引擎、Azure OpenAI 等提供商。当前配置示例请以主 [README](../../README_CN.md) 和 [配置指南](../../docs/zh/guides/01-configuration.md) 为准。

## 推荐下一步

- [配置指南](../../docs/zh/guides/01-configuration.md)：先确认当前配置模型，再过渡到共享部署。
- [快速开始：服务端模式](../../docs/zh/getting-started/03-quickstart-server.md)：正确启动 `openviking-server`。
- [MCP 集成指南](../../docs/zh/guides/06-mcp-integration.md)：接入 Claude Code、Cursor、Claude Desktop、OpenClaw 等 MCP 宿主。
- [Claude Code 记忆插件](../claude-code-memory-plugin/README.md)：在 Claude Code 中使用 OpenViking 长期记忆。
- [OpenCode 记忆插件](../opencode-memory-plugin/README_CN.md)：在 OpenCode 中使用 OpenViking 记忆工具。
- [OpenClaw 插件](../openclaw-plugin/README_CN.md)：与 OpenClaw 集成。

## 常见问题

| 问题 | 排查方向 |
|------|----------|
| `ImportError` 或本地扩展问题 | 重新安装 `openviking`；如果是源码开发，确认本地构建依赖齐全。 |
| HTTP 模式下 `Connection refused` | 启动 `openviking-server`，并检查 `http://localhost:1933/health`。 |
| 租户或认证报错 | 普通数据接口优先使用 `user_key`；`root_key` 仅在显式传入租户信息时使用。 |
| 刚导入后检索慢或搜不到 | 等待 `wait_processed()`，或在导入时直接用 `wait=True`。 |
| 多个客户端或会话争用本地存储 | 不要反复起独立本地进程，改用 HTTP 服务端模式。 |

## 许可证

Apache License 2.0
