# OpenViking Server/CLI 实现任务 Prompt

本文档包含各任务的 Agent Prompt，用于指导实现。

---

## T1: Service 层抽取

### 目标
从 `async_client.py` 抽取业务逻辑到独立的 Service 层，使业务逻辑与传输层解耦。

### 背景
当前 `openviking/async_client.py` 包含了所有业务逻辑，需要将其抽取到 `openviking/service/` 目录下，便于 HTTP Server 和 CLI 复用。

### 任务详情

1. **创建 Service 层目录结构**：
```
openviking/service/
├── __init__.py
├── core.py                  # OpenVikingService 主类
├── fs_service.py            # 文件系统操作
├── resource_service.py      # 资源导入、技能添加
├── search_service.py        # 语义搜索
├── session_service.py       # 会话管理
├── relation_service.py      # 关联管理
├── pack_service.py          # 导入导出
└── debug_service.py         # 调试服务
```

2. **抽取业务逻辑**：

- `FSService`: ls, tree, stat, mkdir, rm, mv, read, abstract, overview, grep, glob
- `ResourceService`: add_resource, add_skill, wait_processed
- `SearchService`: find, search
- `SessionService`: session, sessions, add_message, commit
- `RelationService`: link, unlink, relations
- `DebugService`: observer (ObserverService)
- `PackService`: export_ovpack, import_ovpack

3. **创建 OpenVikingService 主类**：
- 组合所有子 Service
- 管理 VikingFS、VectorIndex 等基础设施
- 提供统一的初始化和关闭方法

4. **修改 async_client.py**：
- 改为调用 Service 层
- 保持现有 API 不变（兼容层）

### 验收标准
- 现有单元测试全部通过
- `async_client.py` API 保持不变
- Service 层可独立使用

### 参考文件
- `openviking/async_client.py` - 现有实现
- `openviking/storage/viking_fs.py` - VikingFS 实现
- `design/server-cli-design.md` - 设计文档 4.1 节

---

## T2: HTTP Server 实现

### 目标
基于 FastAPI 实现 HTTP Server，提供 RESTful API。

### 背景
参考 `design/server-cli-design.md` 第七节 HTTP API 设计。

### 任务详情

1. **创建 Server 目录结构**：
```
openviking/server/
├── __init__.py
├── app.py                   # FastAPI 应用入口
├── bootstrap.py             # 服务启动器
├── config.py                # 服务器配置
├── auth.py                  # API Key 认证
├── dependencies.py          # 依赖注入
├── models.py                # 响应模型、错误码
└── routers/
    ├── __init__.py
    ├── resources.py         # /api/v1/resources
    ├── filesystem.py        # /api/v1/fs
    ├── content.py           # /api/v1/content
    ├── search.py            # /api/v1/search
    ├── relations.py         # /api/v1/relations
    ├── sessions.py          # /api/v1/sessions
    ├── pack.py              # /api/v1/pack
    ├── system.py            # /api/v1/system, /health
    └── debug.py             # /api/v1/debug
```

2. **实现统一响应格式**（参考设计文档 5.2 节）：
```python
class Response(BaseModel):
    status: str              # "ok" | "error"
    result: Optional[Any] = None
    error: Optional[ErrorInfo] = None
    time: float = 0.0
    usage: Optional[UsageInfo] = None
```

3. **实现 API Key 认证**：
- 支持 `X-API-Key` header
- 支持 `Authorization: Bearer` header

4. **实现所有 API 端点**（参考设计文档 7.3 节）：
- 资源管理: POST /api/v1/resources, POST /api/v1/skills
- 文件系统: GET /api/v1/fs/ls, GET /api/v1/fs/tree, etc.
- 内容读取: GET /api/v1/content/read, /abstract, /overview
- 搜索: POST /api/v1/search/find, /search, /grep, /glob
- 关联: POST /api/v1/relations/link, DELETE /api/v1/relations/link
- 会话: POST/GET/DELETE /api/v1/sessions
- 导入导出: POST /api/v1/pack/export, /import
- 系统: GET /health, GET /api/v1/system/status
- 调试: GET /api/v1/debug/status, GET /api/v1/debug/health

