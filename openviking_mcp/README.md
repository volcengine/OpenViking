# OpenViking MCP Server

OpenViking MCP Server 提供了一个标准的 MCP (Model Context Protocol) 接口，让 AI Agent 可以通过 OpenCode 访问 OpenViking 的上下文数据库功能。

## 功能特性

### 记忆管理工具
- `initialize_memory` - 初始化 OpenViking 记忆系统
- `get_status` - 获取系统状态

### 资源管理工具
- `add_resource` - 添加资源（文件/目录/URL）到记忆库
- `semantic_search` - 语义搜索已索引的资源
- `get_abstract` - 获取资源的 L0 摘要
- `get_overview` - 获取资源的 L1 概览

### 文件系统工具
- `read_content` - 读取文件完整内容
- `list_directory` - 列出目录内容
- `glob_search` - 使用 glob 模式搜索文件

### 会话管理工具
- `create_session` - 创建新的对话会话
- `add_message_to_session` - 向会话添加消息
- `commit_session` - 提交会话到持久化存储

## 安装

### 前置要求

1. **Python 3.9+**
2. **OpenViking** 已安装
3. **FastMCP** 库

```bash
# 安装 OpenViking
cd /data0/wlf/opencode_test/OpenViking
uv sync --all-extras

# 安装 FastMCP
pip install mcp
```

### 配置

1. **创建配置文件** `ov.conf`

```yaml
# VLM 配置（用于生成摘要和概览）
vlm:
  provider: volcengine
  model: doubao-pro-32k
  api_key: your_api_key_here
  base_url: https://ark.cn-beijing.volces.com/api/v3

# Embedding 模型配置
embedding:
  provider: volcengine
  model: doubao-embedding
  api_key: your_api_key_here
  base_url: https://ark.cn-beijing.volces.com/api/v3

# 存储配置
storage:
  agfs:
    backend: local
    path: ./openviking_data/agfs

  vectordb:
    backend: local
    path: ./openviking_data/vectordb
```

2. **设置环境变量**

```bash
export OPENVIKING_CONFIG_FILE=/path/to/ov.conf
export OPENVIKING_DATA_DIR=/path/to/data
export PYTHONPATH=/data0/wlf/opencode_test/OpenViking:$PYTHONPATH
```

## 在 OpenCode 中使用

### 1. 配置 OpenCode

编辑 `.opencode/opencode.jsonc`:

```json
{
  "mcp": {
    "openviking": {
      "type": "local",
      "command": ["python", "-m", "openviking_mcp"],
      "environment": {
        "OPENVIKING_CONFIG_FILE": "/data0/wlf/opencode_test/OpenViking/ov.conf",
        "OPENVIKING_DATA_DIR": "/data0/wlf/opencode_test/OpenViking/openviking_data",
        "PYTHONPATH": "/data0/wlf/opencode_test/OpenViking"
      }
    }
  }
}
```

### 2. 启动 OpenCode

```bash
cd /data0/wlf/opencode_test/opencode
bun run dev
```

OpenCode 会自动启动 OpenViking MCP Server 并注册所有工具。

### 3. 使用示例

在 OpenCode 中，AI Agent 可以直接调用这些工具：

```
用户: 请帮我添加这个项目的 README 文件到记忆库
Agent: [调用 add_resource 工具]
      path: "/path/to/README.md"
      reason: "用户请求添加项目文档"

用户: 搜索关于 API 使用的内容
Agent: [调用 semantic_search 工具]
      query: "API usage examples"
      limit: 5
```

## 工具详细说明

### initialize_memory

初始化 OpenViking 记忆系统。必须在使用其他工具前调用。

**参数**: 无

**返回**:
```json
{
  "status": "success",
  "message": "OpenViking memory system initialized successfully"
}
```

### add_resource

添加资源到记忆库。

**参数**:
- `path` (string, required): 资源路径（本地文件/目录或 URL）
- `target` (string, optional): 目标 Viking URI
- `reason` (string, optional): 添加原因
- `instruction` (string, optional): 处理指令
- `wait` (boolean, optional): 是否等待处理完成
- `timeout` (float, optional): 超时时间（秒）

**返回**:
```json
{
  "root_uri": "viking://...",
  "status": "success"
}
```

### semantic_search

