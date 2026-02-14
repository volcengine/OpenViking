# API 接口文档

## 概述

Nanobot 是一个 CLI 工具，不提供传统的 REST API。本文档描述内部模块间的接口规范以及 CLI 命令接口。

## 1. 内部模块接口

### 1.1 MessageBus 接口

**位置**: `vikingbot/bus/queue.py`

**职责**: 异步消息队列，解耦消息生产者和消费者

```python
class MessageBus:
    """异步消息队列"""
    
    async def publish_inbound(self, msg: InboundMessage) -> None:
        """
        发布入站消息到队列
        
        Args:
            msg: 从聊天平台接收的消息
        """
        
    async def publish_outbound(self, msg: OutboundMessage) -> None:
        """
        发布出站消息到队列
        
        Args:
            msg: 要发送到聊天平台的消息
        """
        
    async def consume_inbound(self) -> InboundMessage:
        """
        消费入站消息（阻塞直到有消息）
        
        Returns:
            下一个入站消息
        """
        
    async def consume_outbound(self) -> OutboundMessage:
        """
        消费出站消息（阻塞直到有消息）
        
        Returns:
            下一个出站消息
        """
```

### 1.2 ToolRegistry 接口

**位置**: `vikingbot/agent/tools/registry.py`

**职责**: 工具注册、查找和执行

```python
class ToolRegistry:
    """工具注册表"""
    
    def register(self, tool: Tool) -> None:
        """
        注册一个工具
        
        Args:
            tool: 工具实例
        """
        
    def unregister(self, name: str) -> None:
        """
        注销一个工具
        
        Args:
            name: 工具名称
        """
        
    def get(self, name: str) -> Tool | None:
        """
        获取工具实例
        
        Args:
            name: 工具名称
            
        Returns:
            工具实例或 None
        """
        
    def has(self, name: str) -> bool:
        """
        检查工具是否已注册
        
        Args Args:
            name: 工具名称
            
        Returns:
            True 如果已注册
        """
        
    def get_definitions(self) -> list[dict[str, Any]]:
        """
        获取所有工具的 OpenAI 格式定义
        
        Returns:
            工具定义列表
        """
        
    async def execute(self, name: str, params: dict[str, Any]) -> str:
        """
        执行工具
        
        Args:
            name: 工具名称
            params: 工具参数
            
        Returns:
            执行结果字符串
        """
        
    @property
    def tool_names(self) -> list[str]:
        """
       获取所有已注册的工具名称
        
        Returns:
            工具名称列表
        """
```

### 1.3 Tool 接口

**位置**: `vikingbot/agent/tools/base.py`

**职责**: 工具的抽象基类

```python
class Tool(ABC):
    """工具抽象基类"""
    
    abstract property
    def name(self) -> str:
        """
        工具名称（用于函数调用）
        
        Returns:
            工具名称
        """
        
    abstract property
    def description(self) -> str:
        """
        工具描述
        
        Returns:
            工具描述
        """
        
    abstract property
    def parameters(self) -> dict[str, Any]:
        """
        JSON Schema 格式的工具参数
        
        Returns:
            参数定义
        """
        
    abstract async def
    def execute(self, **kwargs: Any) -> str:
        """
        执行工具
        
        Args:
            **kwargs: 工具特定参数
            
        Returns:
            执行结果字符串
        """
        
    def validate_params(self, params: dict[str, Any]) -> list[str]:
        """
        验证工具参数
        
        Args:
            params: 要验证的参数
            
        Returns:
            错误列表（空表示有效）
        """
        
    def to_schema(self) -> dict[str, Any]:
        """
        转换为 OpenAI 函数模式格式
        
        Returns:
            函数模式字典
        """
```

### 1.4 LLMProvider 接口

**位置**: `vikingbot/providers/base.py`

**职责**: LLM 提供商的抽象基类

```python
class LLMProvider(ABC):
    """LLM 提供商抽象基类"""
    
    def __init__(self, api_key: str | None = None, api_base: str | None = None):
        """
        初始化提供商
        
        Args:
            api_key: API 密钥
            api_base: API 基础 URL
        """
        
    abstract async def
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        """
        发送聊天完成请求
        
        Args:
            messages: 消息列表（包含 role 和 content）
            tools: 可选的工具定义列表
            model: 模型标识符
            max_tokens: 最大响应 token 数
            temperature: 采样温度
            
        Returns:
            LLMResponse 对象
        """
        
    abstract def
    def get_default_model(self) -> str:
        """
        获取默认模型
        
        Returns:
            默认模型名称
        """
```

