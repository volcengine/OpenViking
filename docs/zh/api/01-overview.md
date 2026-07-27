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
    timeout=120.0,
)
client.initialize()
```

#### Go SDK 客户端

Go SDK 是 Client-Server 模式下的 HTTP-only 客户端，作为主仓库的 `sdk/go` 独立 Go module 发布。

```bash
go get github.com/volcengine/OpenViking/sdk/go
```

```go
client, err := openviking.NewClient(openviking.Config{
    BaseURL: "http://localhost:1933",
    APIKey:  "your-key",
})
if err != nil {
    return err
}
defer client.CloseIdleConnections()
```

Go SDK 发送的身份请求头与 Python HTTP client 一致：

| Config 字段 | HTTP Header |
|-------------|-------------|
| `APIKey` | `X-API-Key` |
| `Account` | `X-OpenViking-Account` |
| `User` | `X-OpenViking-User` |
| `ActorPeerID` | `X-OpenViking-Actor-Peer` |

普通 `api_key` 部署下只需要设置 `APIKey`，服务端会从 API key 推导租户身份。只有在 trusted 部署或网关显式透传租户身份时，才需要设置 `Account` 和 `User`。

Go SDK 不支持 Python embedded 模式，也不保留旧 `agent_id` 兼容路径。更多示例见 [`sdk/go/README_CN.md`](../../../sdk/go/README_CN.md)。

#### JavaScript/TypeScript SDK 客户端

JavaScript/TypeScript SDK 是面向 Node.js 18+ 的 HTTP-only 客户端，同时发布
ESM、CommonJS 和 TypeScript 类型声明。

```bash
npm install @openviking/sdk
```

```ts
import { OpenVikingClient } from "@openviking/sdk";

const client = new OpenVikingClient({
  baseUrl: "http://localhost:1933",
  apiKey: "your-key",
});

const results = await client.search("部署文档", {
  targetUri: "viking://resources",
});
```

它与 Python、Go HTTP Client 使用相同的身份请求头和响应信封。更多示例见
[`sdk/typescript/README_CN.md`](../../../sdk/typescript/README_CN.md)。

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
  "user": "alice"
}
```

配置字段说明：

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `url` | 服务端地址 | （必填） |
| `api_key` | API Key | `null`（无认证） |
| `account` | 租户级请求的默认账户请求头 | `null` |
| `user` | 租户级请求的默认用户请求头 | `null` |
| `timeout` | HTTP 请求超时时间（秒） | `600.0` |
| `output` | 默认输出格式：`"table"` 或 `"json"` | `"table"` |