5. **实现服务启动器** (bootstrap.py)：
- 加载配置文件 `ov.conf`（通过 `--config` 参数或 `OPENVIKING_CONFIG_FILE` 环境变量指定）
- 初始化 OpenVikingService
- 启动 uvicorn 服务器

### 验收标准
```bash
# 启动服务
openviking serve --config ./ov.conf --port 8000

# 验证 API
curl http://localhost:8000/health
curl -X POST http://localhost:8000/api/v1/resources \
  -H "X-API-Key: test" -d '{"path": "./docs"}'
curl "http://localhost:8000/api/v1/fs/ls?uri=viking://" \
  -H "X-API-Key: test"
```

### 依赖
- T1 (Service 层) 必须先完成

### 参考文件
- `design/server-cli-design.md` - 第七节
- `openviking/service/` - Service 层（T1 产出）

---

## T3: CLI 基础框架

### 目标
使用 Typer 实现 CLI 核心命令。

### 背景
参考 `design/server-cli-design.md` 第六节 CLI 设计。

### 任务详情

1. **创建 CLI 目录结构**：
```
openviking/cli/
├── __init__.py
├── main.py                  # CLI 入口
├── output.py                # 输出格式化（JSON/Table/Plain）
└── config.py                # 配置管理（读取环境变量、配置文件）
```

2. **更新 pyproject.toml**：
```toml
[project.scripts]
openviking = "openviking_cli.cli.main:app"

[project.optional-dependencies]
cli = [
    "typer>=0.9.0",
    "rich>=13.0.0",
]
```

3. **实现核心命令**：
```bash
# 服务管理
openviking serve [--config <file>] [--port 1933] [--host 0.0.0.0]

# 调试命令
openviking status                         # 系统整体状态（包含 queue/vikingdb/vlm 组件状态）
openviking health                         # 快速健康检查

# 资源导入
openviking add-resource <path> [--to <uri>] [--wait] [--timeout N]

# 文件系统操作
openviking ls <uri> [--simple] [--recursive]

# 内容读取
openviking read <uri>
openviking abstract <uri>
openviking overview <uri>

# 搜索
openviking find <query> [<uri>] [--limit N] [--threshold F]
```

4. **实现配置管理**：
- CLI 连接信息通过 `ovcli.conf` 配置文件管理（url/api_key/user）
- 配置文件路径通过 `OPENVIKING_CLI_CONFIG_FILE` 环境变量指定
- 命令行参数（`--config`、`--host`、`--port`）优先于配置文件
- 不使用 `--url`、`--api-key` 等全局命令行选项

5. **实现输出格式化**：
- 支持 `--output json` 输出 JSON
- 默认使用 Rich 美化输出

### 验收标准
```bash
openviking --help
openviking serve --config ./ov.conf --port 1933
openviking add-resource ./docs/ --wait
openviking ls viking://resources/
openviking find "how to use"
```

### 依赖
- T1 (Service 层) 必须先完成

### 参考文件
- `design/server-cli-design.md` - 第六节
- `openviking/service/` - Service 层（T1 产出）

---

## T4: Python SDK

### 目标
实现 HTTPClient，支持通过 HTTP 连接远程 Server。

### 背景
参考 `design/server-cli-design.md` 3.1 节分层架构和 6.2 节 Python SDK。

### 任务详情

1. **创建 Client 目录结构**：
```
openviking/client/
├── __init__.py
├── base.py                  # 客户端基类接口
├── local.py                 # LocalClient（直接调用 Service）
└── http.py                  # HTTPClient（HTTP 调用 Server）
```

2. **定义客户端接口** (base.py)：
```python
class BaseClient(ABC):
    @abstractmethod
    async def ls(self, uri: str, simple: bool = False, recursive: bool = False) -> Response: ...
    @abstractmethod
    async def find(self, query: str, target_uri: str = "viking://", limit: int = 10) -> Response: ...
    # ... 所有方法
```

3. **实现 HTTPClient** (http.py)：
- 使用 httpx 进行 HTTP 调用
- 实现所有 API 端点的客户端方法
- 处理认证（API Key）
- 处理错误响应，转换为异常
- 实现 debug 方法（get_status, is_healthy）：
```python
def get_status(self) -> SystemStatus:
    """Get system status (same as LocalClient)."""
    response = self._get("/api/v1/debug/status")
    return SystemStatus(**response["result"])

def is_healthy(self) -> bool:
    """Quick health check (same as LocalClient)."""
    response = self._get("/api/v1/debug/health")
    return response["healthy"]
```

