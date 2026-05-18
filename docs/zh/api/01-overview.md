# API 概览

本页介绍如何连接 OpenViking 以及所有 API 端点共享的约定。

## 连接模式

OpenViking 支持两种使用模式：**嵌入式模式**（直接调用 Python API）和 **Client-Server 模式**（通过 HTTP API 连接）。

本 API 文档主要介绍 **Client-Server 模式**的 HTTP API 使用方式。嵌入式模式虽然可用，但后续文档将不单独展开介绍。

| 模式 | 适用场景 | 说明 |
|------|----------|------|
| **嵌入式模式** | 本地开发、单进程 | 使用本地数据存储运行 |
| **HTTP** | 连接 OpenViking 服务器 | 通过 HTTP API 连接远程服务器 |
| **CLI** | Shell 脚本、Agent 工具使用 | 通过 CLI 命令连接服务器 |

### 嵌入式模式（简要说明）

嵌入式模式允许在 Python 进程内直接调用 OpenViking API，无需启动独立的服务器进程。

```python
import openviking as ov

client = ov.OpenViking(path="./data")
client.initialize()
```

嵌入式模式通过 `ov.conf` 配置 embedding、vlm、storage 等模块。默认配置路径为 `~/.openviking/ov.conf`，也可通过环境变量指定：

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
      "provider": "<volcengine|openai|jina|...>",
      "dimension": 1024,
      "model": "<model-name>"
    }
  },
  "vlm": {
    "api_base": "<api-endpoint>",
    "api_key": "<your-api-key>",
    "provider": "<volcengine|openai|openai-codex|kimi|glm>",
    "model": "<model-name>"
  }
}
```

对于 `provider: "openai-codex"`，通过 `openviking-server init` 配置 Codex OAuth 后，`vlm.api_key` 是可选的。

完整的配置选项和 provider 特定示例，请参见 [配置指南](../guides/01-configuration.md)。

### Client-Server 模式（主要介绍）

Client-Server 模式通过 HTTP API 连接 OpenViking 服务器，支持多租户、远程访问等特性。OpenViking 的服务器启动方式请参见相关部署文档。

#### Python SDK 客户端

```python
import openviking as ov

client = ov.SyncHTTPClient(
    url="http://localhost:1933",
    api_key="your-key",
    agent_id="my-agent",
    timeout=120.0,
)
client.initialize()
```

未显式传入 `url` 时，HTTP 客户端会自动从 `ovcli.conf` 读取连接信息。`ovcli.conf` 是 HTTP 客户端和 CLI 共享的配置文件，默认路径 `~/.openviking/ovcli.conf`，也可通过环境变量指定：

```bash
export OPENVIKING_CLI_CONFIG_FILE=/path/to/ovcli.conf
```

配置文件示例：

```json
{
  "url": "http://localhost:1933",
  "api_key": "your-key",
  "account": "acme",
  "user": "alice",
  "agent_id": "my-agent"
}
```

配置字段说明：

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `url` | 服务端地址 | （必填） |
| `api_key` | API Key | `null`（无认证） |
| `account` | 租户级请求的默认账户请求头 | `null` |
| `user` | 租户级请求的默认用户请求头 | `null` |
| `agent_id` | Agent 标识符请求头 | `null` |
| `timeout` | HTTP 请求超时时间（秒） | `60.0` |
| `output` | 默认输出格式：`"table"` 或 `"json"` | `"table"` |

详细内容请参见 [配置指南](../guides/01-configuration.md#ovcliconf)。

#### 完全不依赖配置文件使用 Python SDK 客户端

`SyncHTTPClient` 和 `AsyncHTTPClient` 支持完全不依赖 `ovcli.conf` 配置文件，只需在初始化时**显式传入所有参数**即可：

```python
import openviking as ov