语义搜索已索引的资源。

**参数**:
- `query` (string, required): 搜索查询
- `target_uri` (string, optional): 限制搜索范围
- `limit` (int, optional): 最大结果数（默认 10）
- `score_threshold` (float, optional): 最小相关度分数（0.0-1.0）

**返回**:
```json
{
  "status": "success",
  "query": "...",
  "results": [
    {
      "uri": "viking://...",
      "score": 0.95,
      "content": "..."
    }
  ]
}
```

### get_abstract

获取资源的 L0 摘要（高层次概述）。

**参数**:
- `uri` (string, required): Viking URI

**返回**:
```json
{
  "status": "success",
  "uri": "viking://...",
  "abstract": "..."
}
```

### get_overview

获取资源的 L1 概览（详细摘要）。

**参数**:
- `uri` (string, required): Viking URI

**返回**:
```json
{
  "status": "success",
  "uri": "viking://...",
  "overview": "..."
}
```

### read_content

读取文件的完整内容。

**参数**:
- `uri` (string, required): Viking URI

**返回**:
```json
{
  "status": "success",
  "uri": "viking://...",
  "content": "..."
}
```

### list_directory

列出目录内容。

**参数**:
- `uri` (string, required): Viking URI
- `recursive` (boolean, optional): 是否递归列出（默认 false）

**返回**:
```json
{
  "status": "success",
  "uri": "viking://...",
  "entries": [...]
}
```

### glob_search

使用 glob 模式搜索文件。

**参数**:
- `pattern` (string, required): Glob 模式（如 "**/*.py"）
- `uri` (string, optional): 基础 URI（默认 "viking://"）

**返回**:
```json
{
  "status": "success",
  "pattern": "**/*.py",
  "matches": ["viking://...", ...]
}
```

### create_session

创建新的对话会话。

**参数**:
- `session_id` (string, optional): 会话 ID（自动生成如果未提供）

**返回**:
```json
{
  "status": "success",
  "session_id": "...",
  "user": "..."
}
```

### add_message_to_session

向会话添加消息。

**参数**:
- `session_id` (string, required): 会话 ID
- `role` (string, required): 角色（"user", "assistant", "system"）
- `content` (string, required): 消息内容

**返回**:
```json
{
  "status": "success",
  "session_id": "...",
  "message_count": 5
}
```

### commit_session

提交会话到持久化存储。

**参数**:
- `session_id` (string, required): 会话 ID

**返回**:
```json
{
  "status": "success",
  "session_id": "...",
  "message": "Session committed successfully"
}
```

### get_status

获取系统状态。

**参数**: 无

**返回**:
```json
{
  "status": "success",
  "initialized": true,
  "user": "...",
  "active_sessions": 2,
  "queue_status": {...}
}
```

## 故障排查

### 配置文件未找到

```
Error: OPENVIKING_CONFIG_FILE not set and no default config found
```

**解决方案**: 设置 `OPENVIKING_CONFIG_FILE` 环境变量指向你的 `ov.conf` 文件。

### 导入错误

```
ModuleNotFoundError: No module named 'openviking'
```

**解决方案**: 确保 `PYTHONPATH` 包含 OpenViking 目录。

### MCP 服务器无法启动

检查 OpenCode 日志：
```bash
cd /data0/wlf/opencode_test/opencode
bun run dev
# 查看 stderr 输出
```

## 开发

### 运行测试

```bash
# 测试 MCP 服务器启动
python -m openviking_mcp

# 测试工具调用（需要 MCP 客户端）
# 参考 test_integration.py
```

### 添加新工具

1. 在 `server.py` 中添加新的 `@mcp.tool()` 装饰的函数
2. 确保函数有完整的文档字符串和类型提示
3. 返回 JSON 格式的结果

## 架构

```
OpenCode (TypeScript)
  ↓ stdio (MCP Protocol)
OpenViking MCP Server (Python)
  ↓ Python API
OpenViking Client (AsyncOpenViking)
  ↓
VikingDB (向量数据库) + AGFS (文件系统)
```

## 许可证

Apache-2.0

## 相关链接

- [OpenViking 文档](https://www.openviking.ai/docs)
- [OpenCode 文档](https://opencode.ai)
- [MCP 协议规范](https://modelcontextprotocol.io)