4. **实现 LocalClient** (local.py)：
- 直接调用 OpenVikingService
- 包装返回值为统一的 Response 格式

5. **修改 OpenViking 类**：
```python
class OpenViking:
    def __init__(
        self,
        path: Optional[str] = None,      # 嵌入式模式
        url: Optional[str] = None,        # HTTP 模式
        api_key: Optional[str] = None,
        user: Optional[str] = None,
    ):
        if url:
            self._client = HTTPClient(url, api_key)
        else:
            self._client = LocalClient(path)
```

### 验收标准
```python
# HTTP 模式
client = OpenViking(url="http://localhost:8000", api_key="test")
results = client.find("how to use")

# 嵌入式模式（保持兼容）
client = OpenViking(path="./data")
results = client.find("how to use")
```

### 依赖
- T2 (HTTP Server) 必须先完成

### 参考文件
- `design/server-cli-design.md` - 3.1 节、6.2 节
- `openviking/server/` - HTTP Server（T2 产出）

---

## T5: CLI 完整命令

### 目标
补全所有 CLI 命令。

### 背景
参考 `design/server-cli-design.md` 6.1 节命令列表。

### 任务详情

1. **会话管理命令**：
```bash
openviking session new [--user <name>]
openviking session list
openviking session get <id>
openviking session commit <id>
openviking session delete <id>
```

2. **文件系统命令**：
```bash
openviking tree <uri>
openviking stat <uri>
openviking mkdir <uri>
openviking rm <uri> [--recursive]
openviking mv <from> <to>
```

3. **搜索命令**：
```bash
openviking search <query> [<uri>] [--session-id ID] [--limit N]
openviking grep <uri> <pattern> [-i]
openviking glob <pattern> [<uri>]
```

4. **关联命令**：
```bash
openviking link <from> <to>... [--reason TEXT]
openviking unlink <from> <to>
openviking relations <uri>
```

5. **导入导出命令**：
```bash
openviking export <uri> <file.ovpack>
openviking import <file.ovpack> <uri> [--force] [--no-vectorize]
```

6. **配置命令**：
```bash
openviking config show
openviking config init
```

7. **工具命令**：
```bash
openviking wait [--timeout N]
openviking add-skill <file> [--wait]
```

### 验收标准
```bash
openviking --help  # 显示所有命令
openviking session new --user alice
openviking session list
openviking export viking://resources/docs/ ./backup.ovpack
openviking config show
```

### 依赖
- T3 (CLI 基础框架) 必须先完成

### 参考文件
- `design/server-cli-design.md` - 6.1 节
- `openviking/cli/main.py` - CLI 基础框架（T3 产出）

---

## T6: 集成测试

### 目标
编写端到端集成测试，验证 CLI、Server、Python SDK 的完整工作流程。

### 背景
参考 `design/server-cli-design.md` 8.5 节验证方案。

### 任务详情

1. **创建测试文件**：
```
tests/integration/
├── test_cli.py              # CLI 命令测试
├── test_server.py           # HTTP Server 测试
└── test_http_client.py      # Python SDK 测试
```

2. **CLI 测试** (test_cli.py)：
```python
def test_serve_command():
    """测试 serve 命令启动服务"""
    ...

def test_add_resource_command():
    """测试 add-resource 命令"""
    ...

def test_ls_command():
    """测试 ls 命令"""
    ...

def test_find_command():
    """测试 find 命令"""
    ...

def test_session_commands():
    """测试 session 子命令"""
    ...
```

3. **Server 测试** (test_server.py)：
```python
@pytest.fixture
async def server():
    """启动测试服务器"""
    ...

async def test_health_endpoint(server):
    """测试 /health 端点"""
    ...

async def test_resources_api(server):
    """测试资源 API"""
    ...

async def test_search_api(server):
    """测试搜索 API"""
    ...

async def test_auth_required(server):
    """测试认证"""
    ...
```