### 1.5 BaseChannel 接口

**位置**: `vikingbot/channels/base.py`

**职责**: 聊天通道的抽象基类

```python
class BaseChannel(ABC):
    """聊天通道抽象基类"""
    
    name: str = "base"
    
    def __init__(self, config: Any, bus: MessageBus):
        """
        初始化通道
        
        Args:
            config: 通道特定配置
            bus: 消息总线
        """
        
    abstract async def
    def start(self) -> None:
        """
        启动通道并开始监听消息
        
        应该是一个长期运行的异步任务：
        1. 连接到聊天平台
        2. 监听入站消息
        3. 通过 _handle_message() 转发消息到总线
        """
        
    abstract async def
    def stop(self) -> None:
        """
        停止通道并清理资源
        """
        
    abstract async def
    def send(self, msg: OutboundMessage) -> None:
        """
        通过此通道发送消息
        
        Args:
            msg: 要发送的消息
        """
        
    def is_allowed(self, sender_id: str) -> bool:
        """
        检查发送者是否被允许使用此机器人
        
        Args:
            sender_id: 发送者标识符
            
        Returns:
            True 如果允许
        """
        
    async def
    def _handle_message(
        self,
        sender_id: str,
        chat_id: str,
        content: str,
        media: list[str] | None = None,
        metadata: dict[str, Any] | None = None
    ) -> None:
        """
        处理来自聊天平台的入站消息
        
        Args:
            sender_id: 发送者标识符
            chat_id: 聊天/频道标识符
            content: 消息文本内容
            media: 可选的媒体 URL 列表
            metadata: 可选的通道特定元数据
        """
        
    @property
    def is_running(self) -> bool:
        """
        检查通道是否正在运行
        
        Returns:
            True 如果正在运行
        """
```

### 1.6 SessionManager 接口

**位置**: `vikingbot/session/manager.py`

**职责**: 会话生命周期管理

```python
class SessionManager:
    """会话管理器"""
    
    def __init__(self, workspace: Path):
        """
        初始化会话管理器
        
        Args:
            workspace: 工作空间路径
        """
        
    def get_or_create(self, key: str) -> Session:
        """
        获取现有会话或创建新会话
        
        Args:
            key: 会话会话键（通常是 channel:chat_id）
            
        Returns:
            会话对象
        """
        
    def save(self, session: Session) -> None:
        """
        保存会话到磁盘
        
        Args:
            session: 要保存的会话
        """
        
    def delete(self, key: str) -> bool:
        """
        删除会话
        
        Args:
            key: 会话键
            
        Returns:
            True 如果已删除，False 如果未找到
        """
        
    def list_sessions(self) -> list[dict[str, Any]]:
        """
        列出所有会话
        
        Returns:
            会话信息字典列表
        """
```

### 1.7 CronService 接口

**位置**: `vikingbot/cron/service.py`

**职责**: 定时任务管理

```python
class CronService:
    """定时任务服务"""
    
    def __init__(
        self,
        store_path: Path,
        on_job: Callable[[CronJob], Coroutine[Any, Any, str | None]] | None = None
    ):
        """
        初始化 Cron 服务
        
        Args:
            store_path: 任务存储文件路径
            on_job: 任务执行回调函数
        """
        
    async def start(self) -> None:
        """启动 Cron 服务"""
        
    def stop(self) -> None:
        """停止 Cron 服务"""
        
    def list_jobs(self, include_disabled: bool = False) -> list[CronJob]:
        """
        列出所有任务
        
        Args:
            include_disabled: 是否包含禁用的任务
            
        Returns:
            任务列表
        """
        
    def add_job(
        self,
        name: str,
        schedule: CronSchedule,
        message: str,
        deliver: bool = False,
        channel: str | None = None,
        to: str | None = None,
        delete_after_run: bool = False,
    ) -> CronJob:
        """
        添加新任务
        
        Args:
            name: 任务名称
            schedule: 调度配置
            message: 要发送的消息
            deliver: 是否投递到聊天通道
            channel: 目标通道
            to: 目标用户 ID
            delete_after_run: 执行后是否删除
            
        Returns:
            创建的任务
        """
        
    def remove_job(self, job_id: str) -> bool:
        """
        删除任务
        
        Args:
            job_id: 任务 ID
            
        Returns:
            True 如果已删除
        """
        
    def enable_job(self, job_id: str, enabled: bool = True) -> CronJob | None:
        """
        启用或禁用任务
        
        Args:
            job_id: 任务 ID
            enabled: 是否启用
            
        Returns:
            更新后的任务或 None
        """
        
    async def run_job(self, job_id: str, force: bool = False) -> bool:
        """
        手动运行任务
        
        Args:
            job_id: 任务 ID
            force: 是否强制运行（即使禁用）
            
        Returns:
            True 如果已运行
        """
        
    def status(self) -> dict:
        """
        获取服务状态
        
        Returns:
            状态字典
        """
```

