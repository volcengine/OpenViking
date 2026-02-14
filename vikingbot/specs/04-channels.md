# Channels 模块设计

## 概述

Channels 模块负责与各种聊天平台的集成，提供统一的接口来接收和发送消息。

## 模块结构

```
vikingbot/channels/
├── __init__.py
├── base.py              # 通道抽象基类
├── manager.py          # 通道管理器
├── telegram.py          # Telegram 集成
├── discord.py           # Discord 集成
├── whatsapp.py          # WhatsApp 集成
├── feishu.py            # 飞书集成
├── mochat.py            # MoChat 集成
├── dingtalk.py          # 钉钉集成
├── slack.py             # Slack 集成
├── email.py             # Email 集成
└── qq.py                # QQ 集成
```

## 核心组件

### 1. BaseChannel (通道基类)

**文件**: `vikingbot/channels/base.py`

**职责**:
- 定义聊天通道的抽象接口
- 提供权限检查

**接口**:

```python
class BaseChannel(ABC):
    name: str = "base"
    
    def __init__(self, config: Any, bus: MessageBus)
    
    @abstractmethod
    async def start(self) -> None:
        """启动通道并开始监听消息"""
        pass
    
    @abstractmethod
    async def stop(self) -> None:
        """停止通道并清理资源"""
        pass
    
    @abstractmethod
    async def send(self, msg: OutboundMessage) -> None:
        """通过此通道发送消息"""
        pass
    
    def is_allowed(self, sender_id: str) -> bool:
        """检査发送者是否被允许使用此机器人"""
        pass
    
    async def _handle_message(
        self,
        sender_id: str,
        chat_id: str,
        content: str,
        media: list[str] | None = None,
        metadata: dict[str, Any] | None = None
    ) -> None:
        """处理来自聊天平台的入站消息"""
        pass
    
    @property
    def is_running(self) -> bool:
        """检查通道是否正在运行"""
        pass
```

**权限控制**:
- `allow_from` 白名单
- 空白名单 = 允许所有人
- 非空白名单 = 仅允许列表中的用户

### 2. ChannelManager (通道管理器)

**文件**: `vikingbot/channels/manager.py`

**职责**:
- 管理多个聊天平台
- 协调消息路由

**接口**:

```python
class ChannelManager:
    def __init__(self, config: Config, bus: MessageBus)
    
    async def start_all(self) -> None
    async def stop_all(self) -> None
    def get_channel(self, name: str) -> BaseChannel | None
    def get_status(self) -> dict[str, Any]
    
    @property
    def enabled_channels(self) -> list[str]
```

**工作流程**:
1. 根据配置初始化启用的通道
2. 启动消息总线的出站分发器
3. 启动所有通道
4. 路由出站消息到对应通道

## 支持的通道

### Telegram

**文件**: `vikingbot/channels/telegram.py`

**配置类**: `TelegramConfig`

**特性**:
- Bot API 集成
- 支持 HTTP/SOCKS5 代理
- 支持文本和语音消息（Groq Whisper 转录）
- 简单设置（仅需 bot token）

**配置**:
```python
class TelegramConfig(BaseModel):
    enabled: bool = False
    token: str = ""  # Bot token from @BotFather
    allow_from: list[str] = Field(default_factory=list)
    proxy: str | None = None  # HTTP/SOCKS5 proxy URL
```

### Discord

**文件**: `vikingbot/channels/discord.py`

**配置类**: `DiscordConfig`

**特性**:
- Bot API 集成
- 支持 MESSAGE CONTENT INTENT
- 可选的 SERVER MEMBERS INTENT
- 支持文本和媒体消息

**配置**:
```python
class DiscordConfig(BaseModel):
    enabled: bool = False
    token: str = ""  # Bot token
    allow_from: list[str] = Field(default_factory=list)
    gateway_url: str = "wss://gateway.discord.gg/?v=10&encoding=json"
    intents: int = 37377  # GUILDS + GUILD_MESSAGES + DIRECT_MESSAGES + MESSAGE_CONTENT
```

### WhatsApp

**文件**: `vikingbot/channels/whatsapp.py`

**配置类**: `WhatsAppConfig`

**特性**:
- 通过 Node.js bridge 连接
- 支持 WebSocket 和 HTTP polling
- 需要两个终端运行

**配置**:
```python
class WhatsAppConfig(BaseModel):
    enabled: bool = False
    bridge_url: str = "ws://localhost:3001"
    bridge_token: str = ""  # Shared token for bridge auth
    allow_from: list[str] = Field(default_factory=list)
```

### Feishu (飞书)

**文件**: `vikingbot/channels/feishu.py`

**配置类**: `FeishuConfig`

**特性**:
- WebSocket 长连接（无需公网 IP）
- 支持事件订阅
- 支持消息发送

**配置**:
```python
class FeishuConfig(BaseModel):
    enabled: bool = False
    app_id: str = ""  # App ID from Feishu Open Platform
    app_secret: str = ""  # App Secret
    encrypt_key: str = ""  # Encrypt Key (optional for Long Connection)
    verification_token: str = ""  # Verification Token (optional)
    allow_from: list[str] = Field(default_factory=list)
```

### MoChat

**文件**: `vikingbot/channels/mochat.py`

**配置类**: `MochatConfig`

**特性**:
- Socket.IO WebSocket 集成
- 支持 HTTP polling fallback
- 支持群组和面板
- 支持延迟回复模式