client = ov.SyncHTTPClient(
    url="http://localhost:1933",          # 显式传入
    api_key="your-key",                    # 显式传入（默认情况下 api_key 已经能标识用户身份）
    timeout=30.0,                          # 不要用默认值 60.0
    extra_headers={}                       # 传空 dict 而不是 None，可用于某些场景的网关认证等
)
client.initialize()
```

⚠️ **注意**：只要以下任一条件满足，客户端就会尝试加载配置文件：
- `url` 为 `None`
- `api_key` 为 `None`
- `timeout` 等于 `60.0`（默认值）
- `extra_headers` 为 `None`

#### HTTP 调用示例

- CLI、`SyncHTTPClient`、`AsyncHTTPClient` 遇到本地文件或目录时，会先自动上传，再调用服务端 API。
- Python HTTP client 和 CLI 也可以通过客户端配置启用 shared 临时上传（`ovcli.conf` 中设置 `upload.mode = "shared"`）。
- 裸 HTTP 调用没有这层封装。使用 `curl` 或其他 HTTP 客户端时，需要先调用 `POST /api/v1/resources/temp_upload`，再把返回的 `temp_file_id` 传给目标 API。
- `temp_upload` 默认使用 `upload_mode=local`。只有在你显式需要分布式共享临时上传时，才应传 `upload_mode=shared`。
- 裸 HTTP 如果导入本地目录，需要先自行打成 `.zip` 再通过上述方法上传；服务端不接受直接传宿主机目录路径。
- `POST /api/v1/resources` 可以直接接收远端 URL，但不接受 `./doc.md`、`/tmp/doc.md` 这类宿主机本地路径。

直接 HTTP（curl）调用示例如下

```bash
curl http://localhost:1933/api/v1/fs/ls?uri=viking:// \
    -H "X-API-Key: your-key"
```

#### CLI 模式

OpenViking CLI （可简写为 ov 命令）连接到 OpenViking 服务端，将所有操作暴露为 Shell 命令。CLI 同样从 `ovcli.conf` 读取连接信息（与 HTTP 客户端共享）。

基本用法：

```bash
openviking [全局选项] <command> [参数] [命令选项]
```

全局选项（必须放在命令名之前）：

| 选项 | 说明 |
|------|------|
| `--output`, `-o` | 输出格式：`table`（默认）、`json` |
| `--version` | 显示 CLI 版本 |

示例：

```bash
openviking -o json ls viking://resources/
```

## 生命周期

### 嵌入式模式

```python
import openviking as ov

client = ov.OpenViking(path="./data")
client.initialize()

# ... 使用 client ...

client.close()
```

### Client-Server 模式

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933")
client.initialize()

# ... 使用 client ...

client.close()
```

CLI 则直接通过命令行调用，需要先配置 ovcli.conf 文件，无需额外初始化客户端：

```
openviking -o json ls viking://resources/
```

## 认证

详见 [认证指南](../guides/04-authentication.md)。

- **Authorization Bearer** 请求头：`Authorization: Bearer your-key` （建议的方式）
- **X-API-Key** 请求头：`X-API-Key: your-key`
- 如果服务端未配置 API Key，则跳过认证。
- `/health` 和 `/ready` 端点始终不需要认证。

## 响应格式

所有 HTTP API 响应遵循统一格式：

### 成功响应

```json
{
  "status": "ok",
  "result": { ... },
  "time": 0.123
}
```

顶层 `status` 表示本次 HTTP API 请求是否成功。某些成功响应会在 `result` 中返回业务状态，例如 `"status": "success"`、`"status": "accepted"` 或任务状态。这些字段不是 API 传输层错误。

### 错误响应

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

HTTP 错误始终使用顶层错误 envelope。资源解析、同步 reindex 等同步处理失败会返回非 2xx 响应，顶层为 `status="error"`，并包含 `error` 对象。客户端不应通过 `result.status="error"` 判断请求失败。

请求校验失败，包括 JSON 格式错误、缺少必填字段和参数值非法，统一返回 HTTP `400`，并使用 `error.code="INVALID_ARGUMENT"`。响应不会使用 FastAPI 原生的 `{"detail": ...}` 错误格式；当存在字段级校验信息时，会通过 `error.details.validation_errors` 返回。

Python HTTP SDK（`SyncHTTPClient` 和 `AsyncHTTPClient`）会把该 envelope 映射为对应的 `OpenVikingError` 子类。例如 `PROCESSING_ERROR` 会抛出 `ProcessingError`。

## CLI 输出格式

### Table 模式（默认）

列表数据渲染为表格，非列表数据 fallback 到格式化 JSON：

