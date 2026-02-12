# OpenViking Server/CLI 模式技术方案

## 一、背景与目标

### 1.1 当前架构

OpenViking 目前主要以 Python SDK 形式提供服务，支持两种部署模式：
- **Embedded 模式**：自动启动本地 AGFS 子进程和 VikingVectorIndex（单例）
- **Service 模式**：连接远程 AGFS 和 VectorDB 服务（非单例）

### 1.2 现有问题

| 问题 | 描述 |
|------|------|
| 缺少统一 CLI | 无法通过命令行管理 OpenViking |
| 服务分散 | AGFS 和 VectorDB 需要分别启动 |
| 无 HTTP API | 核心功能没有 HTTP API 暴露 |
| 部署复杂 | 生产环境需要手动协调多个服务 |

### 1.3 目标

1. **CLI 模式**：提供完整的命令行工具
2. **Server 模式**：提供统一的 HTTP API 服务
3. **保持兼容**：现有 SDK 使用方式不变

---

## 二、竞品分析与最佳实践

### 2.1 竞品对比

| 产品 | CLI 设计 | Server 模式 | API 风格 | 部署模式 |
|------|----------|-------------|----------|----------|
| **Chroma** | `chroma run/db/login` | 内置 FastAPI | RESTful | in-memory/persistent/client-server |
| **Qdrant** | 无独立 CLI | 独立服务 | RESTful + gRPC | standalone/distributed |
| **Milvus** | `milvus_cli` 交互式 | 独立服务 | RESTful + gRPC | lite/standalone/distributed |
| **Mem0** | 无 CLI | Platform + OSS | RESTful | managed/self-hosted |
| **LlamaIndex** | 无统一 CLI | 微服务架构 | RESTful | self-hosted |

### 2.2 Chroma 设计参考

Chroma 的 CLI 设计简洁实用：
```bash
chroma run --path <directory>     # 启动服务器
chroma db create <name>           # 创建数据库
chroma login                      # 认证登录
chroma browse                     # 浏览数据
```

**借鉴点**：
- 三种运行模式（in-memory/persistent/client-server）
- 简洁的命令结构
- 环境变量配置支持

### 2.3 Qdrant API 设计参考

Qdrant 的 REST API 采用资源层级结构：
```
POST /collections/{collection_name}/points/search
GET  /collections/{collection_name}/points/{id}
PUT  /collections/{collection_name}/points
```

**借鉴点**：
- 资源层级 URL 设计
- POST 用于复杂查询（请求体包含向量和过滤条件）
- 完善的过滤 DSL
- 一致性级别控制

### 2.4 Milvus CLI 设计参考

Milvus CLI 采用模块化架构：
```
milvus_cli/
├── Connection.py    # 连接管理
├── Collection.py    # 集合操作
├── Database.py      # 数据库管理
├── Index.py         # 索引管理
├── Partition.py     # 分区管理
├── Data.py          # 数据操作
├── Role.py          # 角色管理
└── User.py          # 用户管理
```

**借鉴点**：
- 模块化命令组织
- 交互式 CLI 支持
- 完整的 RBAC 支持

### 2.5 Mem0 架构参考

Mem0 的双部署模式：
- **Platform API**：托管服务，API Key 认证
- **OSS SDK**：自托管，直接调用

**借鉴点**：
- 统一的搜索管道（Platform 和 OSS 共用）
- 复杂的过滤 DSL（AND/OR 逻辑）
- 用户隔离（user_id scoping）

### 2.6 Redis/MySQL 客户端设计参考

Redis 和 MySQL 的客户端设计模式：
- **Server 独立运行**：Server 作为独立进程启动，不依赖 Client
- **Client 仅连接**：Client 只负责连接 Server，不负责启动或管理 Server
- **配置分离**：Server 配置和 Client 配置分开管理

```python
# Redis 示例
import redis
r = redis.Redis(host='localhost', port=6379)  # 仅连接，不启动 Server
r.set('foo', 'bar')

# MySQL 示例
import mysql.connector
conn = mysql.connector.connect(host='localhost', user='root')  # 仅连接
```

**借鉴点**：
- Client 无需 `initialize()` 方法
- 连接参数通过构造函数传入
- Server 生命周期独立于 Client

### 2.7 响应格式与错误处理最佳实践

#### Qdrant 响应格式
```json
{
  "status": "ok",
  "time": 0.123,
  "result": {...},
  "usage": {"cpu": 100, "vector_io_read": 40}
}
```
- 统一的 `status/time/result` 包装
- 可选的 `usage` 字段用于资源监控
- 错误通过 HTTP 状态码 + 消息字符串返回

#### Milvus 错误码
```python
# 数字错误码 + 消息
(code=1, message="UnexpectedError...")
(code=2, message="Connection timeout...")
(code=2200, message="Retry exhausted...")
```
- 基于 gRPC 的数字错误码
- 包含 `code`, `message`, `reason` 三元组

#### gRPC 标准状态码（业界标准）
| 码 | 名称 | HTTP | 说明 |
|----|------|------|------|
| 0 | OK | 200 | 成功 |
| 3 | INVALID_ARGUMENT | 400 | 参数无效 |
| 5 | NOT_FOUND | 404 | 资源不存在 |
| 7 | PERMISSION_DENIED | 403 | 权限不足 |
| 13 | INTERNAL | 500 | 内部错误 |
| 14 | UNAVAILABLE | 503 | 服务不可用 |
| 16 | UNAUTHENTICATED | 401 | 未认证 |

**借鉴点**：
- gRPC 17 种标准状态码覆盖所有常见场景
- 明确的 HTTP 状态码映射
- 可重试 vs 不可重试错误分类

### 2.8 最佳实践总结

| 方面 | 最佳实践 | 来源 |
|------|----------|------|
| **CLI 结构** | 动词-名词命令（`run`, `db create`） | Chroma |
| **API 路由** | 资源层级（`/collections/{name}/points`） | Qdrant |
| **认证** | API Key + 可选 JWT | Qdrant, Mem0 |
| **部署模式** | 三层（embedded/standalone/distributed） | Milvus |
| **过滤系统** | JSON DSL（AND/OR/比较操作符） | Mem0, Qdrant |
| **模块化** | 按资源类型组织命令 | Milvus |
| **Client 设计** | 仅连接，不管理 Server 生命周期 | Redis, MySQL |
| **响应格式** | `{status, time, result}` 统一包装 | Qdrant |
| **错误码** | gRPC 标准状态码（字符串形式） | gRPC, Milvus |
| **HTTP 映射** | 错误码到 HTTP 状态码的标准映射 | gRPC |

