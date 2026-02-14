# Bus 模块设计

## 概述

Bus 模块提供异步消息队列和事件定义，用于解耦消息生产者和消费者。

## 模块结构

```
vikingbot/bus/
├── __init__.py
├── events.py      # 消事件定义
└── queue.py       # 消息队列
```

## 核心组件

### 1. Message Events (消息事件)

**文件**: `vikingbot/bus/events.py`

**职责**:
- 定义消息数据结构
- 提供会话键生成

**数据类**:

#### InboundMessage

**描述**: 从聊天平台接收的消息

```python
@dataclass
class InboundMessage:
    channel: str  # telegram, discord, slack, whatsapp
    sender_id: str  # 用户标识符
    chat_id: str  # 聊天/频道标识符
    content: str  # 消息文本
    timestamp: datetime = field(default_factory=datetime.now)
    media: list[str] = field(default_factory=list)  # 媒体 URL 列表
    metadata: dict[str, Any] = field(default_factory=dict)  # 通道特定元数据
    
    @property
    def session_key(self) -> str:
        """会话唯一键（channel:chat_id）"""
        return f"{self.channel}:{self.chat_id}"
```

**字段说明**:

| 字段 | 类型 | 说明 |
|------|------|------|
| channel | string | 消息来源通道 |
| sender_id | string | 发送者标识符 |
| chat_id | string | 聊天/频道标识符 |
| content | string | 消息文本内容 |
| timestamp | datetime | 消息时间戳 |
| media | array[string] | 媒体 URL 列表 |
| metadata | object | 通道特定元数据 |

#### OutboundMessage

**描述**: 要发送到聊天平台的消息

```python
@dataclass
class OutboundMessage:
    channel: str  # 目标通道
    chat_id: str  # 目标聊天 ID
    content: str  # 消息内容
    reply_to: str | None = None  # 回复的消息 ID
    media: list[str] = field(default_factory=list)  # 媒体 URL 列表
    metadata: dict[str, Any] = field(default_factory=dict)  # 通道特定元数据
```

**字段说明**:

| 字段 | 类型 | 说明 |
|------|------|------|
| channel | string | 目标通道 |
| chat_id | string | 目标聊天 ID |
| content | string | 段消息内容 |
| reply_to | string/null | 回复的消息 ID |
| media | array[string] | 媒体 URL 列表 |
| metadata | object | 通道特定元数据 |

### 2. MessageBus (消息队列)

**文件**: `vikingbot/bus/queue.py`

**职责**:
- 异步消息队列
- 线程安全的消息传递
- 解耦消息生产者和消费者

**接口**:

```python
class MessageBus:
    """异步消息队列"""
    
    def __init__(self)
    
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

## 消息流

### 入站消息流

```
┌─────────────┐
│  Channels   │  ← 接收来自 Telegram/Discord/WhatsApp 等的消息
└──────┬──────┘
       │ InboundMessage
       ▼
┌─────────────┐
│ MessageBus  │  ← 异步队列
└──────┬──────┘
       │
       ▼
┌─────────────┐
│ AgentLoop   │  ← 消费入站消息
└────────────backs─┘
```

### 出站消息流

```
┌─────────────┐
│ AgentLoop   │  ← 生成响应
└──────┬──────┘
       │ OutboundMessage
       ▼
┌─────────────┐
│ MessageBus  │  ← 异步队列
└──────┬──────┘
       │
       ▼
┌─────────────┐
│ ChannelMgr  │  ← 路由到对应通道
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  Channels    │  ← 发送到到聊天平台
└─────────────┘
```

## 设计模式

### 观察者模式

- AgentLoop 观察消息总线
- ChannelManager 观察消息总线
- 多个消费者可以同时监听

### 生产者-消费者模式

- Channels 是消息生产者
- AgentLoop 是消息消费者
- 通过异步队列解耦

### 发布-订阅模式

- 消息通过总线发布
- 多个消费者可以订阅不同类型的消息

## 使用场景

### 1. 通道消息处理

```python
# 在通道中
async def _handle_message(self, sender_id, chat_id, content):
    msg = InboundMessage(
        channel=self.name,
        sender_id=str(sender_id),
        chat_id=str(chat_id),
        content=content
    )
    await self.bus.publish_inbound(msg)
```

### 2. Agent 消息处理

```python
# 在 AgentLoop 中
while self._running:
    msg = await self.bus.consume_inbound()
    # 处理消息
    response = await self._process_message(msg)
    # 发送响应
    await self.bus.publish_outbound(response)
```

### 3. 系统消息

```python
# 子代理可以通过系统消息返回结果
system_msg = InboundMessage(
    channel="system",
    sender_id="subagent_id",
    chat_id=f"{original_channel}:{original_chat_id}",
    content="Task completed"
)
await self.bus.publish_inbound(system_msg)
```

## 性能优化

### 异步 I/O

- 所有队列操作都是异步的
- 使用 asyncio.Queue 实现高效的消息传递

### 缓冲

- 队列支持缓冲多个消息
- 批量处理减少上下文切换

### 内存效率

- 使用 dataclass 减少内存占用
- 时间戳使用 field_factory 避免重复创建

## 错误处理

### 队列异常

- 队列满时等待
不会阻塞整个系统

### 消息验证

- 类型提示确保消息结构正确
- 可选字段使用默认值

## 扩展点

### 添加新消息类型

1. 在 `events.py` 中定义新的 dataclass
2. 更新相关的生产者/消费者代码

### 自定义队列实现

可以继承 `MessageBus` 实现自定义的队列行为。

## 线程安全

- asyncio.Queue 是线程安全的
- 多个协程可以安全地访问队列
- 无需额外的锁机制