## 2. CLI 命令接口

### 2.1 vikingbot onboard

**描述**: 初始化配置和工作空间

**用法**:
```bash
vikingbot onboard
```

**行为**:
1. 创建 `~/.vikingbot/config.json`（如果不存在）
2. 创建工作空间目录
3. 创建默认 Bootstrap 文件
4. 显示设置说明

### 2.2 vikingbot agent

**描述**: 与代理聊天

**用法**:
```bash
# 单条消息
vikingbot agent -m "Hello!"

# 交互式模式
vikingbot agent

# 禁用 Markdown 渲染
vikingbot agent --no-markdown

# 显示运行时日志
vikingbot agent --logs
```

**选项**:
| 选项 | 描述 |
|------|------|
| `-m, --message` | 发送单条消息 |
| `--no-markdown` | 显示纯文本回复 |
| `--logs` | 显示运行时日志 |

**交互式命令**:
| 命令 | 描述 |
|------|------|
| `exit`, `quit`, `/exit`, `/quit`, `:q` | 退出交互式模式 |
| `/new` | 开始新会话 |
| `/help` | 显示可用命令 |

### 2.3 vikingbot gateway

**描述**: 启动网关（连接所有启用的聊天通道）

**用法**:
```bash
vikingbot gateway
```

**行为**:
1. 初始化所有启用的通道
2. 启动消息总线
3. 启动通道监听
4. 运行直到手动停止

### 2.4 vikingbot status

**描述**: 显示系统状态

**用法**:
```bash
vikingbot status
```

**输出**:
- 配置的 LLM 提供商
- 启用的聊天通道
- 工作空间路径
- 会话统计

### 2.5 vikingbot channels

**子命令**:

#### channels login

**描述**: 链接 WhatsApp（扫描 QR 码）

**用法**:
```bash
vikingbot channels login
```

#### channels status

**描述**: 显示通道状态

**用法**:
```bash
vikingbot channels status
```

**输出**:
- 每个通道的启用状态
- 每个通道的运行状态

### 2.6 vikingbot cron

**子命令**:

#### cron add

**描述**: 添加定时任务

**用法**:
```bash
# Cron 表达式
vikingbot cron add --name "daily" --message "Good morning!" --cron "0 9 * * *"

# 间隔执行
vikingbot cron add --name "hourly" --message "Check status" --every 3600

# 指定时间执行
vikingbot cron add --name "reminder" --message "Meeting at 3pm" --at "2026-02-13T15:00:00"
```

**选项**:
| 选项 | 描述 |
|------|------|
| `--name` | 任务名称 |
| `--message` | 要发送的消息 |
| `--cron` | Cron 表达式 |
| `--every` | 间隔（秒）|
| `--at` | 指定时间（ISO 8601）|
| `--deliver` | 是否投递到聊天通道 |
| `--to` | 目标用户 ID |
| `--channel` | 目标通道 |

#### cron list

**描述**: 列出所有定时任务

**用法**:
```bash
vikingbot cron list
```

#### cron remove

**描述**: 删除定时任务

**用法**:
```bash
vikingbot cron remove <job_id>
```

## 3. 工具接口规范

### 3.1 内置工具

#### read_file

**描述**: 读取文件内容

**参数**:
```json
{
  "type": "object",
  "properties": {
    "filePath": {
      "type": "string",
      "description": "要读取的文件路径"
    },
    "offset": {
      "type": "integer",
      "description": "起始行号（1-indexed）",
      "default": 1
    },
    "limit": {
      "type": "integer",
      "description": "最大读取行数",
      "default": 2000
    }
  },
  "required": ["filePath"]
}
```

**返回**: 文件内容字符串

#### write_file

**描述**: 写入文件

**参数**:
```json
{
  "type": "object",
  "properties": {
    "content": {
      "type": "string",
      "description": "要写入的内容"
    },
    "filePath": {
      "type": "string",
      "description": "目标文件路径"
    }
  },
  "required": ["content", "filePath"]
}
```

**返回**: 成功消息字符串

#### edit_file

**描述**: 编辑文件（精确字符串替换）