4. **Python SDK 测试** (test_http_client.py)：
```python
async def test_http_client_find():
    """测试 HTTP 客户端 find 方法"""
    ...

async def test_http_client_session():
    """测试 HTTP 客户端会话管理"""
    ...

async def test_http_client_error_handling():
    """测试错误处理"""
    ...
```

5. **端到端工作流测试**：
```python
async def test_full_workflow():
    """完整工作流测试"""
    # 1. 启动 Server
    # 2. 使用 Python SDK 连接
    # 3. 添加资源
    # 4. 搜索
    # 5. 会话管理
    # 6. 导出/导入
    ...
```

### 验收标准
- 所有集成测试通过
- 覆盖主要使用场景
- 测试报告显示覆盖率

### 依赖
- T4 (Python SDK) 必须先完成
- T5 (CLI 完整命令) 必须先完成

### 参考文件
- `tests/` - 现有测试结构
- `tests/conftest.py` - pytest 配置

---

## T7: 文档更新

### 目标
更新用户文档，包括 README、API 文档、CHANGELOG。

### 背景
参考 `design/server-cli-design.md` 6.3 节使用指南。

### 任务详情

1. **更新 README.md**：
- 添加 CLI 安装和使用说明
- 添加 Server 部署说明
- 添加 HTTP API 使用示例
- 更新架构图

2. **生成 OpenAPI 文档**：
- FastAPI 自动生成 `/docs` 和 `/redoc`
- 确保所有端点有完整的描述和示例

3. **更新 CHANGELOG.md**：
- 记录新增的 CLI 功能
- 记录新增的 HTTP API
- 记录 SDK 的 HTTP 模式支持

4. **添加使用示例**：
```markdown
## CLI 使用

### 启动服务
\`\`\`bash
openviking serve --config ./ov.conf --port 8000
\`\`\`

### 添加资源
\`\`\`bash
openviking add-resource ./docs/ --wait
\`\`\`

### 语义搜索
\`\`\`bash
openviking find "how to configure"
\`\`\`

## HTTP API 使用

### 健康检查
\`\`\`bash
curl http://localhost:8000/health
\`\`\`

### 搜索
\`\`\`bash
curl -X POST http://localhost:8000/api/v1/search/find \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"query": "how to configure", "limit": 10}'
\`\`\`

## Python SDK (HTTP 模式)

\`\`\`python
from openviking import OpenViking

client = OpenViking(url="http://localhost:8000", api_key="your-key")
results = client.find("how to configure")
\`\`\`
```

### 验收标准
- README 包含完整的 CLI/Server/API 使用说明
- OpenAPI 文档可访问且完整
- CHANGELOG 记录所有变更

### 依赖
- T6 (集成测试) 必须先完成

### 参考文件
- `design/server-cli-design.md` - 6.3 节
- `README.md` - 现有文档

---

## T8: Docker 部署

### 目标
提供 Docker 镜像和 docker-compose 配置，简化部署流程。

### 背景
Docker 是现代应用部署的标准方式，降低用户部署门槛。

### 任务详情

1. **创建 Dockerfile**：
```dockerfile
FROM python:3.11-slim

WORKDIR /app

# 安装依赖
COPY pyproject.toml .
RUN pip install .

# 复制代码
COPY openviking/ openviking/

# 暴露端口
EXPOSE 8000

# 启动命令
CMD ["openviking", "serve", "--host", "0.0.0.0", "--port", "8000"]
```

2. **创建 docker-compose.yml**：
```yaml
version: '3.8'

services:
  openviking:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - openviking-data:/data
      - ./ov.conf:/etc/openviking/ov.conf
    environment:
      - OPENVIKING_CONFIG_FILE=/etc/openviking/ov.conf
      - OPENAI_API_KEY=${OPENAI_API_KEY}
    restart: unless-stopped

volumes:
  openviking-data:
```

3. **创建 .dockerignore**：
```
.git
.venv
__pycache__
*.pyc
.pytest_cache
.mypy_cache
tests/
docs/
*.md
```

4. **多阶段构建优化**（可选）：
- 使用多阶段构建减小镜像体积
- 分离构建依赖和运行依赖

5. **发布到 Docker Hub / GitHub Container Registry**：
- 配置 GitHub Actions 自动构建和发布
- 支持多架构（amd64, arm64）

