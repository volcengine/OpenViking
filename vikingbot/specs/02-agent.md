# Agent 模块设计

## 概述

Agent 模块是 Nanobot 的核心处理引擎，负责协调 LLM 调用、工具执行、会话管理和记忆持久化。

## 模块结构

```
vikingbot/agent/
├── __init__.py
├── loop.py              # 代理循环（核心处理引擎）
├── context.py           # 上下文构建器
├── memory.py            # 记忆存储
├── skills.py            # 技能加载器
├── subagent.py          # 子代理管理器
└── tools/              # 工具模块
    ├── __init__.py
    ├── base.py
    ├── registry.py
    ├── spawn.py
    ├── filesystem.py
    ├── shell.py
    ├── web.py
    ├── message.py
    └── cron.py
```

## 核心组件

### 1. AgentLoop (代理循环)

**文件**: `vikingbot/agent/loop.py`

**职责**:
- 核心处理引擎，协调 LLM 调用和工具执行
- 管理会话和记忆
- 处理系统消息（子代理通知）

**接口**:

```python
class AgentLoop:
    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int = 20,
        memory_window: int = 50,
        brave_api_key: str | None = None,
        exec_config: ExecToolConfig | None = None,
        cron_service: CronService | None = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
    )
    
    async def run(self) -> None
    def stop(self) -> None
    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
    ) -> str
```

**内部方法**:
- `_register_default_tools()`: 注册默认工具集
- `_process_message()`: 处理单个入站消息
- `_process_system_message()`: 处理系统消息
- `_consolidate_memory()`: 合并记忆

**工作流程**:
1. 从消息总线接收入站消息
2. 构建上下文（历史、记忆、技能）
3. 调用 LLM 获取响应
4. 如果有工具调用，执行工具并循环
5. 如果没有工具调用，返回最终响应
6. 保存到会话并触发记忆合并（如果需要）

### 2. ContextBuilder (上下文构建器)

**文件**: `vikingbot/agent/context.py`

**职责**:
- 构建 LLM 提示词
- 加载 Bootstrap 文件
- 整合记忆和技能

**接口**:

```python
class ContextBuilder:
    def __init__(self, workspace: Path)
    
    def build_system_prompt(self, skill_names: list[str] | None = None) -> str
    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        skill_names: list[str] | None = None,
        media: list[str] | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> list[dict[str, Any]]
    def add_tool_result(
        self,
        messages: list[dict[str, Any]],
        tool_call_id: str,
        tool_name: str,
        result: str
    ) -> list[dict[str, Any]]
    def add_assistant_message(
        self,
        messages: list[dict[str, Any]],
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None,
        reasoning_content: str | None = None,
    ) -> list[dict[str, Any]]
```

**提示词构建顺序**:
1. 核心身份（时间、运行时、工作空间）
2. Bootstrap 文件（AGENTS.md, SOUL.md, USER.md, TOOLS.md, IDENTITY.md）
3. 长期记忆（MEMORY.md）
4. 始终加载的技能（always=true）
5. 可用技能摘要（XML 格式）

### 3. MemoryStore (记忆存储)

**文件**: `vikingbot/agent/memory.py`

**职责**:
- 双层记忆系统（长期 + 历史）
- MEMORY.md: 长期事实
- HISTORY.md: 可搜索的历史日志

**接口**:

```python
class MemoryStore:
    def __init__(self, workspace: Path)
    
    def read_long_term(self) -> str
    def write_long_term(self, content: str) -> None
    def append_history(self, entry: str) -> None
    def get_memory_context(self) -> str
```

**记忆合并策略**:
1. 当会话消息超过 `memory_window` 时触发
2. 使用 LLM 总结会话内容
3. 提取长期事实到 MEMORY.md
4. 添加历史条目到 HISTORY.md
5. 保留最近的消息在会话中

### 4. SkillsLoader (技能加载器)

**文件**: `vikingbot/agent/skills.py`

**职责**:
- 加载工作空间和内置技能
- 检查技能依赖
- 构建技能摘要

**接口**:

```python
class SkillsLoader:
    def __init__(self, workspace: Path, builtin_skills_dir: Path | None = None)
    
    def list_skills(self, filter_unavailable: bool = True) -> list[dict[str, str]]
    def load_skill(self, name: str) -> str | None
    def load_skills_for_context(self, skill_names: list[str]) -> str
    def build_skills_summary(self) -> str
    def get_always_skills(self) -> list[str]
    def get_skill_metadata(self, name: str) -> dict | None
```

**技能加载优先级**:
1. 工作空间技能（最高优先级）
2. 内置技能（vikingbot/skills/）

**技能元数据**:
- `name`: 技能名称
- `description`: 技能描述
- `always`: 是否始终加载
- `requires.bins`: 需要的 CLI 工具
- `requires.env`: 需要的环境变量

### 5. SubagentManager (子代理管理器)

**文件**: `vikingbot/agent/subagent.py`

**职责**:
- 管理后台子代理执行
- 子代理共享 LLM 提供商但具有隔离的上下文

**接口**:

```python
class SubagentManager:
    def __init__(
        self,
        provider: LLMProvider,
        workspace: Path,
        bus: MessageBus,
        model: str | None = None,
        brave_api_key: str | None = None,
        exec_config: ExecToolConfig | None = None,
        restrict_to_workspace: bool = False,
    )
    
    async def spawn(
        self,
        task: str,
        label: str | None = None,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
    ) -> str
```

**子代理特性**:
- 独立的工具集（无 message 和 spawn 工具）
- 限制的迭代次数（15 次）
- 专注的系统提示词
- 结果通过系统消息返回给原始会话

## 设计模式

### 依赖注入

AgentLoop 依赖以下组件：
- `MessageBus`: 消息路由
- `LLMProvider`: LLM 调用
- `SessionManager`: 会话管理
- `CronService`: 定时任务
- `ToolRegistry`: 工具执行

### 策略模式

- 不同的 LLM 提供商通过统一接口互换
- 不同的工具通过统一接口执行

### 观察者模式

- AgentLoop 观察消息总线
- 通过回调处理消息

## 配置

### AgentDefaults 配置

```python
class AgentDefaults(BaseModel):
    workspace: str = "~/.vikingbot/workspace"
    model: str = "anthropic/claude-opus-4-5"
    max_tokens: int = 8192
    temperature: float = 0.7
    max_tool_iterations: int = 20
    memory_window: int = 50
```

## 扩展点

### 添加自定义工具

1. 创建工具类继承 `Tool`
2. 实现所有必需的属性和方法
3. 在 `AgentLoop._register_default_tools()` 中注册

### 添加自定义记忆策略

可以继承 `MemoryStore` 实现自定义的持久化策略。

## 错误处理

- 工具执行错误被捕获并返回为字符串
- LLM 调用错误被记录并返回友好消息
- 记忆合并失败不影响主流程

## 性能优化

- 会话缓存避免重复加载
- 记忆合并仅在会话过大时触发
- 异步工具执行提高并发性能