**参数**:
```json
{
  "type": "object",
  "properties": {
    "filePath": {
      "type": "string",
      "description": "要编辑的文件路径"
    },
    "oldString": {
      "type": "string",
      "description": "要替换的旧字符串"
    },
    "newString": {
      "type": "string",
      "description": "新字符串"
    },
    "replaceAll": {
      "type": "boolean",
      "description": "是否替换所有出现",
      "default": false
    }
  },
  "required": ["filePath", "oldString", "newString"]
}
```

**返回**: 成功消息字符串

#### list_dir

**描述**: 列出目录内容

**参数**:
```json
{
  "type": "object",
  "properties": {
    "path": {
      "type": "string",
      "description": "目录路径"
    }
  },
  "required": ["path"]
}
```

**返回**: 目录内容字符串

#### exec

**描述**: 执行 Shell 命令

**参数**:
```json
{
  "type": "object",
  "properties": {
    "command": {
      "type": "string",
      "description": "要执行的命令"
    },
    "timeout": {
      "type": "integer",
      "description": "超时时间（毫秒）",
      "default": 120000
    }
  },
  "required": ["command"]
}
```

**返回**: 命令输出字符串

#### web_search

**描述**: Web 搜索

**参数**:
```json
{
  "type": "object",
  "properties": {
    "query": {
      "type": "string",
      "description": "搜索查询"
    },
    "numResults": {
      "type": "integer",
      "description": "结果数量",
      "default": 8
    }
  },
  "required": ["query"]
}
```

**返回**: 搜索结果字符串

#### web_fetch

**描述**: 获取网页内容

**参数**:
```json
{
  "type": "object",
  "properties": {
    "url": {
      "type": "string",
      "description": "要获取的 URL"
    },
    "format": {
      "type": "string",
      "enum": ["text", "markdown", "html"],
      "description": "返回格式",
      "default": "markdown"
    }
  },
  "required": ["url"]
}
```

**返回**: 网页内容字符串

#### message

**描述**: 发送消息到聊天平台

**参数**:
```json
{
  "type": "object",
  "properties": {
    "content": {
      "type": "string",
      "description": "消息内容"
    },
    "channel": {
      "type": "string",
      "description": "目标通道（telegram, discord 等）"
    },
    "chatId": {
      "type": "string",
      "description": "目标聊天 ID"
    }
  },
  "required": ["content", "channel", "chatId"]
}
```

**返回**: 成功消息字符串

#### spawn

**描述**: 生成子代理执行后台任务

**参数**:
```json
{
  "type": "object",
  "properties": {
    "task": {
      "type": "string",
      "description": "任务描述"
    },
    "label": {
      "type": "string",
      "description": "任务标签"
    }
  },
  "required": ["task"]
}
```

**返回**: 启动状态字符串

#### cron

**描述**: 管理定时任务

**参数**:
```json
{
  "type": "object",
  "properties": {
    "action": {
      "type": "string",
      "enum": ["add", "list", "remove"],
      "description": "操作类型"
    },
    "name": {
      "type": "string",
      "description": "任务名称"
    },
    "message":CronJob {
      "type": "string",
      "description": "任务消息"
    },
    "schedule": {
      "type": "string",
      "description": "Cron 表达式或间隔"
    }
  },
  "required": ["action"]
}
```

**返回**: 操作结果字符串

## 4. 事件数据结构

### 4.1 InboundMessage

**描述**: 从聊天平台接收的消息

```python
@dataclass
class InboundMessage:
    channel: str  # telegram, discord, slack, whatsapp
    sender_id: str  # 用户标识符
    chat_id: str  # 聊天/频道标识符
    content: str  # 消息文本
    timestamp: datetime  # 消息时间戳
    media: list[str]  # 媒体 URL 列表
    metadata: dict[str, Any]  # 通道特定元数据
    
    @property
    def session_key(self) -> str:
        """会话唯一键（channel:chat_id）"""
```

### 4.2 OutboundMessage

**描述**: 要发送到聊天平台的消息

```python
@dataclass
class OutboundMessage:
    channel: str  # 目标通道
    chat_id: str  # 目标聊天 ID
    content: str  # 消息内容
    reply_to: str | None  # 回复的消息 ID
    media: list[str]  # 媒体 URL 列列表
    metadata: dict[str, Any]  # 通道特定元数据
```

### 4.3 LLMResponse

**描述**: LLM 提供商的响应