```bash
openviking ls viking://resources/
# name          size  mode  isDir  uri
# .abstract.md  100   420   false  viking://resources/.abstract.md
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

JSON 输出 - 成功：

```json
{"ok": true, "result": ...}
```

JSON 输出 - 错误：

```json
{"ok": false, "error": {"code": "NOT_FOUND", "message": "...", "details": {}}}
```

### 特殊情况

- **字符串结果**（`read`、`abstract`、`overview`）：直接打印原文
- **None 结果**（`mkdir`、`rm`、`mv`）：无输出

### 退出码

**注：退出码是 CLI（命令行工具）的返回码，不是 HTTP API 的状态码。**

| 退出码 | 说明 | 触发场景 |
|--------|------|----------|
| 0 | 成功 | 命令执行成功 |
| 1 | 一般错误 | 命令执行失败（如 API 调用失败、网络错误、找不到二进制文件等） |
| 2 | 配置错误 | 无法加载 `ovcli.conf` 配置文件、`--sudo` 需要 `root_api_key` 但未配置、`--sudo` 用于非管理员命令 |
| 3 | 连接错误 | 无法连接到服务器 |

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
| `CONFLICT` | 409 | 操作与正在进行的任务或已有状态冲突 |
| `DEADLINE_EXCEEDED` | 504 | 操作超时 |
| `UNAVAILABLE` | 503 | 服务不可用 |
| `PROCESSING_ERROR` | 500 | 资源或语义处理失败 |
| `INTERNAL` | 500 | 内部服务器错误 |
| `UNIMPLEMENTED` | 501 | 功能未实现 |
| `EMBEDDING_FAILED` | 500 | Embedding 生成失败 |
| `VLM_FAILED` | 500 | VLM 调用失败 |
| `SESSION_EXPIRED` | 410 | 会话已过期 |
| `NOT_INITIALIZED` | - | 服务或组件未初始化（需要先调用 initialize()） |

---

## API 端点总览

以下是 OpenViking 提供的所有 HTTP API 端点，按功能模块分组：

### 系统端点

| 方法 | 路径 | 说明 | 认证 |
|------|------|------|------|
| GET | `/health` | 健康检查 | 无需认证 |
| GET | `/ready` | 就绪探针（检查 AGFS、VectorDB、APIKeyManager） | 无需认证 |
| GET | `/metrics` | Prometheus 指标导出 | 可选 |
| GET | `/api/v1/system/status` | 系统状态 | 需要 |
| POST | `/api/v1/system/wait` | 等待处理完成 | 需要 |
| POST | `/api/v1/system/consistency` | 文件系统和向量索引一致性检查 | 需要 |

### 资源端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/resources/temp_upload` | 临时文件上传（用于后续资源导入） |
| POST | `/api/v1/resources` | 添加资源（支持 URL 或 temp_file_id） |
| POST | `/api/v1/skills` | 添加技能 |

### Pack 端点

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| POST | `/api/v1/pack/export` | 导出 .ovpack 文件 | ROOT/ADMIN |
| POST | `/api/v1/pack/import` | 导入 .ovpack 文件 | ROOT/ADMIN |
| POST | `/api/v1/pack/backup` | 备份公开 scope | ROOT/ADMIN |
| POST | `/api/v1/pack/restore` | 恢复备份包 | ROOT/ADMIN |

### 文件系统端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/fs/ls` | 列出目录内容 |
| GET | `/api/v1/fs/tree` | 获取目录树结构 |
| GET | `/api/v1/fs/stat` | 获取资源状态 |
| POST | `/api/v1/fs/mkdir` | 创建目录 |
| DELETE | `/api/v1/fs` | 删除资源 |
| POST | `/api/v1/fs/mv` | 移动/重命名资源 |

### 内容端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/content/read` | 读取完整内容（L2） |
| GET | `/api/v1/content/abstract` | 读取摘要（L0） |
| GET | `/api/v1/content/overview` | 读取概览（L1） |
| GET | `/api/v1/content/download` | 下载原始文件字节流 |
| POST | `/api/v1/content/write` | 修改已有文件并自动刷新语义与向量 |
| POST | `/api/v1/content/reindex` | 重新构建已有内容的语义/向量索引 |

### 搜索端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/search/find` | 语义搜索（无会话上下文） |
| POST | `/api/v1/search/search` | 上下文感知搜索（支持会话） |
| POST | `/api/v1/search/grep` | 内容模式搜索 |
| POST | `/api/v1/search/glob` | 文件模式匹配 |

### 关系端点（实验特性，可能在后续版本中改变）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/relations` | 获取资源关联 |
| POST | `/api/v1/relations/link` | 创建资源链接 |
| DELETE | `/api/v1/relations/link` | 删除资源链接 |

### 会话端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/sessions` | 创建会话 |
| GET | `/api/v1/sessions` | 列出会话 |
| GET | `/api/v1/sessions/{session_id}` | 获取会话 |
| GET | `/api/v1/sessions/{session_id}/context` | 获取组装后的会话上下文 |
| GET | `/api/v1/sessions/{session_id}/archives/{archive_id}` | 获取特定会话归档 |
| DELETE | `/api/v1/sessions/{session_id}` | 删除会话 |
| POST | `/api/v1/sessions/{session_id}/commit` | 提交会话（归档并提取记忆） |
| POST | `/api/v1/sessions/{session_id}/extract` | 从会话提取记忆 |
| POST | `/api/v1/sessions/{session_id}/messages` | 添加消息 |
| POST | `/api/v1/sessions/{session_id}/used` | 记录实际使用的上下文 / 技能 |

