# OpenViking MCP Server

MCP（Model Context Protocol）HTTP 服务器，将 OpenViking RAG 能力作为工具暴露。

## 工具

| 工具 | 说明 |
|------|------|
| `query` | 完整 RAG 流程 — 搜索 + LLM 答案生成 |
| `search` | 仅语义搜索，返回匹配文档 |
| `add_resource` | 添加文件、目录或 URL 到数据库 |

## 快速开始

```bash
# 设置配置
cp ov.conf.example ov.conf
# 编辑 ov.conf，填入你的 API Key

# 安装依赖
uv sync

# 启动服务器（端口 2033 的可流式 HTTP）
uv run server.py
```

服务器将在 `http://127.0.0.1:2033/mcp` 可用。

## 从 Claude 连接

```bash
# 在 Claude CLI 中添加 MCP 服务器
claude mcp add --transport http openviking http://localhost:2033/mcp
```

或添加到 `.mcp.json`：

```json
{
  "mcpServers": {
    "openviking": {
      "type": "http",
      "url": "http://localhost:2033/mcp"
    }
  }
}
```

## 选项

```
uv run server.py [OPTIONS]

  --config PATH       配置文件路径（默认：./ov.conf，环境变量：OV_CONFIG）
  --data PATH         数据目录路径（默认：./data，环境变量：OV_DATA）
  --host HOST         绑定地址（默认：127.0.0.1）
  --port PORT         监听端口（默认：2033，环境变量：OV_PORT）
  --transport TYPE    streamable-http | stdio（默认：streamable-http）
```

## 使用 MCP Inspector 测试

```bash
npx @modelcontextprotocol/inspector
# 连接到 http://localhost:2033/mcp
```