### 验收标准
```bash
# 构建镜像
docker build -t openviking .

# 运行容器
docker run -p 8000:8000 -e OPENAI_API_KEY=xxx openviking

# 使用 docker-compose
docker-compose up -d

# 验证服务
curl http://localhost:8000/health
```

### 依赖
- T2 (HTTP Server) 必须先完成

### 参考文件
- `pyproject.toml` - 依赖配置
- `openviking/server/` - Server 实现

---

## T9: MCP Server 实现

### 目标
实现 Model Context Protocol (MCP) 服务端，让 Claude 等 AI 可以直接调用 OpenViking。

### 背景
MCP 是 Anthropic 推出的标准协议，允许 AI 模型与外部工具交互。实现 MCP Server 可以让 OpenViking 无缝集成到 Claude Desktop、Cursor 等支持 MCP 的应用中。

### 任务详情

1. **创建 MCP Server 目录结构**：
```
openviking/mcp/
├── __init__.py
├── server.py                # MCP Server 主入口
├── tools.py                 # Tool 定义
└── resources.py             # Resource 定义
```

2. **定义 MCP Tools**：
```python
# 核心 Tools
tools = [
    {
        "name": "openviking_find",
        "description": "Semantic search in OpenViking context database",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "uri": {"type": "string", "description": "Target URI scope"},
                "limit": {"type": "integer", "description": "Max results"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "openviking_read",
        "description": "Read content from OpenViking",
        "inputSchema": {
            "type": "object",
            "properties": {
                "uri": {"type": "string", "description": "Resource URI"}
            },
            "required": ["uri"]
        }
    },
    {
        "name": "openviking_ls",
        "description": "List directory contents in OpenViking",
        "inputSchema": {
            "type": "object",
            "properties": {
                "uri": {"type": "string", "description": "Directory URI"}
            },
            "required": ["uri"]
        }
    },
    # ... 更多 tools
]
```

3. **实现 MCP Server**：
```python
from mcp.server import Server
from mcp.types import Tool, TextContent

server = Server("openviking")

@server.list_tools()
async def list_tools() -> list[Tool]:
    return tools

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "openviking_find":
        result = await client.find(arguments["query"], ...)
        return [TextContent(type="text", text=json.dumps(result))]
    # ... 处理其他 tools
```

4. **支持 stdio 和 SSE 传输**：
- stdio: 用于 Claude Desktop 等本地应用
- SSE: 用于 Web 应用

5. **配置文件示例** (claude_desktop_config.json)：
```json
{
  "mcpServers": {
    "openviking": {
      "command": "openviking",
      "args": ["mcp", "--path", "/path/to/data"]
    }
  }
}
```

6. **添加 CLI 命令**：
```bash
openviking mcp [--path <dir>] [--transport stdio|sse]
```

### 验收标准
- MCP Server 可以通过 stdio 启动
- Claude Desktop 可以成功连接并调用 tools
- 所有核心功能（find, read, ls, abstract, overview）可通过 MCP 调用

### 依赖
- T1 (Service 层) 必须先完成
- 建议 T3 (CLI 基础框架) 先完成

