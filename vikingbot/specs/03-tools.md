# Tools 模块设计

## 概述

Tools 模块提供 Agent 可用的能力，包括文件操作、Shell 执行、Web 访问、消息发送、子代理生成和定时任务管理。

## 模块结构

```
vikingbot/agent/tools/
├── __init__.py
├── base.py              # 工具抽象基类
├── registry.py          # 工具注册表
├── spawn.py            # 子代理生成工具
├── filesystem.py        # 文件系统工具
├── shell.py            # Shell 执行工具
├── web.py              # Web 工具
├── message.py          # 消息发送工具
└── cron.py             # 定时任务工具
```

## 核心组件

### 1. Tool (工具基类)

**文件**: `vikingbot/agent/tools/base.py`

**职责**:
- 定义工具的抽象接口
- 提供参数验证

**接口**:

```python
class Tool(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """工具名称（用于函数调用）"""
        pass
    
    @property
    @abstractmethod
    def description(self) -> str:
        """工具描述"""
        pass
    
    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        """JSON Schema 格式的工具参数"""
        pass
    
    @abstractmethod
    async def execute(self, **kwargs: Any) -> str:
        """执行工具"""
        pass
    
    def validate_params(self, params: dict[str, Any]) -> list[str]:
        """验证工具参数"""
        pass
    
    def to_schema(self) -> dict[str, Any]:
        """转换为 OpenAI 函数模式格式"""
        pass
```

**参数验证**支持：
- 类型检查（string, integer, number, boolean, array, object）
- 枚举值验证
- 数值范围验证（minimum, maximum）
- 字符串长度验证（minLength, maxLength）
- 嵌套对象验证
- 必需字段检查

### 2. ToolRegistry (工具注册表)

**文件**: `vikingbot/agent/tools/registry.py`

**职责**:
- 动态注册和执行工具
- 工具查找和验证

**接口**:

```python
class ToolRegistry:
    def __init__(self)
    
    def register(self, tool: Tool) -> None
    def unregister(self, name: str) -> None
    def get(self, name: str) -> Tool | None
    def has(self, name: str) -> bool
    def get_definitions(self) -> list[dict[str, Any]]
    async def execute(self, name: str, params: dict[str, Any]) -> str
    
    @property
    def tool_names(self) -> list[str]
```

## 内置工具

### 1. Filesystem Tools (文件系统工具)

**文件**: `vikingbot/agent/tools/filesystem.py`

#### ReadFileTool

**功能**: 读取文件内容

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

**安全特性**:
- 支持工作空间限制
- 路径规范化
- 错误处理

#### WriteFileTool

**功能**: 写入文件

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

#### EditFileTool

**功能**: 编辑文件（精确字符串替换）

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

#### ListDirTool

**功能**: 列出目录内容

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

### 2. Shell Tool (Shell 执行工具)

**文件**: `vikingbot/agent/tools/shell.py`

#### ExecTool

**功能**: 执行 Shell 命令

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

**安全特性**:
- 超时控制
- 工作目录限制
- 输出大小限制

### 3. Web Tools (Web 工具)

**文件**: `vikingbot/agent/tools/web.py`

#### WebSearchTool

**功能**: Web 搜索（使用 Brave Search）

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

#### WebFetchTool

**功能**: 获取网页内容

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

### 4. Message Tool (消息发送工具)

**文件**: `vikingbot/agent/tools/message.py`

#### MessageTool

**功能**: 发送消息到聊天平台

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

### 5. Spawn Tool (子代理生成工具)

**文件**: `vikingbot/agent/tools/spawn.py`

#### SpawnTool

**功能**: 生成子代理执行后台任务

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

### 6. Cron Tool (定时任务工具)

**文件**: `vikingbot/agent/tools/cron.py`

#### CronTool

**功能**: 管理定时任务

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
    "message": {
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

## 工具注册流程

1. **AgentLoop 初始化**:
   - 创建 `ToolRegistry` 实例
   - 调用 `_register_default_tools()`

2. **工具注册**:
   - 每个工具类被实例化
   - 通过 `registry.register(tool)` 注册

3. **工具执行**:
   - LLM 返回工具调用
   - `registry.execute(name, params)` 被调用
   - 工具的 `execute()` 方法执行
   - 结果返回给 LLM

## 设计模式

### 策略模式

不同的工具通过统一接口执行，LLM 不需要知道具体实现。

### 注册表模式

工具通过名称动态注册和查找，支持运行时扩展。

### 验证模式

所有工具参数在执行前通过 JSON Schema 验证。

## 扩展点

### 添加自定义工具

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

## 安全考虑

### 工作空间限制

- 文件工具支持 `allowed_dir` 参数
- 限制所有文件操作在指定目录内
- 防止路径遍历攻击

### 超时控制

- Shell 工具有超时限制
- 防止长时间运行的命令

### 输入验证

- 所有参数通过 JSON Schema 验证
- 防止无效输入导致错误

## 性能优化

### 异步执行

- 所有工具执行都是异步的
- 支持并发工具调用

### 错误处理

- 工具执行错误被捕获并返回为字符串
- 不中断主流程
- 错误信息对 LLM 可见