详细内容请参见 [配置指南](../guides/01-configuration.md#ovcliconf)。

#### 完全不依赖配置文件使用 Python SDK 客户端

`SyncHTTPClient` 和 `AsyncHTTPClient` 支持完全不依赖 `ovcli.conf` 配置文件，只需在初始化时**显式传入所有参数**即可：

```python
import openviking as ov

client = ov.SyncHTTPClient(
    url="http://localhost:1933",          # 显式传入
    api_key="your-key",                    # 显式传入（默认情况下 api_key 已经能标识用户身份）
    timeout=30.0,                          # 不要用默认值 600.0
    extra_headers={}                       # 传空 dict 而不是 None，可用于某些场景的网关认证等
)
client.initialize()
```

⚠️ **注意**：只要以下任一条件满足，客户端就会尝试加载配置文件：
- `url` 为 `None`
- `api_key` 为 `None`
- `timeout` 等于 `600.0`（默认值）
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

以下目录以服务端实际挂载路由为准。每组标题会跳转到详细文档；详细页只为真实存在的 HTTP、Python SDK、TypeScript SDK、Go SDK 或 CLI 能力显示对应 Tab，不会用等价的裸 HTTP 调用冒充 SDK。

### [系统状态](07-system.md)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 基础健康检查（无需认证） |
| GET | `/ready` | AGFS、VectorDB 和 API Key 管理器就绪检查（无需认证） |
| GET | `/api/v1/system/status` | 系统状态 |
| POST | `/api/v1/system/wait` | 等待后台处理完成 |
| POST | `/api/v1/system/consistency` | 文件系统与向量索引一致性检查 |
| POST | `/api/v1/system/backend/sync-status` | 查询后端同步状态 |
| POST | `/api/v1/system/backend/sync-retry` | 重试后端同步 |
| GET | `/api/v1/system/sync/{sync_path}` | 路径形式的同步状态兼容接口 |
| POST | `/api/v1/system/sync/{sync_path}/retry` | 路径形式的同步重试兼容接口 |

### [资源](02-resources.md)与[文件系统](03-filesystem.md)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/resources/temp_upload` | 上传后续导入所需的临时文件 |
| POST | `/api/v1/resources` | 从 URL 或临时文件添加资源 |
| GET | `/api/v1/fs/ls` | 列出目录 |
| GET | `/api/v1/fs/tree` | 获取目录树 |
| GET | `/api/v1/fs/stat` | 获取资源状态 |
| GET | `/api/v1/fs/attrs` | 获取逻辑扩展属性 |
| POST | `/api/v1/fs/attrs/set_tags` | 设置检索标签（兼容别名） |
| POST | `/api/v1/fs/mkdir` | 创建目录 |
| DELETE | `/api/v1/fs` | 删除资源 |
| POST | `/api/v1/fs/mv` | 移动或重命名资源 |

### [内容](12-content.md)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/content/read` | 读取完整内容（L2） |
| GET | `/api/v1/content/abstract` | 读取摘要（L0） |
| GET | `/api/v1/content/overview` | 读取概览（L1） |
| GET | `/api/v1/content/download` | 下载原始文件字节 |
| POST | `/api/v1/content/write` | 写入内容并刷新语义索引 |
| POST | `/api/v1/content/set_tags` | 设置检索标签 |
| POST | `/api/v1/content/reindex` | 重建语义或向量索引 |

### [技能](04-skills.md)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/skills` | 列出技能 |
| POST | `/api/v1/skills` | 添加技能 |
| POST | `/api/v1/skills/find` | 搜索技能 |
| POST | `/api/v1/skills/validate` | 校验技能数据 |
| GET | `/api/v1/skills/{skill_name}` | 获取技能 |
| PUT | `/api/v1/skills/{skill_name}` | 更新技能 |
| DELETE | `/api/v1/skills/{skill_name}` | 删除技能 |

### [会话](05-sessions.md)与[记忆](16-memory.md)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/sessions` | 创建会话 |
| GET | `/api/v1/sessions` | 列出会话 |
| GET | `/api/v1/sessions/{session_id}` | 获取会话 |
| GET | `/api/v1/sessions/{session_id}/tool-results` | 列出工具结果 |
| GET | `/api/v1/sessions/{session_id}/tool-results/{tool_result_id}` | 读取工具结果 |
| GET | `/api/v1/sessions/{session_id}/tool-results/{tool_result_id}/search` | 在工具结果内搜索 |
| GET | `/api/v1/sessions/{session_id}/context` | 获取组装后的上下文 |
| GET | `/api/v1/sessions/{session_id}/archives/{archive_id}` | 获取会话归档 |
| DELETE | `/api/v1/sessions/{session_id}` | 删除会话 |
| POST | `/api/v1/sessions/{session_id}/commit` | 归档会话并提取记忆 |
| POST | `/api/v1/sessions/{session_id}/extract` | 提取记忆 |
| POST | `/api/v1/sessions/{session_id}/messages` | 添加单条消息 |
| POST | `/api/v1/sessions/{session_id}/messages/batch` | 批量添加消息 |
| POST | `/api/v1/sessions/{session_id}/used` | 记录实际使用的上下文或技能 |
| POST | `/api/v1/search/recall` | 召回记忆并返回可直接注入的上下文 |

### [检索](06-retrieval.md)、[代码检索](21-code.md)与[关系](13-relations.md)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/search/find` | 语义搜索 |
| POST | `/api/v1/search/search` | 上下文感知搜索 |
| POST | `/api/v1/search/grep` | 内容模式搜索 |
| POST | `/api/v1/search/glob` | 文件模式匹配 |
| POST | `/api/v1/code/outline` | 提取代码结构 |
| POST | `/api/v1/code/search` | 代码搜索 |
| POST | `/api/v1/code/expand` | 展开代码上下文 |
| GET | `/api/v1/relations` | 获取资源关系 |
| POST | `/api/v1/relations/link` | 创建资源链接 |
| DELETE | `/api/v1/relations/link` | 删除资源链接 |
| POST | `/api/v1/relations/build_graph` | 构建关系图 |

### [Watch](15-watches.md)、[快照](11-snapshot.md)与 [OVPack](14-ovpack.md)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/watches` | 列出 watch，或按 `to_uri` 查询 |
| GET | `/api/v1/watches/{task_id}` | 按任务 ID 获取 watch |
| PATCH | `/api/v1/watches` | 按 `to_uri` 更新 watch |
| PATCH | `/api/v1/watches/{task_id}` | 按任务 ID 更新 watch |
| DELETE | `/api/v1/watches` | 按 `to_uri` 删除 watch |
| DELETE | `/api/v1/watches/{task_id}` | 按任务 ID 删除 watch |
| POST | `/api/v1/watches/trigger` | 按 `to_uri` 触发 watch |
| POST | `/api/v1/watches/{task_id}/trigger` | 按任务 ID 触发 watch |
| POST | `/api/v1/snapshot/commit` | 创建快照 |
| GET | `/api/v1/snapshot/log` | 查看快照历史 |
| POST | `/api/v1/snapshot/restore` | 恢复历史快照 |
| GET | `/api/v1/snapshot/show` | 查看快照或其中的文件 |
| GET | `/api/v1/snapshot/diff` | 对比快照 |
| GET | `/api/v1/snapshot/ignore` | 读取快照忽略规则 |
| PUT | `/api/v1/snapshot/ignore` | 替换快照忽略规则 |
| DELETE | `/api/v1/snapshot/ignore` | 清空快照忽略规则 |
| POST | `/api/v1/pack/export` | 导出 `.ovpack` |
| POST | `/api/v1/pack/import` | 导入 `.ovpack` |
| POST | `/api/v1/pack/backup` | 备份公开作用域 |
| POST | `/api/v1/pack/restore` | 恢复备份包 |

### [后台任务](17-tasks.md)、[运行观测](18-observer.md)与 [Metrics](09-metrics.md)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/tasks/{task_id}` | 获取后台任务 |
| GET | `/api/v1/tasks` | 列出后台任务 |
| GET | `/api/v1/observer/queue` | 队列状态 |
| GET | `/api/v1/observer/vikingdb` | VikingDB 状态 |
| GET | `/api/v1/observer/models` | 模型状态 |
| GET | `/api/v1/observer/lock` | 锁状态 |
| GET | `/api/v1/observer/retrieval` | 检索状态 |
| GET | `/api/v1/observer/filesystem` | 文件系统状态 |
| GET | `/api/v1/observer/system` | 聚合运行状态 |
| GET | `/metrics` | Prometheus 指标 |

### [管理员](08-admin.md)与[隐私配置](10-privacy.md)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/admin/accounts` | 创建账号及首个管理员 |
| GET | `/api/v1/admin/accounts` | 列出账号 |
| POST | `/api/v1/admin/migrate` | 迁移旧版身份数据 |
| DELETE | `/api/v1/admin/accounts/{account_id}` | 删除账号 |
| POST | `/api/v1/admin/accounts/{account_id}/users` | 注册用户 |
| GET | `/api/v1/admin/accounts/{account_id}/users` | 列出用户 |
| DELETE | `/api/v1/admin/accounts/{account_id}/users/{user_id}` | 移除用户 |
| PUT | `/api/v1/admin/accounts/{account_id}/users/{user_id}/role` | 修改用户角色 |
| POST | `/api/v1/admin/accounts/{account_id}/users/{user_id}/key` | 重新生成用户 Key |
| GET | `/api/v1/privacy-configs` | 列出隐私配置分类 |
| GET | `/api/v1/privacy-configs/{category}` | 列出分类目标 |
| GET | `/api/v1/privacy-configs/{category}/{target_key}` | 获取生效配置 |
| GET | `/api/v1/privacy-configs/{category}/{target_key}/versions` | 列出配置版本 |
| GET | `/api/v1/privacy-configs/{category}/{target_key}/versions/{version}` | 获取指定版本 |
| POST | `/api/v1/privacy-configs/{category}/{target_key}` | 写入并激活新版本 |
| POST | `/api/v1/privacy-configs/{category}/{target_key}/activate` | 激活指定版本 |

### [WebDAV](20-webdav.md) 与 [VikingBot API](24-vikingbot.md)

| 方法 | 路径 | 说明 |
|------|------|------|
| OPTIONS | `/webdav/resources`、`/webdav/resources/{resource_path}` | 查询 WebDAV 能力 |
| PROPFIND | `/webdav/resources`、`/webdav/resources/{resource_path}` | 查询资源属性 |
| GET / HEAD | `/webdav/resources`、`/webdav/resources/{resource_path}` | 读取文件或目录 |
| PUT | `/webdav/resources`、`/webdav/resources/{resource_path}` | 写入 UTF-8 文本文件 |
| DELETE | `/webdav/resources`、`/webdav/resources/{resource_path}` | 删除文件或目录 |
| MKCOL | `/webdav/resources`、`/webdav/resources/{resource_path}` | 创建目录 |
| MOVE | `/webdav/resources`、`/webdav/resources/{resource_path}` | 移动或重命名资源 |
| GET | `/bot/v1/health` | VikingBot 健康检查 |
| POST | `/bot/v1/chat` | VikingBot 非流式对话 |
| POST | `/bot/v1/chat/stream` | VikingBot 流式对话 |
| POST | `/bot/v1/feedback` | 提交 VikingBot 回答反馈 |

---

## 文档阅读计划

左侧导航按职责而不是按历史文件体积组织：

| 分组 | 适合查找的内容 |
|------|----------------|
| 核心数据 | 资源、内容、文件系统、技能、会话、记忆 |
| 检索与关系 | 语义检索、代码检索、资源关系 |
| 数据生命周期 | Watch、快照、OVPack |
| 运维与观测 | 系统、任务、Observer、Metrics |
| 身份与治理 | 管理员、隐私配置 |
| 协议与扩展 | WebDAV、VikingBot API |