```python
@dataclass
class LLMResponse:
    content: str | None  # 响应内容
    tool_calls: list[ToolCallRequest]  # 工具调用列表
    finish_reason: str  # 完成原因
    usage: dict[str, int]  # Token 使用统计
    reasoning_content: str | None  # 思考输出（Kimi, DeepSeek-R1）
    
    @property
    def has_tool_calls(self) -> bool:
        """是否包含工具调用"""
```

### 4.4 ToolCallRequest

**描述**: LLM 发起的工具调用

```python
@dataclass
class ToolCallRequest:
    id: str  # 工具调用 ID
    name: str  # 工具名称
    arguments: dict[str, Any]  # 工具参数
```

## 5. 配置接口

### 5.1 配置加载

**函数**: `load_config()`

**返回**: `Config` 对象

**行为**:
1. 读取 `~/.vikingbot/config.json`
2. 使用 Pydantic 验证
3. 应用环境变量覆盖
4. 返回配置对象

### 5.2 配置保存

**函数**: `save_config(config: Config)`

**行为**:
1. 验证配置对象
2. 写入 `~/.vikingbot/config.json`
3. 设置适当的文件权限

### 5.3 工作空间路径

**函数**: `get_workspace_path()`

**返回**: `Path` 对象

**行为**:
1. 读取配置中的 `agents.defaults.workspace`
2. 展开用户目录（`~`）
3. 解析为绝对路径

## 6. 错误处理

### 6.1 工具执行错误

**格式**: `"Error: {error_message}"`

**示例**:
```
Error: Tool 'read_file' not found
Error: Invalid parameters for tool 'write_file': missing required filePath
Error: executing exec: Command failed with exit code 1
```

### 6.2 配置错误

**处理**: Pydantic 验证错误

**行为**: 显示详细的验证错误并退出

### 6.3 通道错误

**处理**: 捵获并记录通道错误

**行为**: 记录错误但不中断其他通道

## 7. 扩展接口

### 7.1 添加自定义工具

1. 创建工具类继承 `Tool`
2. 实现所有必需的属性和方法
3. 在 `AgentLoop._register_default_tools()` 中注册

**示例**:
```python
class MyCustomTool(Tool):
    @property
    def name(self) -> str:
        return "my_custom_tool"
    
    @property
    def description(self) -> str:
        return "My custom tool"
    
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "input": {
                    "type": "string",
                    "description": "Input value"
                }
            },
            "required": ["input"]
        }
    
    async def execute(self, **kwargs) -> str:
        return f"Processed: {kwargs['input']}"
```

### 7.2 添加自定义通道

1. 创建通道类继承 `BaseChannel`
2. 实现所有必需的异步方法
3. 在 `ChannelManager._init_channels()` 中添加初始化代码

**示例**:
```python
class MyCustomChannel(BaseChannel):
    name = "mychannel"
    
    async def start(self) -> None:
        self._running = True
        # 连接并监听消息
        
    async def stop(self) -> None:
        self._running = False
        # 清理资源
        
    async def send(self, msg: OutboundMessage) -> None:
        # 发送消息到平台
```

### 7.3 添加自定义提供商

1. 在 `ProviderRegistry.PROVIDERS` 中添加 `ProviderSpec`
2. 在 `ProvidersConfig` 中添加配置字段

**示例**:
```python
# 在 registry.py 中
ProviderSpec(
    name="myprovider",
    keywords=("myprovider",),
    env_key="MYPROVIDER_API_KEY",
    display_name="My Provider",
    litellm_prefix="myprovider",
)

# 在 schema.py 中
class ProvidersConfig(BaseModel):
    # ... 其他提供商
    myprovider: ProviderConfig = ProviderConfig()
```

## 8. 性能考虑

### 8.1 异步 I/O

所有接口方法都是异步的，确保：
- 非阻塞的消息处理
- 并发的工具执行
- 高效的资源利用

### 8.2 错误传播

- 工具执行错误被捕获并返回为字符串
- 通道错误被记录但不中断其他通道
- LLM 调用错误被捕获并返回友好消息

### 8.3 资源清理

- 所有 `stop()` 方法应该清理资源
- 使用上下文管理器确保清理
- 避免资源泄漏

## 9. 安全考虑

### 9.1 输入验证

- 工具参数使用 JSON Schema 验证
- 配置使用 Pydantic 验证
- 路径操作进行安全检查

### 9.2 权限控制

- 通道实现 `is_allowed()` 检查
- 文件工具支持工作空间限制
- Shell 工具支持超时和目录限制

### 9.3 敏感数据

- API 密钥存储在用户主目录
- 配置文件权限设置为 600
- 不在日志中输出敏感信息
