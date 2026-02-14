# API 概览

本页介绍如何连接 OpenViking 以及所有 API 端点共享的约定。

## 连接 OpenViking

OpenViking 支持三种连接模式：

| 模式 | 使用场景 | 说明 |
|------|----------|------|
| **嵌入式** | 本地开发，单进程 | 本地运行，数据存储在本地 |
| **HTTP** | 连接 OpenViking Server | 通过 HTTP API 连接远程服务 |
| **CLI** | Shell 脚本、Agent 工具调用 | 通过 CLI 命令连接服务端 |

### 嵌入式模式

```python
import openviking as ov

client = ov.OpenViking(path="./data")
client.initialize()
```

嵌入式模式通过 `ov.conf` 配置 embedding、vlm、storage 等模块。默认路径 `~/.openviking/ov.conf`，也可通过环境变量指定：

```bash
export OPENVIKING_CONFIG_FILE=/path/to/ov.conf
```

最小配置示例：

```json
{
  "embedding": {
    "dense": {
      "api_base": "<api-endpoint>",
      "api_key": "<your-api-key>",
      "provider": "<volcengine|openai>",
      "dimension": 1024,
      "model": "<model-name>"
    }
  },
  "vlm": {
    "api_base": "<api-endpoint>",
    "api_key": "<your-api-key>",
    "provider": "<volcengine|openai>",
    "model": "<model-name>"
  }
}
```

完整配置选项和各服务商示例见 [配置指南](../guides/01-configuration.md)。

### HTTP 模式

```python
client = ov.SyncHTTPClient(
    url="http://localhost:1933",
    api_key="your-key",
)
client.initialize()
```

未显式传入 `url` 时，HTTP 客户端会自动从 `ovcli.conf` 读取连接信息。`ovcli.conf` 是 HTTP 客户端和 CLI 共享的配置文件，默认路径 `~/.openviking/ovcli.conf`，也可通过环境变量指定：

```bash
export OPENVIKING_CLI_CONFIG_FILE=/path/to/ovcli.conf
```

```json
{
  "url": "http://localhost:1933",
  "api_key": "your-key"
}
```

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `url` | 服务端地址 | （必填） |
| `api_key` | API Key | `null`（无认证） |
| `output` | 默认输出格式：`"table"` 或 `"json"` | `"table"` |

