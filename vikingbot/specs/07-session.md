# Session 模块设计

## 概述

Session 模块负责对话会话的管理，包括会话创建、持久化、历史管理和清理。

## 模块结构

```
vikingbot/session/
├── __init__.py
└── manager.py
```

## 核心组件

### Session (会话)

**文件**: `vikingbot/session/manager.py`

**职责**:
- 存储对话会话
- JSONL 格式持久化
- 提供历史管理

**数据结构**:

```python
@dataclass
class Session:
    key: str  # channel:chat_id
    messages: list[dict[str, Any]]  # 消息列表
    created_at: datetime  # 会话创建时间
    updated_at: datetime  # 会话更新时间
    metadata: dict[str, Any]  # 会话元数据
```

**接口**:

```python
class Session:
    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        """
        添加消息到会话
        
        Args:
            role: 消息角色（user/assistant/system）
            content: 消息内容
            **kwargs: 额外字段（如 tools_used）
        """
        pass
    
    def get_history(self, max_messages: int = = 50) -> list[dict[str, Any]]:
        """
        获取消息历史（LLM 格式）
        
        Args:
            max_messages: 最大返回消息数
            
        Returns:
            消息列表（仅包含 role 和 content）
        """
        pass
    
    def clear(self) -> None:
        """清空所有消息"""
        pass
```

**消息格式**:

```python
{
    "role": "user",  # 消息角色
    "content": "Hello!",  # 消息内容
    "timestamp": "2026-02-13T12:00:00",  # 时间戳
    "tools_used": ["web_search"],  # 使用的工具列表（可选）
    # ... 其他自定义字段
}
```

### SessionManager (会话管理器)

**文件**: `vikingbot/session/manager.py`

**职责**:
- 管理会话生命周期
- 会话持久化和缓存
- 会话列表管理

**接口**:

```python
class SessionManager:
    def __init__(self, workspace: Path):
        """
        初始化会话管理器
        
        Args:
            workspace: 工作空间路径
        """
        pass
    
    def get_or_create(self, key: str) -> Session:
        """
        获取现有会话或创建新会话
        
        Args:
            key: 会话键（通常是 channel:chat_id）
            
        Returns:
            会话对象
        """
        pass
    
    def save(self, session: Session) -> None:
        """
        保存会话到磁盘
        
        Args:
            session: 要保存的会话
        """
        pass
    
    def delete(self, key: str) -> bool:
        """
        删除会话
        
        Args:
            key: 会话键
            
        Returns:
            True 如果已删除，False 如果未找到
        """
        pass
    
    def list_sessions(self) -> list[dict[str, Any]]:
        """
        列出所有会话
        
        Returns:
            会话信息字典列表
        """
        pass
```

## 会话存储格式

### JSONL 格式

会话以 JSONL 格式存储，每行一个（JSON 对象）：

```jsonl
{"_type": "metadata", "created_at": "2026-02-13T12:00:00", "updated_at": "2026-02-13T12:30:00", "metadata": {}}
{"role": "user", "content": "Hello!", "timestamp": "2026-02-13T12:00:00"}
{"role": "assistant", "content": "Hi there!", "timestamp": "2026-02-13T12:00:01"}
{"role": "user", "content": "What's the weather?", "timestamp": "2026-02-13T12:01:00", "tools_used": ["web_search"]}
{"role": "assistant", "content": "It's sunny today.", "timestamp": "2026-02-13T12:01:05", "tools_used": ["web_search"]}
```

**优势**:
- 易于追加（append-only）
- 支持流式读取
- 可以快速读取元数据行

### 文件位置

```
~/.vikingbot/sessions/
├── telegram_123456.jsonl
├── discord_789012.jsonl
├── cli_direct.jsonl
└── ...
```

## 会话键生成

**规则**:
- 格式: `{channel}:{chat_id}`
- 示例: `telegram:123456`, `discord:789012`, `cli:direct`

**用途**:
- 唯一标识每个会话
- 用于文件名（替换 `:` 为 `_`）
- 用于会话查找

## 会话生命周期

### 创建会话

```python
# 在 AgentLoop 中
session = session_manager.get_or_create(msg.session_key)
session.add_message("user", msg.content)
session_manager.save(session)
```

### 保存会话

```python
# 每次添加消息后保存
session_manager.save(session)
```

### 清理会话

```python
# 当用户发送 /new 命令时
session.clear()
session_manager.save(session)
```

### 删除会话

```python
# 当会话不再需要时
session_manager.delete(session_key)
```

## 设计模式

### 缓存模式

- 内存缓存避免重复读取文件
- 使用字典存储活跃会话
- 修改时更新缓存

### 单例模式

- `SessionManager` 实例在 `AgentLoop` 中共享
- 避免重复创建管理器

### 持久化策略

- JSONL 格式便于追加
- 元数据单独存储在第一行
- 消息逐行存储

## 配置

### 会话存储位置

```python
# 在 SessionManager 中
self.sessions_dir = ensure_dir(Path.home() / ".vikingbot" / "sessions")
```

### 文件名安全

```python
# 使用 safe_filename() 处理会话键
safe_key = safe_filename(key.replace(":", "_"))
return self.sessions_dir / f"{safe_key}.jsonl"
```

## 扩展点

### 自定义会话存储

可以继承 `SessionManager` 实现自定义的持久化策略。

### 自定义会话数据结构

可以扩展 `Session` 类添加额外的字段。

### 会话钩子

可以在 `SessionManager` 中添加会话创建、保存、删除的钩子。

## 性能优化

### 缓存策略

- 活跃会话缓存在内存中
- 避免频繁的磁盘 I/O

### 延迟保存

- 可以批量保存消息而不是每次保存
- 使用定时器定期保存

### 文件优化

- JSONL 格式支持高效追加
- 避免读取整个文件

## 安全考虑

### 文件权限

- 会话文件权限设置为 `rw-------` (600)
- 仅用户可读写

### 路径安全

- 使用 `safe_filename()` 处理会话键
- 防止路径遍历攻击

### 数据验证

- 使用 Pydantic 验证消息结构
- 防止无效数据损坏存储

## 错误处理

### 加载失败

- JSON 解析失败时返回空会话
- 记录警告但不中断系统

### 保存失败

- 文件写入失败时记录错误
- 不中断主流程

### 删除失败

- 文件删除失败时返回 False
- 记录错误