### 2.9 参考资料

- [Chroma Documentation](https://docs.trychroma.com/) - CLI 设计参考
- [Qdrant API Reference](https://api.qdrant.tech/) - REST API 设计参考
- [Milvus CLI](https://github.com/zilliztech/milvus_cli) - 模块化 CLI 参考
- [Mem0 Documentation](https://docs.mem0.ai/) - 双部署模式参考
- [LlamaIndex Architecture](https://developers.llamaindex.ai/python/cloud/self_hosting/architecture/) - 微服务架构参考
- [gRPC Status Codes](https://grpc.github.io/grpc/core/md_doc_statuscodes.html) - 标准错误码定义
- [gRPC HTTP Mapping](https://grpc.github.io/grpc/core/md_doc_http-grpc-status-mapping.html) - HTTP 状态码映射
- [Qdrant Common Errors](https://qdrant.tech/documentation/guides/common-errors/) - 错误处理参考
- [Chroma Troubleshooting](https://docs.trychroma.com/troubleshooting) - 异常类设计参考

---

## 三、架构设计

### 3.0 接口定位

OpenViking 提供三种接口，面向不同使用场景：

| 接口 | 使用者 | 场景 | 特点 |
|------|--------|------|------|
| **Python SDK** | Agent 开发者 | Agent 代码中调用 | 对象化 API，支持异步，client 实例维护状态 |
| **Bash CLI** | Agent / 运维人员 | Agent subprocess 调用、运维脚本 | 非交互式，通过 ovcli.conf 配置文件管理连接状态 |
| **HTTP API** | 任意语言客户端 | 跨语言集成、微服务调用 | RESTful，无状态，每次请求带身份信息 |

**选型建议**：
- **Agent 开发**：优先使用 Python SDK，可以维护 client 实例状态
- **Agent 调用外部工具**：使用 Bash CLI，通过 ovcli.conf 配置文件传递连接和身份信息
- **非 Python 环境**：使用 HTTP API

### 3.1 分层架构

```
┌─────────────────────────────────────────────────────────────┐
│                      CLI Layer                               │
│  openviking <command> [options]                              │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                     Client Layer                             │
│  ┌─────────────────┐  ┌─────────────────┐                   │
│  │ LocalClient     │  │ HTTPClient      │                   │
│  │ (直接调用Service)│  │ (HTTP调用Server)│                   │
│  └─────────────────┘  └─────────────────┘                   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                     Service Layer                            │
│  ┌─────────────────────────────────────────────────────────┐│
│  │ OpenVikingService                                        ││
│  │ - ResourceService (资源解析、向量化)                      ││
│  │ - SearchService (语义搜索)                               ││
│  │ - SessionService (会话管理)                              ││
│  │ - FSService (文件系统操作)                               ││
│  │ - DebugService (调试服务)                                ││
│  └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                   Infrastructure Layer                       │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐          │
│  │ VikingFS    │  │ VikingDB    │  │ AGFS        │          │
│  └─────────────┘  └─────────────┘  └─────────────┘          │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 部署模式

#### 选型决策

**为什么支持多种部署模式**：

1. **嵌入式模式**：降低入门门槛，开发者无需部署服务即可使用
2. **独立服务模式**：支持团队共享、多 Agent 并发访问
3. **混合模式**：灵活组合，适应不同基础设施环境

**参考业界实践**：
- Chroma：in-memory / persistent / client-server 三种模式
- Milvus：lite / standalone / distributed 三种模式
- SQLite：嵌入式 vs PostgreSQL/MySQL 独立服务

| 模式 | 说明 | 适用场景 |
|------|------|----------|
| **嵌入式** | SDK 自动启动本地 AGFS + VectorDB | 本地开发、单机部署 |
| **独立服务** | 通过 CLI 启动 Server，SDK/CLI 通过 HTTP 连接 | 团队共享、生产环境 |
| **混合模式** | 本地 Server + 远程 AGFS/VectorDB | 分布式部署 |

```
嵌入式模式:
┌─────────────────────────────────────┐
│ Python Process                       │
│  ┌─────────┐  ┌─────────┐           │
│  │ SDK     │──│ Service │           │
│  └─────────┘  └────┬────┘           │
│                    │                 │
│  ┌─────────┐  ┌────┴────┐           │
│  │ AGFS    │  │ VectorDB│           │
│  └─────────┘  └─────────┘           │
└─────────────────────────────────────┘

独立服务模式:
┌─────────────┐      ┌─────────────────────────────────────┐
│ CLI / SDK   │─HTTP─│ OpenViking Server                    │
│ (Client)    │      │  ┌─────────┐  ┌─────────┐           │
└─────────────┘      │  │ Service │──│ AGFS    │           │
                     │  └────┬────┘  └─────────┘           │
                     │       │                              │
                     │  ┌────┴────┐                        │
                     │  │ VectorDB│                        │
                     │  └─────────┘                        │
                     └─────────────────────────────────────┘
```

### 3.3 身份与隔离

支持多租户场景，首先通过 account_id 区分不同的应用租户，然后允许通过 `user` 和 `agent` 实现租户内的身份隔离：

用户或智能体身份标识详见 `from openviking_cli.session.user_id import UserIdentifier`

| 参数 | 说明 | 默认值 |
|------|------|------|
| `account_id` | 租户账号标识，区分不同的租户，租户内 user 和 agent id 空间隔离，也就是保证租户之间数据隔离 | `default` |
| `user_id` | 用户身份标识，用户在租户内的唯一标识符 | `default` |
| `agent_id` | 智能体身份标识，智能体或应用在租户内的唯一标识符 | `default` |

> 举例说明，如果一个团队部署一个 OpenViking 上下文数据库，让团队成员共享一部分上下文资料，但各自保留独立的记忆数据，团队使用统一的 account_id，每个成员使用不同的 user_id 来区分，成员使用不同的应用（如 OpenCode, OpenClaw 等）则使用不同的 agent_id 来区分。
> 一般情况下，`account_id` 不会出现在 viking:// uri 中，因为每个租户的用户数据都是隔离的，不需要在 uri 中指定租户。


### 3.4 配置管理

#### Server 配置

服务端配置统一使用 JSON 配置文件，通过 `--config` 或 `OPENVIKING_CONFIG_FILE` 环境变量指定（与 `OpenVikingConfig` 共用同一个文件）：

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 1933,
    "api_key": "your-api-key"
  },
  "storage": {
    "path": "~/.openviking/data"
  },
  "embedding": {
    "dense": {
      "provider": "openai",
      "model": "text-embedding-3-small"
    }
  },
  "vlm": {
    "provider": "openai",
    "model": "gpt-4o"
  }
}
```

#### Client 配置

客户端 SDK 通过构造函数参数配置：

```python
# 构造函数参数
client = SyncHTTPClient(url="http://localhost:1933", api_key="your-api-key")
```

SDK 构造函数只接受 `url`、`api_key`、`path` 参数。不支持 `config` 参数，也不支持 `vectordb_url`/`agfs_url` 参数。

#### CLI 配置

CLI 通过 `ovcli.conf` 配置文件管理连接信息，不使用 `--url`、`--api-key` 等全局命令行选项：

```json
{
  "url": "http://localhost:1933",
  "api_key": "sk-xxx",
  "output": "table"
}
```

配置文件路径通过 `OPENVIKING_CLI_CONFIG_FILE` 环境变量指定。

#### 环境变量

只保留 2 个环境变量：

| 环境变量 | 说明 | 示例 |
|----------|------|------|
| `OPENVIKING_CONFIG_FILE` | ov.conf 配置文件路径（SDK 嵌入式 + Server） | `~/.openviking/ov.conf` |
| `OPENVIKING_CLI_CONFIG_FILE` | ovcli.conf 配置文件路径（CLI 连接配置） | `~/.openviking/ovcli.conf` |

不再使用单字段环境变量（`OPENVIKING_URL`、`OPENVIKING_API_KEY`、`OPENVIKING_HOST`、`OPENVIKING_PORT`、`OPENVIKING_PATH`、`OPENVIKING_VECTORDB_URL`、`OPENVIKING_AGFS_URL` 均已移除）。

#### 配置文件

系统使用 2 个配置文件：

| 配置文件 | 用途 | 环境变量 |
|----------|------|----------|
| `ov.conf` | SDK 嵌入式模式 + Server 配置 | `OPENVIKING_CONFIG_FILE` |
| `ovcli.conf` | CLI 连接配置（url/api_key/user） | `OPENVIKING_CLI_CONFIG_FILE` |

#### 配置优先级

从高到低：
1. 构造函数参数（SDK）/ 命令行参数（`--config`、`--host`、`--port`）
2. 配置文件（`ov.conf` 或 `ovcli.conf`）

---

## 四、模块设计

### 4.1 模块职责

| 模块 | 职责 | 依赖 |
|------|------|------|
| **service/** | 核心业务逻辑，与传输层无关 | Infrastructure Layer |
| **server/** | HTTP API 服务，调用 Service 层 | service/ |
| **cli/** | 命令行工具，调用 Client 层 | client/ |
| **client/** | 客户端抽象，支持本地和 HTTP 两种模式 | service/ 或 HTTP |

**设计原则**：
- **Service 层独立**：业务逻辑不依赖传输层（HTTP/CLI），便于测试和复用
- **Client 层抽象**：LocalClient 和 HTTPClient 实现相同接口，上层无感知
- **CLI 薄层**：CLI 只负责参数解析和输出格式化，业务逻辑在 Service 层

### 4.2 目录结构

```
openviking/
├── service/                     # Service 层（业务逻辑）
│   ├── __init__.py
│   ├── core.py                  # OpenVikingService
│   ├── fs_service.py            # 文件系统操作
│   ├── resource_service.py      # 资源导入、技能添加
│   ├── search_service.py        # 语义搜索
│   ├── session_service.py       # 会话管理
│   ├── relation_service.py      # 关联管理
│   ├── pack_service.py          # 导入导出
│   └── debug_service.py         # 调试服务
│
├── server/                      # HTTP Server
│   ├── __init__.py
│   ├── app.py                   # FastAPI 应用
│   ├── bootstrap.py             # 服务启动器
│   ├── config.py                # 服务器配置
│   ├── dependencies.py          # 依赖注入
│   └── routers/
│       ├── resources.py         # /api/v1/resources
│       ├── filesystem.py        # /api/v1/fs
│       ├── content.py           # /api/v1/content
│       ├── search.py            # /api/v1/search
│       ├── relations.py         # /api/v1/relations
│       ├── sessions.py          # /api/v1/sessions
│       └── debug.py             # /api/v1/debug
│
├── cli/                         # CLI 模块
│   ├── __init__.py
│   ├── main.py                  # CLI 入口
│   └── output.py                # 输出格式化（JSON/table/脚本模式）
│
├── client/                      # Client 层
│   ├── __init__.py
│   ├── local.py                 # LocalClient
│   └── http.py                  # HTTPClient
│
├── async_client.py              # 兼容层
└── sync_client.py               # 兼容层
```

### 4.3 pyproject.toml

```toml
[project.scripts]
openviking = "openviking_cli.cli.main:app"
```

### 4.4 新增依赖

```toml
dependencies = [
    # ... 现有依赖
    # argparse is used for CLI (part of Python stdlib, no extra dependency needed)
    "rich>=13.0.0",      # CLI 美化输出
]
```

---

## 五、接口设计

### 5.1 核心方法定义

| 方法 | 参数 | 说明 |
|------|------|------|
| `add_resource` | `path, target, wait, timeout` | 导入资源 |
| `add_skill` | `data, wait` | 添加技能 |
| `ls` | `uri, simple, recursive` | 列出目录 |
| `tree` | `uri` | 目录树 |
| `stat` | `uri` | 状态信息 |
| `mkdir` | `uri` | 创建目录 |
| `rm` | `uri, recursive` | 删除 |
| `mv` | `from_uri, to_uri` | 移动 |
| `read` | `uri` | 读取完整内容 (L2) |
| `abstract` | `uri` | 读取摘要 (L0) |
| `overview` | `uri` | 读取概览 (L1) |
| `find` | `query, target_uri, limit, threshold` | 语义搜索 |
| `search` | `query, target_uri, session, limit` | 带上下文搜索 |
| `grep` | `uri, pattern, case_insensitive` | 内容搜索 |
| `glob` | `pattern, uri` | 模式匹配 |
| `link` | `from_uri, uris, reason` | 创建关联 |
| `unlink` | `from_uri, uri` | 删除关联 |
| `relations` | `uri` | 查看关联 |
| `export_ovpack` | `uri, to` | 导出 |
| `import_ovpack` | `file, target, force, vectorize` | 导入 |
| `session` | `user` | 创建/获取 Session 对象 |
| `sessions` | - | 获取所有 Session 对象列表 |
| `wait_processed` | `timeout` | 等待处理完成 |
| `get_status` | - | 获取系统状态（包含 queue/vikingdb/vlm 组件状态） |
| `is_healthy` | - | 快速健康检查 |

**Session 对象方法：**

| 方法 | 参数 | 说明 |
|------|------|------|
| `add_message` | `role, content` | 添加消息 |
| `commit` | - | 提交会话（归档消息、提取记忆） |
| `delete` | - | 删除会话 |

### 5.2 统一返回值格式

参考 Qdrant 的响应格式设计，采用 `{status, time, result}` 统一包装：

**成功响应**：
```json
{
  "status": "ok",
  "result": {...},
  "time": 0.123,
  "usage": {                    // 可选，用于监控
    "tokens": 100,
    "vectors_scanned": 1000
  }
}
```

**错误响应**：
```json
{
  "status": "error",
  "error": {
    "code": "NOT_FOUND",         // gRPC 风格错误码
    "message": "Resource not found: viking://...",
    "details": {...}            // 可选，额外错误信息
  },
  "time": 0.05
}
```

**Python 数据模型**：
```python
@dataclass
class Response:
    status: str                 # "ok" | "error"
    result: Optional[Any] = None
    error: Optional[ErrorInfo] = None
    time: float = 0.0
    usage: Optional[UsageInfo] = None

@dataclass
class ErrorInfo:
    code: str                   # NOT_FOUND, INVALID_URI, ...
    message: str
    details: Optional[dict] = None

@dataclass
class UsageInfo:
    tokens: Optional[int] = None
    vectors_scanned: Optional[int] = None
```

### 5.3 各方法返回值

**ls / tree**
```json
{
  "status": "ok",
  "result": {
    "entries": [
      {"name": "doc.md", "type": "file", "uri": "viking://resources/doc.md", "size": 1024},
      {"name": "images/", "type": "dir", "uri": "viking://resources/images/", "children_count": 5}
    ]
  },
  "time": 0.05
}
```

**find / search**
```json
{
  "status": "ok",
  "result": {
    "items": [
      {
        "uri": "viking://resources/doc.md",
        "score": 0.92,
        "content": "...",
        "abstract": "...",
        "metadata": {"type": "markdown", "scope": "resources"}
      }
    ],
    "query": "how to use openviking",
    "total": 10
  },
  "time": 0.15,
  "usage": {"vectors_scanned": 1000}
}
```

**read / abstract / overview**
```json
{
  "status": "ok",
  "result": {
    "uri": "viking://resources/doc.md",
    "content": "...",
    "level": "L2"
  },
  "time": 0.02
}
```

**stat**
```json
{
  "status": "ok",
  "result": {
    "uri": "viking://resources/doc.md",
    "type": "file",
    "size": 1024,
    "created_at": "2024-01-01T00:00:00Z",
    "updated_at": "2024-01-02T00:00:00Z",
    "has_abstract": true,
    "has_overview": true,
    "vectorized": true
  },
  "time": 0.01
}
```

**add_resource**
```json
{
  "status": "ok",
  "result": {
    "uri": "viking://resources/my-docs/",
    "files_count": 10,
    "processing_status": "processing",
    "queue": {
      "vectorize": {"processed": 5, "total": 10, "failed": 0}
    }
  },
  "time": 0.5
}
```

**session**（Python CLI 返回 Session 对象，HTTP API 返回 JSON）
```python
# Python CLI
session = client.session()
session.id          # "session-abc123"
session.user        # "alice"
session.created_at  # datetime
session.message_count  # 10
session.compressed  # False

# HTTP API 返回
{
  "status": "ok",
  "result": {
    "session_id": "session-abc123",
    "user": "alice",
    "created_at": "2024-01-01T00:00:00Z",
    "message_count": 10,
    "compressed": false
  },
  "time": 0.02
}
```

**错误示例**
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

```json
{
  "status": "error",
  "error": {
    "code": "INVALID_URI",
    "message": "Invalid URI format: 'invalid-uri'",
    "details": {
      "expected": "viking://<scope>/<path>",
      "received": "invalid-uri"
    }
  },
  "time": 0.001
}
```

**debug/status**
```json
{
  "status": "ok",
  "result": {
    "is_healthy": true,
    "components": {
      "queue": {"name": "queue", "is_healthy": true, "has_errors": false, "details": {...}},
      "vikingdb": {"name": "vikingdb", "is_healthy": true, "has_errors": false, "details": {...}},
      "vlm": {"name": "vlm", "is_healthy": true, "has_errors": false, "details": {...}}
    },
    "errors": []
  },
  "time": 0.05
}
```

**debug/health**
```json
{
  "healthy": true
}
```

### 5.4 错误处理

基于 gRPC 标准状态码，结合 OpenViking 业务场景定制。

#### 通用错误码（基于 gRPC 标准）

| 错误码 | HTTP 状态 | 说明 | 适用场景 |
|--------|-----------|------|----------|
| `OK` | 200 | 成功 | 所有成功操作 |
| `INVALID_ARGUMENT` | 400 | 参数无效 | URI 格式错误、参数缺失、类型错误 |
| `NOT_FOUND` | 404 | 资源不存在 | URI 指向的资源不存在 |
| `ALREADY_EXISTS` | 409 | 资源已存在 | 创建重复资源、重复导入 |
| `PERMISSION_DENIED` | 403 | 权限不足 | API Key 无权限、用户隔离 |
| `UNAUTHENTICATED` | 401 | 未认证 | API Key 缺失或无效 |
| `RESOURCE_EXHAUSTED` | 429 | 资源耗尽 | 配额超限、速率限制 |
| `FAILED_PRECONDITION` | 412 | 前置条件失败 | 资源未就绪、依赖缺失 |
| `ABORTED` | 409 | 操作中止 | 并发冲突、事务失败 |
| `DEADLINE_EXCEEDED` | 504 | 超时 | 处理超时、等待超时 |
| `UNAVAILABLE` | 503 | 服务不可用 | AGFS/VectorDB 不可用 |
| `INTERNAL` | 500 | 内部错误 | 未预期的服务端错误 |
| `UNIMPLEMENTED` | 501 | 未实现 | 功能未实现 |

#### OpenViking 特定错误码

| 错误码 | HTTP 状态 | 说明 | 适用场景 |
|--------|-----------|------|----------|
| `INVALID_URI` | 400 | URI 格式无效 | `viking://` 协议解析失败 |
| `PROCESSING` | 202 | 处理中 | 资源正在处理，尚未就绪 |
| `EMBEDDING_FAILED` | 500 | 向量化失败 | Embedding API 调用失败 |
| `VLM_FAILED` | 500 | VLM 调用失败 | 摘要/概览生成失败 |
| `SESSION_EXPIRED` | 410 | 会话过期 | 会话已被删除或过期 |

#### Python 异常类

参考 Chroma 的特定异常类设计，异常类与错误码一一对应：

```python
# openviking/exceptions.py

class OpenVikingError(Exception):
    """Base exception for all OpenViking errors."""
    def __init__(self, code: str, message: str, details: dict = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(f"[{code}] {message}")

# 参数错误
class InvalidArgumentError(OpenVikingError): pass
class InvalidURIError(InvalidArgumentError): pass

# 资源错误
class NotFoundError(OpenVikingError): pass
class AlreadyExistsError(OpenVikingError): pass

# 认证/权限错误
class UnauthenticatedError(OpenVikingError): pass
class PermissionDeniedError(OpenVikingError): pass

# 服务错误
class UnavailableError(OpenVikingError): pass
class InternalError(OpenVikingError): pass
class DeadlineExceededError(OpenVikingError): pass

# 业务错误
class ProcessingError(OpenVikingError): pass
class EmbeddingFailedError(OpenVikingError): pass
class SessionExpiredError(OpenVikingError): pass
```

**错误码到异常的映射**：
```python
ERROR_CODE_TO_EXCEPTION = {
    "INVALID_ARGUMENT": InvalidArgumentError,
    "INVALID_URI": InvalidURIError,
    "NOT_FOUND": NotFoundError,
    "ALREADY_EXISTS": AlreadyExistsError,
    "UNAUTHENTICATED": UnauthenticatedError,
    "PERMISSION_DENIED": PermissionDeniedError,
    "UNAVAILABLE": UnavailableError,
    "INTERNAL": InternalError,
    "DEADLINE_EXCEEDED": DeadlineExceededError,
    "PROCESSING": ProcessingError,
    "EMBEDDING_FAILED": EmbeddingFailedError,
    "SESSION_EXPIRED": SessionExpiredError,
}

def raise_for_error(response: dict):
    """Convert error response to exception."""
    if response.get("status") == "error":
        error = response["error"]
        exc_class = ERROR_CODE_TO_EXCEPTION.get(error["code"], OpenVikingError)
        raise exc_class(error["code"], error["message"], error.get("details", {}))
```

---

## 六、CLI 实现

### 6.1 Bash CLI

#### 模式选型：非交互式 CLI

**选型决策**：采用非交互式 CLI。

**为什么选择非交互式**：

1. **Agent 调用场景**：Agent 通过 subprocess 调用 CLI，非交互式更自然
2. **无状态设计**：每次调用独立，避免状态污染和并发冲突
3. **易于集成**：可以直接在 shell 脚本、CI/CD 中使用
4. **参考业界实践**：kubectl、aws cli、gh cli 都是非交互式设计

#### 多 Agent 场景

OpenViking 支持多 user、多 agent，CLI 需要处理多进程并发场景：

```
┌─────────────────────────────────────────────────────────────┐
│                    OpenViking Server                         │
│                                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                  │
│  │ user:alice│  │ user:bob │  │ user:carol│                 │
│  │ agent:a1  │  │ agent:b1 │  │ agent:c1  │                 │
│  └──────────┘  └──────────┘  └──────────┘                  │
└─────────────────────────────────────────────────────────────┘
        ▲              ▲              ▲
        │              │              │
   ┌────┴────┐   ┌────┴────┐   ┌────┴────┐
   │ Agent A │   │ Agent B │   │ Agent C │
   │ (进程1) │   │ (进程2) │   │ (进程3) │
   └─────────┘   └─────────┘   └─────────┘
```

多个 Agent 进程可能同时运行，各自有不同的 user/agent 身份。各进程通过各自的 `ovcli.conf` 配置文件管理连接信息和身份（详见 3.4 配置管理）。

#### 使用示例

**Agent 调用场景**：
```bash
# CLI 连接信息通过 ovcli.conf 配置文件管理
# 配置文件路径通过 OPENVIKING_CLI_CONFIG_FILE 环境变量指定
export OPENVIKING_CLI_CONFIG_FILE=~/.openviking/ovcli.conf

# ovcli.conf 内容示例：
# {
#   "url": "http://localhost:1933",
#   "api_key": "sk-xxx",
#   "output": "table"
# }

# 后续 CLI 调用自动读取 ovcli.conf，无需重复指定
openviking find "how to use openviking"
openviking ls viking://resources/
openviking read viking://resources/doc.md
```

#### 设计原则

1. **与接口一致**：命令名、参数与接口定义保持一致
2. **体现核心特性**：突出文件系统范式、分层上下文、语义搜索、会话管理
3. **简洁直观**：常用操作简单，复杂操作通过子命令组织

#### 命令列表

```bash
# 服务管理
openviking serve [--config <file>] [--port 1933] [--host 0.0.0.0]
openviking status

# 资源导入
openviking add-resource <path> [--to <uri>] [--wait] [--timeout N]
openviking add-skill <file> [--wait]

# 文件系统操作
openviking ls <uri> [--simple] [--recursive]
openviking tree <uri>
openviking stat <uri>
openviking mkdir <uri>
openviking rm <uri> [--recursive]
openviking mv <from> <to>

# 内容读取
openviking read <uri>
openviking abstract <uri>
openviking overview <uri>

# 搜索
openviking find <query> [<uri>] [--limit N] [--threshold F]
openviking search <query> [<uri>] [--session-id ID] [--limit N]
openviking grep <uri> <pattern> [-i]
openviking glob <pattern> [<uri>]

# 关联管理
openviking link <from> <to>... [--reason TEXT]
openviking unlink <from> <to>
openviking relations <uri>

# 会话管理
openviking session new [--user <name>]
openviking session list
openviking session get <id>
openviking session commit <id>

# 导入导出
openviking export <uri> <file.ovpack>
openviking import <file.ovpack> <uri> [--force] [--no-vectorize]

# 工具命令
openviking wait [--timeout N]
openviking config show
openviking config init

# 调试命令
openviking status                         # 系统整体状态（包含 queue/vikingdb/vlm 组件状态）
openviking health                         # 快速健康检查
```

#### 命令分组说明

| 命令组 | 体现特性 | 说明 |
|--------|----------|------|
| `add-resource/add-skill` | 自动处理 | 导入后自动解析、分层、向量化 |
| `ls/tree/stat/mkdir/rm/mv` | 文件系统范式 | 像操作文件一样管理上下文 |
| `read/abstract/overview` | 分层上下文 | L0/L1/L2 按需加载 |
| `find/search` | 目录递归检索 | 语义搜索 + 目录定位 |
| `session *` | 会话管理 | 自动压缩、记忆提取 |
| `status/health` | 调试诊断 | 系统状态查询、健康检查 |

#### 实现 (使用 argparse)

```python
# openviking/__main__.py
import argparse
import sys


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="OpenViking - An Agent-native context database",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # serve command
    serve_parser = subparsers.add_parser("serve", help="Start OpenViking HTTP Server")
    serve_parser.add_argument("--config", type=str, default=None, help="Config file path (ov.conf)")
    serve_parser.add_argument("--host", type=str, default=None, help="Host to bind to")
    serve_parser.add_argument("--port", type=int, default=None, help="Port to bind to")

    args = parser.parse_args()

    if args.command == "serve":
        from openviking.server.bootstrap import main as serve_main
        serve_main()
    else:
        parser.print_help()
        sys.exit(1)
```

---

### 6.2 Python SDK

#### 使用方式

```python
from openviking import OpenViking

# 嵌入式模式
client = OpenViking(path="./data")

# HTTP 模式
client = OpenViking(url="http://localhost:1933", api_key="xxx")
```

#### 完整示例

```python
from openviking import OpenViking

# 连接
client = OpenViking(url="http://localhost:1933", api_key="xxx")

# 导入资源
result = client.add_resource("./docs/", target="viking://resources/my-docs/", wait=True)
print(f"导入完成: {result.data.uri}, 文件数: {result.data.files_count}")

# 分层读取
abstract = client.abstract("viking://resources/my-docs/")  # L0
overview = client.overview("viking://resources/my-docs/")  # L1
content = client.read("viking://resources/my-docs/README.md")  # L2

# 语义搜索
results = client.find("how to configure", target_uri="viking://resources/my-docs/", limit=10)
for r in results.data.results:
    print(f"{r.uri}: {r.score:.2f}")

# 会话管理（OOP 模式）
session = client.session()
session.add_message("user", "What is OpenViking?")
results = client.search("What is OpenViking?", session=session)

# Session 操作
session.compress()
memories = session.extract()

# 获取所有 session
all_sessions = client.sessions()
```

#### 实现方式

```python
class Session:
    """Session 对象，封装会话操作"""
    def __init__(self, client, session_id: str, user: UserIdentifier):
        self._client = client
        self.id = session_id
        self.user = user

    def add_message(self, role: str, content: str):
        """添加消息"""
        ...

    def compress(self):
        """压缩会话"""
        ...

    def extract(self):
        """提取记忆"""
        ...

class OpenViking:
    def __init__(
        self,
        path: Optional[str] = None,
        url: Optional[str] = None,
        api_key: Optional[str] = None,
        user: Optional[str] = None,
        user: Optional[UserIdentifier] = None
    ):
        if url:
            self._backend = HTTPBackend(url, api_key)
        else:
            self._backend = LocalBackend(path)
        self._user = user


    def session(self, user: UserIdentifier = None) -> Session:
        """创建或获取 Session 对象"""
        ...

    def sessions(self) -> List[Session]:
        """获取所有 Session 对象"""
        ...
```

### 6.3 使用指南

#### 安装

```bash
pip install openviking
```

#### 配置文件

详见 3.4 配置管理中的配置文件定义。

#### viking:// 目录结构

```
viking://
├── resources/           # 外部资源（文档、代码等）
├── user/
│   └── memories/        # 用户记忆
├── agent/
│   ├── memories/        # Agent 记忆
│   ├── skills/          # Agent 技能
│   └── instructions/    # Agent 指令
└── session/             # 会话数据
```

#### 分层上下文

| 层级 | 方法 | 说明 | 适用场景 |
|------|------|------|----------|
| L0 | `abstract()` | 一句话摘要 | 快速浏览、列表展示 |
| L1 | `overview()` | 核心信息 | 初步了解、决策参考 |
| L2 | `read()` | 完整内容 | 详细阅读、代码执行 |

---

## 七、HTTP API 实现

### 7.1 设计原则

**选型决策**：

1. **RESTful 风格**：资源层级 URL，标准 HTTP 方法
   - 参考 Qdrant API 设计：`/collections/{name}/points`
   - 便于理解和使用，符合开发者习惯

2. **POST 用于复杂查询**：搜索等操作使用 POST，请求体包含查询参数
   - 原因：GET 的 URL 长度有限制，复杂查询参数（如向量、过滤条件）不适合放 URL
   - 参考：Qdrant、Elasticsearch 都采用 POST 进行搜索

3. **统一响应格式**：所有 API 返回 `{status, time, result}` 结构
   - 参考 Qdrant 响应格式
   - 便于客户端统一处理

**为什么不用 GraphQL**：
- OpenViking 的查询模式相对固定，不需要 GraphQL 的灵活性
- RESTful 更简单，学习成本低
- 便于缓存和调试

### 7.2 认证机制

采用 API Key 认证：

```
X-API-Key: your-api-key
# 或
Authorization: Bearer your-api-key
```

**认证策略**：
- `/health` 端点：永远不需要认证（用于负载均衡器健康检查）
- 其他 API 端点：
  - 如果 `config.api_key` 为 `None`（默认）→ 跳过认证（本地开发模式）
  - 如果 `config.api_key` 有值 → 验证请求中的 Key

**配置方式**：
```json
// ~/.openviking/ov.conf
{
  "server": {
    "api_key": "your-secret-key"  // 设置后启用认证，不设置则跳过认证
  }
}
```

### 7.3 API 端点设计

所有 API 响应格式遵循 5.2 统一返回值格式，具体返回值结构见 5.3 各方法返回值。

#### 资源管理 `/api/v1/resources`
```
POST   /api/v1/resources                    # add_resource
       Body: {"path": "...", "target": "...", "wait": false}

POST   /api/v1/skills                       # add_skill
       Body: {"data": {...}}
```

#### 文件系统 `/api/v1/fs`
```
GET    /api/v1/fs/ls?uri=viking://          # ls
GET    /api/v1/fs/tree?uri=viking://        # tree
GET    /api/v1/fs/stat?uri=viking://...     # stat
POST   /api/v1/fs/mkdir                     # mkdir
       Body: {"uri": "viking://..."}

DELETE /api/v1/fs?uri=viking://...&recursive=false  # rm
POST   /api/v1/fs/mv                        # mv
       Body: {"from": "...", "to": "..."}
```

#### 内容读取 `/api/v1/content`
```
GET    /api/v1/content/read?uri=viking://...     # read (L2)
GET    /api/v1/content/abstract?uri=viking://... # abstract (L0)
GET    /api/v1/content/overview?uri=viking://... # overview (L1)
```

#### 搜索 `/api/v1/search`
```
POST   /api/v1/search/find                  # find
       Body: {"query": "...", "uri": "viking://", "limit": 10}

POST   /api/v1/search/search                # search (with session)
       Body: {"query": "...", "uri": "...", "session_id": "..."}

POST   /api/v1/search/grep                  # grep
       Body: {"uri": "...", "pattern": "...", "case_insensitive": false}

POST   /api/v1/search/glob                  # glob
       Body: {"pattern": "**/*.md", "uri": "viking://"}
```

#### 关联 `/api/v1/relations`
```
POST   /api/v1/relations/link               # link
       Body: {"from": "...", "to": "...", "reason": "..."}

DELETE /api/v1/relations/link               # unlink
       Body: {"from": "...", "to": "..."}

GET    /api/v1/relations?uri=viking://...   # relations
```

#### 会话 `/api/v1/sessions`
```
POST   /api/v1/sessions                     # session new
       Body: {"user": "alice"}

GET    /api/v1/sessions                     # session list
GET    /api/v1/sessions/{id}                # session get
DELETE /api/v1/sessions/{id}                # session delete

POST   /api/v1/sessions/{id}/commit        # session commit
POST   /api/v1/sessions/{id}/messages       # add message
       Body: {"role": "user", "content": "..."}
```

#### 导入导出 `/api/v1/pack`
```
POST   /api/v1/pack/export                  # export
       Body: {"uri": "...", "file": "..."}

POST   /api/v1/pack/import                  # import
       Body: {"file": "...", "uri": "...", "force": false}
```

#### 系统 `/api/v1/system`
```
GET    /health                              # 健康检查
GET    /api/v1/system/status                # 系统状态
POST   /api/v1/system/wait                  # wait_processed
       Body: {"timeout": 60}
```

#### 调试 `/api/v1/debug`
```
GET    /api/v1/debug/status                 # 系统整体状态（包含 queue/vikingdb/vlm 组件状态）
GET    /api/v1/debug/health                 # 快速健康检查（返回 bool）
```

### 7.4 Server 实现

```python
# openviking/server/app.py
from fastapi import FastAPI, Depends
from openviking.server.auth import verify_api_key
from openviking.server.routers import resources, filesystem, content, search, sessions, debug

app = FastAPI(
    title="OpenViking API",
    description="Context Database for AI Agents",
    version="1.0.0"
)

# 注册路由
app.include_router(resources.router, prefix="/api/v1/resources", dependencies=[Depends(verify_api_key)])
app.include_router(filesystem.router, prefix="/api/v1/fs", dependencies=[Depends(verify_api_key)])
app.include_router(content.router, prefix="/api/v1/content", dependencies=[Depends(verify_api_key)])
app.include_router(search.router, prefix="/api/v1/search", dependencies=[Depends(verify_api_key)])
app.include_router(sessions.router, prefix="/api/v1/sessions", dependencies=[Depends(verify_api_key)])
app.include_router(debug.router, prefix="/api/v1/debug", dependencies=[Depends(verify_api_key)])

@app.get("/health")
async def health():
    return {"status": "healthy"}
```

---

## 八、实现任务拆解

### 8.1 任务概览

| 任务 | 描述 | 依赖 | 优先级 | 状态 | 适合社区开发者 |
|------|------|------|--------|------|---------------|
| T1 | Service 层抽取 | - | P0 | Done | |
| T2 | HTTP Server | T1 | P1 | Done | |
| T3 | CLI 基础框架 | T1 | P1 | | |
| T4 | Python SDK | T2 | P2 | Done | |
| T5 | CLI 完整命令 | T3 | P2 | | |
| T6 | 集成测试 | T4, T5 | P3 | | |
| T7 | 文档更新 | T6 | P3 | | |
| T8 | Docker 部署 | T2 | P1 | | ✅ |
| T9 | MCP Server | T1 | P1 | | ✅ |
| T10 | TypeScript SDK | T2 | P2 | | ✅ |
| T11 | Golang SDK | T2 | P2 | | ✅ |

### 8.2 依赖关系

```
T1 (Service层)
    ├── T2 (Server) ──┬── T4 (Python SDK) ──┐
    │                 ├── T8 (Docker)        │
    │                 ├── T10 (TS SDK)       ├── T6 (测试) ── T7 (文档)
    │                 └── T11 (Go SDK)       │
    │                                        │
    ├── T3 (CLI基础) ── T5 (CLI完整) ────────┘
    │
    └── T9 (MCP Server)
```

### 8.3 任务详情

#### T1: Service 层抽取（P0）

**目标**：从 `async_client.py` 抽取业务逻辑到独立的 Service 层

**交付物**：
- `openviking/service/core.py` - OpenVikingService
- `openviking/service/resource_service.py` - 资源导入、技能添加
- `openviking/service/search_service.py` - 语义搜索
- `openviking/service/session_service.py` - 会话管理
- `openviking/service/fs_service.py` - 文件系统操作

**验收标准**：
- 现有单元测试全部通过
- `async_client.py` 作为兼容层，API 不变

---

#### T2: HTTP Server（P1）

**目标**：实现 FastAPI HTTP Server

**交付物**：
- `openviking/server/app.py` - FastAPI 应用
- `openviking/server/bootstrap.py` - 服务启动器
- `openviking/server/auth.py` - API Key 认证
- `openviking/server/routers/*.py` - 所有 API 路由
- `openviking/server/models.py` - 统一响应模型

**验收标准**：
```bash
# 启动服务
openviking serve --config ./ov.conf --port 1933

# 验证 API
curl http://localhost:1933/health
curl -X POST http://localhost:1933/api/v1/resources \
  -H "X-API-Key: test" -d '{"path": "./docs"}'
curl http://localhost:1933/api/v1/fs/ls?uri=viking://
```

---

#### T3: CLI 基础框架（P1）

**目标**：实现 CLI 核心命令

**交付物**：
- `openviking/cli/main.py` - CLI 入口
- `openviking/cli/output.py` - 输出格式化
- 核心命令：`serve`, `ls`, `find`, `read`, `abstract`, `overview`, `add-resource`

**验收标准**：
```bash
openviking serve --config ./ov.conf --port 1933
openviking add-resource ./docs/ --wait
openviking ls viking://resources/
openviking find "how to use"
```

---

#### T4: Python SDK（P2）

**目标**：支持通过 HTTP 连接远程 Server

**交付物**：
- `openviking/client/http.py` - HTTPClient
- `openviking/client/local.py` - LocalClient（重构）
- 修改 `async_client.py` 支持 `server_url` 参数

**验收标准**：
```python
# HTTP 模式
client = OpenViking(url="http://localhost:1933", api_key="test")
results = client.find("how to use")
```

---

#### T5: CLI 完整命令（P2）

**目标**：补全所有 CLI 命令

**交付物**：
- 会话命令：`session new/list/get/compress/extract`
- 文件系统命令：`tree`, `stat`, `mkdir`, `rm`, `mv`
- 搜索命令：`search`, `grep`, `glob`
- 关联命令：`link`, `unlink`, `relations`
- 导入导出：`export`, `import`
- 配置命令：`config show/init`
- 工具命令：`wait`, `status`

**验收标准**：
```bash
openviking --help  # 显示所有命令
openviking session new --user alice
openviking export viking://resources/docs/ ./backup.ovpack
```

---

#### T6: 集成测试（P3）

**目标**：端到端测试

**交付物**：
- `tests/integration/test_cli.py`
- `tests/integration/test_server.py`
- `tests/integration/test_http_client.py`

**验收标准**：
- 所有集成测试通过
- 覆盖主要使用场景

---

#### T7: 文档更新（P3）

**目标**：更新用户文档

**交付物**：
- README.md 更新（CLI 和 Server 使用说明）
- OpenAPI 文档（自动生成）
- CHANGELOG.md 更新

---

#### T8: Docker 部署（P1）

**目标**：提供 Docker 镜像和 docker-compose 配置

**交付物**：
- `Dockerfile` - 多阶段构建
- `docker-compose.yml` - 本地部署配置
- `.dockerignore` - 忽略文件
- GitHub Actions 自动构建发布

**验收标准**：
```bash
docker build -t openviking .
docker run -p 1933:1933 -e OPENAI_API_KEY=xxx openviking
curl http://localhost:1933/health
```

---

#### T9: MCP Server（P1）

**目标**：实现 Model Context Protocol 服务端，让 Claude 等 AI 可直接调用

**交付物**：
- `openviking/mcp/server.py` - MCP Server 主入口
- `openviking/mcp/tools.py` - Tool 定义（find, read, ls, abstract, overview）
- CLI 命令：`openviking mcp [--path <dir>]`

**验收标准**：
- Claude Desktop 可成功连接并调用 tools
- 支持 stdio 传输

---

#### T10: TypeScript SDK（P2）

**目标**：基于 HTTP API 实现 TypeScript/JavaScript 客户端

**交付物**：
- `openviking-js/` - 独立 npm 包
- 完整类型定义
- 错误处理
- README 和示例

**验收标准**：
```typescript
import { OpenViking } from '@openviking/sdk';
const client = new OpenViking({ url: 'http://localhost:1933', apiKey: 'xxx' });
const results = await client.find('how to configure');
```

---

#### T11: Golang SDK（P2）

**目标**：基于 HTTP API 实现 Golang 客户端

**交付物**：
- `openviking-go/` - 独立 Go 模块
- 完整类型定义
- 错误处理
- README 和示例

**验收标准**：
```go
client := openviking.NewClient(openviking.Config{URL: "http://localhost:1933", APIKey: "xxx"})
results, _ := client.Find("how to configure", nil)
```

### 8.4 关键文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `pyproject.toml` | 修改 | 添加依赖和入口点 |
| `openviking/exceptions.py` | 新增 | 异常类定义 |
| `openviking/cli/main.py` | 新增 | CLI 入口 |
| `openviking/server/app.py` | 新增 | FastAPI 应用入口 |
| `openviking/server/auth.py` | 新增 | API Key 认证 |
| `openviking/server/models.py` | 新增 | 响应模型、错误码定义 |
| `openviking/server/routers/*.py` | 新增 | API 路由 |
| `openviking/client/http.py` | 新增 | HTTP 客户端 |
| `openviking/async_client.py` | 修改 | 支持 HTTP 模式 |

### 8.5 验证方案

#### Bash CLI 验证
```bash
openviking config init
openviking serve --port 1933
openviking add-resource ./docs/ --wait
openviking ls viking://
openviking find "how to use"
```

#### HTTP API 验证
```bash
curl http://localhost:1933/health
curl -X POST http://localhost:1933/api/v1/resources \
  -H "X-API-Key: xxx" -d '{"path": "./docs"}'
curl "http://localhost:1933/api/v1/fs/ls?uri=viking://" \
  -H "X-API-Key: xxx"
```

#### Python CLI 验证
```python
from openviking import OpenViking
client = OpenViking(url="http://localhost:1933", api_key="xxx")
result = client.find("how to use")
print(result)
```