**配置**:
```python
class MochatMentionConfig(BaseModel):
    require_in_groups: bool = False

class MochatGroupRule(BaseModel):
    require_mention: bool = False

class MochatConfig(BaseModel):
    enabled: bool = False
    base_url: str = "https://mochat.io"
    socket_url: str = ""
    socket_path: str = "/socket.io"
    socket_disable_msgpack: bool = False
    socket_reconnect_delay_ms: int = 1000
    socket_max_reconnect_delay_ms: int = 10000
    socket_connect_timeout_ms: int = 10000
    refresh_interval_ms: int = 30000
    watch_timeout_ms: int = 25000
    watch_limit: int = 100
    retry_delay_ms: int = 500
    max_retry_attempts: int = 0
    claw_token: str = ""
    agent_user_id: str = ""
    sessions: list[str] = Field(default_factory=list)
    panels: list[str] = Field(default_factory=list)
    allow_from: list[str] = Field(default_factory=list)
)
    mention: MochatMentionConfig = Field(default_factory=MochatMentionConfig)
    groups: dict[str, MochatGroupRule] = Field(default_factory=dict)
    reply_delay_mode: str = "non-mention"  # off | non-mention
    reply_delay_ms: int = 120000
```

### DingTalk (钉钉)

**文件**: `vikingbot/channels/dingtalk.py`

**配置类**: `DingTalkConfig`

**特性**:
- Stream 模式（无需公网 IP）
- 支持消息发送

**配置**:
```python
class DingTalkConfig(BaseModel):
    enabled: bool = False
    client_id: str = ""  # AppKey
    client_secret: str = ""  # AppSecret
    allow_from: list[str] = Field(default_factory=list)
```

### Slack

**文件**: `vikingbot/channels/slack.py`

**配置类**: `SlackConfig`

**特性**:
- Socket 模式（无需公网 URL）
- 支持 DM 和群组消息
- 支持提及策略

**配置**:
```python
class SlackDMConfig(BaseModel):
    enabled: bool = True
    policy: str = "open"  # "open" or "allowlist"
    allow_from: list[str] = Field(default_factory=list)

class SlackConfig(BaseModel):
    enabled: bool = False
    mode: str = "socket"  # "socket" supported
    webhook_path: str = "/slack/events"
    bot_token: str = ""  # xoxb-...
    app_token: str = ""  # xapp-...
    user_token_read_only: bool = True
    group_policy: str = "mention"  # "mention", "open", "allowlist"
    group_allow_from: list[str] = Field(default_factory=list)
    dm: SlackDMConfig = Field(default_factory=SlackDMConfig)
```

### Email

**文件**: `vikingbot/channels/email.py`

**配置类**: `EmailConfig`

**特性**:
- IMAP 轮询接收
- SMTP 发送
- 支持自动回复控制

**配置**:
```python
class EmailConfig(BaseModel):
    enabled: bool = False
    consent_granted: bool = False  # Explicit owner permission
    
    # IMAP (receive)
    imap_host: str = ""
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = ""
    imap_mailbox: str = "INBOX"
    imap_use_ssl: bool = True
    
    # SMTP (send)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    from_address: str = ""
    
    # Behavior
    auto_reply_enabled: bool = True
    poll_interval_seconds: int = 30
    mark_seen: bool = True
    max_body_chars: int = 12000
    subject_prefix: str = "Re: "
    allow_from: list[str] = Field(default_factory=list)
```

### QQ

**文件**: `vikingbot/channels/qq.py`

**配置类**: `QQConfig`

**特性**:
- botpy SDK WebSocket 集成
- 支持私聊消息
- 无需公网 IP

**配置**:
```python
class QQConfig(BaseModel):
    enabled: bool = False
    app_id: str = ""  # 机器人 ID (AppID) from q.qq.com
    secret: str = ""  # 机器人密钥 (AppSecret) from q.qq.com
    allow_from: list[str] = Field(default_factory=list)
```

## 消息流

### 入站消息流

1. 通道接收消息
2. 检查权限（`is_allowed()`）
3. 创建 `InboundMessage`
4. 发布到消息总线（`bus.publish_inbound()`）

### 出站消息流

1. Agent 生成 `OutboundMessage`
2. 发布到消息总线（`bus.publish_outbound()`）
3. ChannelManager 路由到对应通道
4. 通道调用 `send()` 发送消息

## 设计模式

### 策略模式

不同的聊天平台通过统一的 `BaseChannel` 接口互换。

### 工厂模式

`ChannelManager._init_channels()` 根据配置动态创建通道实例。

### 观察者模式

通道观察消息总线并发布消息。

## 配置

### ChannelsConfig

```python
class ChannelsConfig(BaseModel):
    whatsapp: WhatsAppConfig = Field(default_factory=WhatsAppConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    discord: DiscordConfig = Field(default_factory=DiscordConfig)
    feishu: FeishuConfig = Field(default_factory=FeishuConfig)
    mochat: MochatConfig = Field(default_factory=MochatConfig)
    dingtalk: DingTalkConfig = Field(default_factory=DingTalkConfig)
    email: EmailConfig = Field(default_factory=EmailConfig)
    slack: SlackConfig = Field(default_factory=SlackConfig)
    qq: QQConfig = Field(default_factory=QQConfig)
```

## 扩展点

### 添加新通道

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

## 安全考虑

### 权限控制

- 每个通道支持 `allow_from` 白名单
- 空白名单 = 允许所有人
- 非空白名单 = 仅允许列表中的用户

### API 密钥安全

- Token 存储在配置文件中
- 配置文件权限应设置为 600
- 不在日志中输出敏感信息

### 消息验证

- 通道实现 `is_allowed()` 检查
- 未授权的消息被拒绝并记录

## 性能优化

### 异步 I/O

- 所有通道方法都是异步的
- 支持并发消息处理

### 连接管理

- 自动重连机制（WebSocket）
- 心跳保持连接
- 连接超时处理

### 消息队列

- 使用异步队列缓冲消息
- 批量发送优化