### 参考文件
- [MCP Specification](https://spec.modelcontextprotocol.io/)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- `openviking/service/` - Service 层

---

## T10: TypeScript SDK

### 目标
基于 HTTP API 实现 TypeScript/JavaScript 客户端 SDK。

### 背景
TypeScript/JavaScript 是 Web 开发的主流语言，覆盖前端和 Node.js 后端开发者。

### 任务详情

1. **创建项目结构**：
```
openviking-js/
├── package.json
├── tsconfig.json
├── src/
│   ├── index.ts             # 主入口
│   ├── client.ts            # OpenViking 客户端
│   ├── types.ts             # 类型定义
│   └── errors.ts            # 错误类
├── tests/
│   └── client.test.ts
└── README.md
```

2. **实现客户端类**：
```typescript
// src/client.ts
export interface OpenVikingConfig {
  url: string;
  apiKey: string;
  user?: string;
  agent?: string;
}

export class OpenViking {
  private config: OpenVikingConfig;

  constructor(config: OpenVikingConfig) {
    this.config = config;
  }

  async find(query: string, options?: FindOptions): Promise<SearchResult> {
    const response = await fetch(`${this.config.url}/api/v1/search/find`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': this.config.apiKey,
      },
      body: JSON.stringify({ query, ...options }),
    });
    return this.handleResponse(response);
  }

  async ls(uri: string, options?: LsOptions): Promise<LsResult> { ... }
  async read(uri: string): Promise<ReadResult> { ... }
  async abstract(uri: string): Promise<ReadResult> { ... }
  async overview(uri: string): Promise<ReadResult> { ... }
  // ... 其他方法
}
```

3. **类型定义**：
```typescript
// src/types.ts
export interface Response<T> {
  status: 'ok' | 'error';
  result?: T;
  error?: ErrorInfo;
  time: number;
}

export interface SearchResult {
  items: SearchItem[];
  query: string;
  total: number;
}

export interface SearchItem {
  uri: string;
  score: number;
  content: string;
  abstract?: string;
}

// ... 其他类型
```

4. **错误处理**：
```typescript
// src/errors.ts
export class OpenVikingError extends Error {
  code: string;
  details?: Record<string, unknown>;

  constructor(code: string, message: string, details?: Record<string, unknown>) {
    super(message);
    this.code = code;
    this.details = details;
  }
}

export class NotFoundError extends OpenVikingError {}
export class InvalidURIError extends OpenVikingError {}
// ... 其他错误类
```

5. **发布配置**：
```json
// package.json
{
  "name": "@openviking/sdk",
  "version": "1.0.0",
  "main": "dist/index.js",
  "types": "dist/index.d.ts",
  "scripts": {
    "build": "tsc",
    "test": "jest"
  }
}
```

### 验收标准
```typescript
import { OpenViking } from '@openviking/sdk';

const client = new OpenViking({
  url: 'http://localhost:8000',
  apiKey: 'your-api-key',
});

// 搜索
const results = await client.find('how to configure');
console.log(results.items);

// 读取
const content = await client.read('viking://resources/doc.md');
console.log(content);
```

### 依赖
- T2 (HTTP Server) 必须先完成

### 参考文件
- `design/server-cli-design.md` - 7.3 节 API 端点
- `openviking/server/models.py` - 响应模型定义

---

## T11: Golang SDK

### 目标
基于 HTTP API 实现 Golang 客户端 SDK。

### 背景
Go 是云原生和基础设施领域的主流语言，覆盖 Kubernetes 生态和后端开发者。

### 任务详情

1. **创建项目结构**：
```
openviking-go/
├── go.mod
├── go.sum
├── client.go                # 主客户端
├── types.go                 # 类型定义
├── errors.go                # 错误定义
├── client_test.go           # 测试
└── README.md
```

2. **实现客户端**：
```go
// client.go
package openviking

import (
    "bytes"
    "encoding/json"
    "net/http"
)

type Config struct {
    URL    string
    APIKey string
    User   string
    Agent  string
}

type Client struct {
    config     Config
    httpClient *http.Client
}

func NewClient(config Config) *Client {
    return &Client{
        config:     config,
        httpClient: &http.Client{},
    }
}

func (c *Client) Find(query string, opts *FindOptions) (*SearchResult, error) {
    body := map[string]interface{}{
        "query": query,
    }
    if opts != nil {
        if opts.URI != "" {
            body["uri"] = opts.URI
        }
        if opts.Limit > 0 {
            body["limit"] = opts.Limit
        }
    }

    resp, err := c.post("/api/v1/search/find", body)
    if err != nil {
        return nil, err
    }

    var result SearchResult
    if err := json.Unmarshal(resp.Result, &result); err != nil {
        return nil, err
    }
    return &result, nil
}

func (c *Client) Ls(uri string, opts *LsOptions) (*LsResult, error) { ... }
func (c *Client) Read(uri string) (*ReadResult, error) { ... }
func (c *Client) Abstract(uri string) (*ReadResult, error) { ... }
func (c *Client) Overview(uri string) (*ReadResult, error) { ... }
// ... 其他方法
```

3. **类型定义**：
```go
// types.go
package openviking

import "encoding/json"

type Response struct {
    Status string          `json:"status"`
    Result json.RawMessage `json:"result,omitempty"`
    Error  *ErrorInfo      `json:"error,omitempty"`
    Time   float64         `json:"time"`
}

type ErrorInfo struct {
    Code    string                 `json:"code"`
    Message string                 `json:"message"`
    Details map[string]interface{} `json:"details,omitempty"`
}

type SearchResult struct {
    Items []SearchItem `json:"items"`
    Query string       `json:"query"`
    Total int          `json:"total"`
}

type SearchItem struct {
    URI      string  `json:"uri"`
    Score    float64 `json:"score"`
    Content  string  `json:"content"`
    Abstract string  `json:"abstract,omitempty"`
}

type FindOptions struct {
    URI       string
    Limit     int
    Threshold float64
}

// ... 其他类型
```

4. **错误处理**：
```go
// errors.go
package openviking

import "fmt"

type OpenVikingError struct {
    Code    string
    Message string
    Details map[string]interface{}
}

func (e *OpenVikingError) Error() string {
    return fmt.Sprintf("[%s] %s", e.Code, e.Message)
}

var (
    ErrNotFound      = &OpenVikingError{Code: "NOT_FOUND"}
    ErrInvalidURI    = &OpenVikingError{Code: "INVALID_URI"}
    ErrUnavailable   = &OpenVikingError{Code: "UNAVAILABLE"}
    // ... 其他错误
)
```

5. **HTTP 请求封装**：
```go
func (c *Client) post(path string, body interface{}) (*Response, error) {
    jsonBody, err := json.Marshal(body)
    if err != nil {
        return nil, err
    }

    req, err := http.NewRequest("POST", c.config.URL+path, bytes.NewBuffer(jsonBody))
    if err != nil {
        return nil, err
    }

    req.Header.Set("Content-Type", "application/json")
    req.Header.Set("X-API-Key", c.config.APIKey)

    resp, err := c.httpClient.Do(req)
    if err != nil {
        return nil, err
    }
    defer resp.Body.Close()

    var response Response
    if err := json.NewDecoder(resp.Body).Decode(&response); err != nil {
        return nil, err
    }

    if response.Status == "error" {
        return nil, &OpenVikingError{
            Code:    response.Error.Code,
            Message: response.Error.Message,
            Details: response.Error.Details,
        }
    }

    return &response, nil
}
```

### 验收标准
```go
package main

import (
    "fmt"
    "github.com/openviking/openviking-go"
)

func main() {
    client := openviking.NewClient(openviking.Config{
        URL:    "http://localhost:8000",
        APIKey: "your-api-key",
    })

    // 搜索
    results, err := client.Find("how to configure", &openviking.FindOptions{
        Limit: 10,
    })
    if err != nil {
        panic(err)
    }
    for _, item := range results.Items {
        fmt.Printf("%s: %.2f\n", item.URI, item.Score)
    }

    // 读取
    content, err := client.Read("viking://resources/doc.md")
    if err != nil {
        panic(err)
    }
    fmt.Println(content.Content)
}
```

### 依赖
- T2 (HTTP Server) 必须先完成

### 参考文件
- `design/server-cli-design.md` - 7.3 节 API 端点
- `openviking/server/models.py` - 响应模型定义

---

## 附录：异常类定义

所有任务共用的异常类定义，应在 T1 中创建 `openviking/exceptions.py`：

```python
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

---

## 附录：错误码映射

```python
ERROR_CODE_TO_HTTP_STATUS = {
    "OK": 200,
    "INVALID_ARGUMENT": 400,
    "INVALID_URI": 400,
    "NOT_FOUND": 404,
    "ALREADY_EXISTS": 409,
    "PERMISSION_DENIED": 403,
    "UNAUTHENTICATED": 401,
    "RESOURCE_EXHAUSTED": 429,
    "FAILED_PRECONDITION": 412,
    "ABORTED": 409,
    "DEADLINE_EXCEEDED": 504,
    "UNAVAILABLE": 503,
    "INTERNAL": 500,
    "UNIMPLEMENTED": 501,
    "PROCESSING": 202,
    "EMBEDDING_FAILED": 500,
    "VLM_FAILED": 500,
    "SESSION_EXPIRED": 410,
}
```