详见 [配置指南](../guides/01-configuration.md#ovcliconf)。

### 直接 HTTP（curl）

```bash
curl http://localhost:1933/api/v1/fs/ls?uri=viking:// \
  -H "X-API-Key: your-key"
```

### CLI 模式

CLI 连接到 OpenViking 服务端，将所有操作暴露为 Shell 命令。CLI 同样从 `ovcli.conf` 读取连接信息（与 HTTP 客户端共享）。

**基本用法**

```bash
openviking [全局选项] <command> [参数] [命令选项]
```

**全局选项**（必须放在命令名之前）

| 选项 | 说明 |
|------|------|
| `--output`, `-o` | 输出格式：`table`（默认）、`json` |
| `--version` | 显示 CLI 版本 |

示例：

```bash
openviking -o json ls viking://resources/
```

## 生命周期

**嵌入式模式**

```python
import openviking as ov

client = ov.OpenViking(path="./data")
client.initialize()

# ... 使用 client ...

client.close()
```

**HTTP 模式**

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933")
client.initialize()

# ... 使用 client ...

client.close()
```

## 认证

详见 [认证指南](../guides/04-authentication.md)。

- **X-API-Key** 请求头：`X-API-Key: your-key`
- **Bearer** 请求头：`Authorization: Bearer your-key`
- 如果服务端未配置 API Key，则跳过认证。
- `/health` 端点始终不需要认证。

## 响应格式

所有 HTTP API 响应遵循统一格式：

**成功**

```json
{
  "status": "ok",
  "result": { ... },
  "time": 0.123
}
```

**错误**

```json
{
  "status": "error",
  "error": {
    "code": "NOT_FOUND",
    "message": "Resource not found: viking://resources/nonexistent/"
  },
  "time": 0.01
}
```

## CLI 输出格式

### Table 模式（默认）

列表数据渲染为表格，非列表数据 fallback 到格式化 JSON：

```bash
openviking ls viking://resources/
# name          size  mode  isDir  uri
# .abstract.md  100   420   False  viking://resources/.abstract.md
```

### JSON 模式（`--output json`）

所有命令输出格式化 JSON，与 API 响应的 `result` 结构一致：

```bash
openviking -o json ls viking://resources/
# [{ "name": "...", "size": 100, ... }, ...]
```

可在 `ovcli.conf` 中设置默认输出格式：

```json
{
  "url": "http://localhost:1933",
  "output": "json"
}
```

### 紧凑模式（`--compact`, `-c`）

- 当 `--output=json` 时：紧凑 JSON 格式 + `{ok, result}` 包装，适用于脚本
- 当 `--output=table` 时：对表格输出采取精简表示（如去除空列等）

**JSON 输出 - 成功**

```json
{"ok": true, "result": ...}
```

**JSON 输出 - 错误**

```json
{"ok": false, "error": {"code": "NOT_FOUND", "message": "Resource not found", "details": {}}}
```

### 特殊情况

- **字符串结果**（`read`、`abstract`、`overview`）：直接打印原文
- **None 结果**（`mkdir`、`rm`、`mv`）：无输出

### 退出码

| 退出码 | 含义 |
|--------|------|
| 0 | 成功 |
| 1 | 一般错误 |
| 2 | 配置错误 |
| 3 | 连接错误 |

## 错误码

| 错误码 | HTTP 状态码 | 说明 |
|--------|-------------|------|
| `OK` | 200 | 成功 |
| `INVALID_ARGUMENT` | 400 | 无效参数 |
| `INVALID_URI` | 400 | 无效的 Viking URI 格式 |
| `NOT_FOUND` | 404 | 资源未找到 |
| `ALREADY_EXISTS` | 409 | 资源已存在 |
| `UNAUTHENTICATED` | 401 | 缺少或无效的 API Key |
| `PERMISSION_DENIED` | 403 | 权限不足 |
| `RESOURCE_EXHAUSTED` | 429 | 超出速率限制 |
| `FAILED_PRECONDITION` | 412 | 前置条件不满足 |
| `DEADLINE_EXCEEDED` | 504 | 操作超时 |
| `UNAVAILABLE` | 503 | 服务不可用 |
| `INTERNAL` | 500 | 内部服务器错误 |
| `UNIMPLEMENTED` | 501 | 功能未实现 |
| `EMBEDDING_FAILED` | 500 | Embedding 生成失败 |
| `VLM_FAILED` | 500 | VLM 调用失败 |
| `SESSION_EXPIRED` | 410 | 会话已过期 |

## API 端点

### 系统

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查（无需认证） |
| GET | `/api/v1/system/status` | 系统状态 |
| POST | `/api/v1/system/wait` | 等待处理完成 |

### 资源

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/resources` | 添加资源 |
| POST | `/api/v1/skills` | 添加技能 |
| POST | `/api/v1/pack/export` | 导出 .ovpack |
| POST | `/api/v1/pack/import` | 导入 .ovpack |

### 文件系统

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/fs/ls` | 列出目录 |
| GET | `/api/v1/fs/tree` | 目录树 |
| GET | `/api/v1/fs/stat` | 资源状态 |
| POST | `/api/v1/fs/mkdir` | 创建目录 |
| DELETE | `/api/v1/fs` | 删除资源 |
| POST | `/api/v1/fs/mv` | 移动资源 |

### 内容

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/content/read` | 读取完整内容（L2） |
| GET | `/api/v1/content/abstract` | 读取摘要（L0） |
| GET | `/api/v1/content/overview` | 读取概览（L1） |

### 搜索

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/search/find` | 语义搜索 |
| POST | `/api/v1/search/search` | 上下文感知搜索 |
| POST | `/api/v1/search/grep` | 模式搜索 |
| POST | `/api/v1/search/glob` | 文件模式匹配 |

### 关联

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/relations` | 获取关联 |
| POST | `/api/v1/relations/link` | 创建链接 |
| DELETE | `/api/v1/relations/link` | 删除链接 |

### 会话

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/sessions` | 创建会话 |
| GET | `/api/v1/sessions` | 列出会话 |
| GET | `/api/v1/sessions/{id}` | 获取会话 |
| DELETE | `/api/v1/sessions/{id}` | 删除会话 |
| POST | `/api/v1/sessions/{id}/commit` | 提交会话 |
| POST | `/api/v1/sessions/{id}/messages` | 添加消息 |

### Observer

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/observer/queue` | 队列状态 |
| GET | `/api/v1/observer/vikingdb` | VikingDB 状态 |
| GET | `/api/v1/observer/vlm` | VLM 状态 |
| GET | `/api/v1/observer/system` | 系统状态 |
| GET | `/api/v1/debug/health` | 快速健康检查 |

## 相关文档

- [资源管理](02-resources.md) - 资源管理 API
- [检索](06-retrieval.md) - 搜索 API
- [文件系统](03-filesystem.md) - 文件系统操作
- [会话管理](05-sessions.md) - 会话管理
- [技能](04-skills.md) - 技能管理
- [系统](07-system.md) - 系统和监控 API