### 隐私配置

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/privacy-configs` | 列出隐私配置分类 |
| GET | `/api/v1/privacy-configs/{category}` | 列出分类下目标 |
| GET | `/api/v1/privacy-configs/{category}/{target_key}` | 获取当前生效配置（meta + current） |
| POST | `/api/v1/privacy-configs/{category}/{target_key}` | 写入新版本并激活 |
| GET | `/api/v1/privacy-configs/{category}/{target_key}/versions` | 列出版本号 |
| GET | `/api/v1/privacy-configs/{category}/{target_key}/versions/{version}` | 获取指定版本详情 |
| POST | `/api/v1/privacy-configs/{category}/{target_key}/activate` | 激活指定版本 |

### 任务端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/tasks/{task_id}` | 获取单个后台任务状态 |
| GET | `/api/v1/tasks` | 列出后台任务（支持按类型、状态、资源过滤） |

### 观测端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/observer/queue` | 队列系统状态 |
| GET | `/api/v1/observer/vikingdb` | VikingDB 状态 |
| GET | `/api/v1/observer/models` | 模型状态（VLM / embedding / rerank） |
| GET | `/api/v1/observer/lock` | 锁子系统状态 |
| GET | `/api/v1/observer/retrieval` | 检索子系统状态 |
| GET | `/api/v1/observer/system` | 系统整体状态 |

### 调试端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/debug/health` | 快速健康检查 |
| GET | `/api/v1/debug/vector/scroll` | 分页查看向量记录 |
| GET | `/api/v1/debug/vector/count` | 统计向量记录数量 |

### 统计端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/stats/memories` | 获取记忆健康统计（支持按类别过滤） |
| GET | `/api/v1/stats/sessions/{session_id}` | 获取会话提取统计 |

### 管理员端点（多租户）

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| POST | `/api/v1/admin/accounts` | 创建工作区 + 首个 admin | ROOT |
| GET | `/api/v1/admin/accounts` | 列出所有工作区 | ROOT |
| DELETE | `/api/v1/admin/accounts/{account_id}` | 删除工作区（级联清理数据） | ROOT |
| POST | `/api/v1/admin/accounts/{account_id}/users` | 注册用户 | ROOT/ADMIN |
| GET | `/api/v1/admin/accounts/{account_id}/users` | 列出用户 | ROOT/ADMIN |
| GET | `/api/v1/admin/accounts/{account_id}/agents` | 列出 agent namespace | ROOT/ADMIN |
| DELETE | `/api/v1/admin/accounts/{account_id}/users/{user_id}` | 移除用户 | ROOT/ADMIN |
| PUT | `/api/v1/admin/accounts/{account_id}/users/{user_id}/role` | 修改用户角色 | ROOT |
| POST | `/api/v1/admin/accounts/{account_id}/users/{user_id}/key` | 重新生成 User Key | ROOT/ADMIN |

### VikingBot 交互端点（可选）

VikingBot API 需要服务器启动时指定 `--with-bot` 选项：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | Bot 健康检查（与系统 /health 复用） |
| POST | `/chat` | 发送消息给 Bot |
| POST | `/chat/stream` | Bot 流式响应 |

### WebDAV 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| OPTIONS | `/webdav/resources`, `/webdav/resources/{path}` | WebDAV 选项查询 |
| PROPFIND | `/webdav/resources`, `/webdav/resources/{path}` | WebDAV 属性查询 |
| GET/HEAD | `/webdav/resources/{path}` | 读取文件 |
| PUT | `/webdav/resources/{path}` | 上传/创建文件（仅 UTF-8 文本） |
| DELETE | `/webdav/resources/{path}` | 删除文件/目录 |
| MKCOL | `/webdav/resources/{path}` | 创建目录 |
| MOVE | `/webdav/resources/{path}` | 移动/重命名资源 |

---

## 文档阅读计划

后续 API 文档按功能模块组织如下：

| 文档 | 内容 |
|------|------|
| [资源管理](02-resources.md) | 资源和技能的添加、导入、导出 |
| [文件系统](03-filesystem.md) | 目录操作、内容读写 |
| [技能](04-skills.md) | 技能管理 API |
| [会话管理](05-sessions.md) | 会话创建、消息管理、记忆提取 |
| [检索](06-retrieval.md) | 搜索、关联、上下文获取 |
| [系统](07-system.md) | 系统状态、监控、调试 API |
| [隐私配置](10-privacy.md) | 隐私配置版本管理与切换 |
| [指标与 Metrics](09-metrics.md) | Prometheus 指标导出与抓取说明 |
| [管理员](08-admin.md) | 多租户账号和用户管理 |
