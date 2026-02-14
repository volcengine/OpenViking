# 数据模型和数据库设计

## 概述

Nanobot 使用文件系统作为主要的数据持久化机制，而不是传统的数据库。所有数据以 JSON、JSONL 或 Markdown 格式存储。

## 数据存储位置

### 配置文件

```
~/.vikingbot/
├── config.json          # 主配置文件
├── workspace/           # 工作空间（符号链接或实际目录）
├── sessions/            # 会话存储
│   ├── telegram_123456.jsonl
│   ├── discord_789012.jsonl
│   └── cli_direct.jsonl
└── history/             # CLI 历史记录
    └── cli_history
```

### 工作空间

```
~/.vikingbot/workspace/
├── AGENTS.md           # 代理指令
├── SOUL.md             # 个性定义
├── USER.md             # 用户偏好
├── TOOLS.md            # 工具说明
├── IDENTITY.md          # 身份定义
├── HEARTBEAT.md        # 心跳任务
├── memory/             # 记忆存储
│   ├── MEMORY.md        # 长期记忆
│   └── HISTORY.md        # 历史日志
└── skills/             # 自定义技能
    ├── weather/
    │   └── SKILL.md
    └── github/
        └── SKILL.md
```

### Cron 存储

```
~/.vikingbot/cron.json  # 定时任务存储
```

## 数据模型

### 1. 配置模型

#### Config (主配置)

**文件**: `~/.vikingbot/config.json`

**结构**:

```json
{
  "agents": {
    "defaults": {
      "workspace": "~/.vikingbot/workspace",
      "model": "anthropic/claude-opus-4-5",
      "maxTokens": 8192,
      "temperature": 0.7,
      "maxToolIterations": 20,
      "memoryWindow": 50
    }
  },
  "channels": {
    "telegram": {
      "enabled": false,
      "token": "",
      "allowFrom": [],
      "proxy": null
    },
    "discord": {
      "enabled": false,
      "token": "",
      "allowFrom": [],
      "gatewayUrl": "wss://gateway.discord.gg/?v=10&encoding=json",
      "intents": 37377
    },
    "whatsapp": {
      "enabled": false,
      "bridgeUrl": "ws://localhost:3001",
      "bridgeToken": "",
      "allowFrom": []
    },
    "feishu": {
      "enabled": false,
      "appId": "",
      "appSecret": "",
      "encryptKey": "",
      "verificationToken": "",
      "allowFrom": []
    },
    "mochat": {
      "enabled": false,
      "baseUrl": "https://mochat.io",
      "socketUrl": "",
      "socketPath": "/socket.io",
      "socketDisableMsgpack": false,
      "socketReconnectDelayMs": 1000,
      "socketMaxReconnectDelayMs": 10000,
      "socketConnectTimeoutMs": 10000,
      "refreshIntervalMs": 30000,
      "watchTimeoutMs": 25000,
      "watchLimit": 100,
      "retryDelayMs": 500,
      "maxRetryAttempts": 0,
      "clawToken": "",
      "agentUserId": "",
      "sessions": [],
      "panels": [],
      "allowFrom": [],
      "mention": {
        "requireInGroups": false
      },
      "groups": {},
      "replyDelayMode": "non-mention",
      "replyDelayMs": 120000
    },
    "dingtalk": {
      "enabled": false,
      "clientId": "",
      "clientSecret": "",
      "allowFrom": []
    },
    "email": {
      "enabled": false,
      "consentGranted": false,
      "imapHost": "",
      "imapPort": 993,
      "imapUsername": "",
      "imapPassword": "",
      "imapMailbox": "INBOX",
      "imapUseSsl": true,
      "smtpHost": "",
      "smtpPort": 587,
      "smtpUsername": "",
      "smtpPassword": "",
      "smtpUseTls": true,
      "smtpUseSsl": false,
      "fromAddress": "",
      "autoReplyEnabled": true,
      "pollIntervalSeconds": 30,
      "markSeen": true,
      "maxBodyChars": 12000,
      "subjectPrefix": "Re: ",
      "allowFrom": []
    },
    "slack": {
      "enabled": false,
      "mode": "socket",
      "webhookPath": "/slack/events",
      "botToken": "",
      "appToken": "",
      "userTokenReadOnly": true,
      "groupPolicy": "mention",
      "groupAllowFrom": [],
      "dm": {
        "enabled": true,
        "policy": "open",
        "allowFrom": []
      }
    },
    "qq": {
      "enabled": false,
      "appId": "",
      "secret": "",
      "allowFrom": []
    }
  },
  "providers": {
    "anthropic": {
      "apiKey": "",
      "apiBase": null,
      "extraHeaders": null
    },
    "openai": {
      "apiKey": "",
      "apiBase": null,
      "extraHeaders": null
    },
    "openrouter": {
      "apiKey": "",
      "apiBase": null,
      "extraHeaders": null
    },
    "deepseek": {
      "apiKey": "",
      "apiBase": null,
      "extraHeaders": null
    },
    "groq": {
      "apiKey": "",
      "apiBase": null,
      "extraHeaders": null
    },
    "zhipu": {
      "apiKey": "",
      "apiBase": null,
      "extraHeaders": null
    },
    "dashscope": {
      "apiKey": "",
      "apiBase": null,
      "extraHeaders": null
    },
    "vllm": {
      "apiKey": "",
      "apiBase": null,
      "extraHeaders": null
    },
    "gemini": {
      "apiKey": "",
      "apiBase": null,
      "extraHeaders": null
    },
    "moonshot": {
      "apiKey": "",
      "apiBase": null,
      "extraHeaders": null
    },
    "minimax": {
      "apiKey": "",
      "apiBase": null,
      "extraHeaders": null
    },
    "aihubmix": {
      "apiKey": "",
      "apiBase": null,
      "extraHeaders": null
    }
  },
  "gateway": {
    "host": "0.0.0.0",
    "port": 18790
  },
  "tools": {
    "web": {
      "search": {
        "apiKey": "",
        "maxResults": 5
      }
    },
    "exec": {
      "timeout": 60
    },
    "restrictToWorkspace": false
  }
}
```

### 2. 会话模型

#### Session (会话)

**文件**: `~/.vikingbot/sessions/{channel}_{chat_id}.jsonl`

**格式**: JSONL (每行一个 JSON 对象)

**结构**:

```jsonl
{"_type": "metadata", "created_at": "2026-02-13T12:00:00", "updated_at": "2026-02-13T12:30:00", "metadata": {}}
{"role": "user", "content": "Hello!", "timestamp": "2026-02-13T12:00:00"}
{"role": "assistant", "content": "Hi there!", "timestamp": "2026-02-13T12:00:01"}
{"role": "user", "content": "What's the weather?", "timestamp": "2026-02-13T12:01:00", "tools_used": ["web_search"]}
{"role": "assistant", "content": "It's sunny today.", "timestamp": "2026-02-13T12:01:05", "tools_used": ["web_search"]}
```

**字段说明**:

| 字段 | 类型 | 说明 |
|------|------|------|
| _type | string | 元数据行标识（仅第一行）|
| created_at | ISO 8601 datetime | 会话创建时间 |
| updated_at | ISO 8601 datetime | 会话更新时间 |
| metadata | object | 会话元数据 |
| role | string | 消息角色（user/assistant/system）|
| content | string | 消息内容 |
| timestamp | ISO 8601 datetime | 消息时间戳 |
| tools_used | array[string] | 使用的工具列表（可选）|

### 3. 记忆模型

#### MEMORY.md (长期记忆)

**文件**: `~/.vikingbot/workspace/memory/MEMORY.md`

**格式**: Markdown

**结构**:

```markdown
# Long-term Memory

## User Information
- Name: John Doe
- Email: john@example.com
- Location: San Francisco, CA

## Preferences
- Theme: dark
- Language: English
- Timezone: America/Los_Angeles

## Projects
- vikingbot: Personal AI assistant project
  - Status: Active development
  - Tech stack: Python, asyncio, LiteLLM
  - Repository: https://github.com/HKUDS/vikingbot

## Important Facts
- User prefers concise responses
- User is working on AI agent research
- User has access to OpenRouter API
```

#### HISTORY.md (历史日志)

**文件**: `~/.vikingbot/workspace/memory/HISTORY.md`

**格式**: Markdown（append-only）

**结构**:

```markdown
[2026-02-13 12:00] USER: Started conversation about weather
[2026-02-13 12:01] ASSISTANT: Provided weather information for San Francisco
[2026-02-13 12:05] USER: Asked about project architecture
[2026-02-13 12:10] ASSISTANT: Explained vikingbot's modular design
[2026-02-13 12:15] USER: Requested help with Python debugging
[2026-02-13 12:20] ASSISTANT: Helped debug async function issue [tools: web_search, read_file]
```

### 4. Cron 任务模型

#### Cron Store (定时任务存储)

**文件**: `~/.vikingbot/cron.json`

**格式**: JSON

**结构**:

```json
{
  "version": 1,
  "jobs": [
    {
      "id": "abc12345",
      "name": "daily_reminder",
      "enabled": true,
      "schedule": {
        "kind": "cron",
        "atMs": null,
        "everyMs": null,
        "expr": "0 9 * * *",
        "tz": "America/Los_Angeles"
      },
      "payload": {
        "kind": "agent_turn",
        "message": "Good morning! Check your calendar.",
        "deliver": true,
        "channel": "telegram",
        "to": "123456789"
      },
      "state": {
        "nextRunAtMs": 17394768000000,
        "lastRunAtMs": 1739391600000,
        "lastStatus": "ok",
        "lastError": null
      },
      "createdAtMs": 1739305200000,
      "updatedAtMs": 1739391600000,
      "deleteAfterRun": false
    }
  ]
}
```

**字段说明**:

| 字段 | 类型 | 说明 |
|------|------|------|
| version | integer | 存储格式版本 |
| jobs | array | 任务列表 |
| id | string | 任务唯一 ID |
| name | string | 任务名称 |
| enabled | boolean | 是否启用 |
| schedule.kind | string | 调度类型（at/every/cron）|
| schedule.atMs | integer/null | "at" 调度的执行时间（毫秒）|
| schedule.everyMs | integer/null | "every" 调度的度（毫秒）|
| schedule.expr | string/null | cron 表达式 |
| schedule.tz | string/null | 时区 |
| payload.kind | string | 负载类型 |
| payload.message | string | 要发送的消息 |
| payload.deliver | boolean | 是否投递到聊天通道 |
| payload.channel | string/null | 目标通道 |
| payload.to | string/null | 目标用户 ID |
| state.nextRunAtMs | integer/null | 下次执行时间（毫秒）|
| state.lastRunAtMs | integer/null | 上次执行时间（毫秒）|
| state.lastStatus | string/null | 上次执行状态（ok/error）|
| state.lastError | string/null | 上次错误信息 |
| createdAtMs | integer | 创建时间（毫秒）|
| updatedAtMs | integer | 更新时间（毫秒）|
| deleteAfterRun | boolean | 执行后是否删除 |

### 5. 技能模型

#### SKILL.md (技能定义)

**文件**: `~/.vikingbot/workspace/skills/{skill-name}/SKILL.md`

**格式**: Markdown with YAML frontmatter

**结构**:

```markdown
---
name: weather
description: Get weather information for any location
always: false
requires:
  bins: []
  env: []
metadata: '{"vikingbot": {"always": false}}'
---

# Weather Skill

Get current weather information for any location worldwide.

## Usage

When the user asks about weather, use this skill to:

1. Extract the location from the user's request
2. Call the weather API with the location
3. Present the weather information in a clear, concise format

## Example

User: "What's the weather in Tokyo?"
Assistant: Let me check the weather in Tokyo for you.
[Uses: weather skill]
```

**Frontmatter 字段**:

| 字段 | 类型 | 说明 |
|------|------|------|
| name | string | 技能名称 |
| description | string | 技能描述 |
| always | boolean | 是否始终加载到上下文 |
| requires.bins | array[string] | 需要的 CLI 工具 |
| requires.env | array[string] | 需要的环境变量 |
| metadata | string | 额外的元数据（JSON 字符串）|

### 6. Bootstrap 文件模型

#### AGENTS.md (代理指令)

**文件**: `~/.vikingbot/workspace/AGENTS.md`

**格式**: Markdown

**结构**:

```markdown
# Agent Instructions

You are a helpful AI assistant. Be concise, accurate, and friendly.

## Guidelines

- Always explain what you're doing before taking actions
- Ask for clarification when the request is ambiguous
- Use tools to help accomplish tasks
- Remember important information in your memory files

## Tools Available

You have access to:
- File operations (read, write, edit, list)
- Shell commands (exec)
- Web access (search, fetch)
- Messaging (message)
- Background tasks (spawn)
- Scheduling (cron)

## Memory

- `memory/MEMORY.md` — long-term facts (preferences, context, relationships)
- `memory/HISTORY.md` — append-only event log, search with grep to recall past events
```

#### SOUL.md (个性定义)

**文件**: `~/.vikingbot/workspace/SOUL.md`

**格式**: Markdown

**结构**:

```markdown
# Soul

I am vikingbot 🐈, a personal AI assistant.

## Personality

- Helpful and friendly
- Concise and to the point
- Curious and eager to learn

## Values

- Accuracy over speed
- User privacy and safety
- Transparency in actions

## Communication Style

- Be clear and direct
- Explain reasoning when helpful
- Ask clarifying questions when needed
```

#### USER.md (用户偏好)

**文件**: `~/.vikingbot/workspace/USER.md`

**格式**: Markdown

**结构**:

```markdown
# User Preferences

## Communication

- Preferred response style: concise
- Language: English
- Timezone: America/Los_Angeles

## Work Style

- Prefer code examples
- Like step-by-step explanations
- Want confirmation before major actions

## Interests

- AI and machine learning
- Software development
- Open source projects
```

#### HEARTBEAT.md (心跳任务)

**文件**: `~/.vikingbot/workspace/HEARTBEAT.md`

**格式**: Markdown (任务列表）

**结构**:

```markdown
- [ ] Check calendar and remind of upcoming events
- [ ] Scan inbox for urgent emails
- [ ] Check weather forecast for today
- [ ] Review project TODOs
```

## 数据访问模式

### 读取

1. **配置**: `load_config()` 读取 `~/.vikingbot/config.json`
2. **会话**: `SessionManager.get_or_create()` 读取 JSONL 文件
3. **记忆**: `MemoryStore.read_long_term()` 读取 `MEMORY.md`
4. **Cron**: `CronService._load_store()` 读取 `cron.json`
5. **技能**: `SkillsLoader.load_skill()` 读取 `SKILL.md`

### 写入

1. **配置**: `save_config()` 写入 `~/.vikingbot/config.json`
2. **会话**: `SessionManager.save()` 写入 JSONL 文件
3. **记忆**: `MemoryStore.write_long_term()` 写入 `MEMORY.md`
4. **历史**: `MemoryStore.append_history()` 追加到 `HISTORY.md`
5. **Cron**: `CronService._save_store()` 写入 `cron.json`

### 缓存

1. **会话缓存**: `SessionManager._cache` (内存字典）
2. **Cron 存储**: `CronService._store` (内存对象）

## 数据一致性

### 并发控制

- **文件写入**: 单线程写入（通过 asyncio 任务串行化）
- **会话保存**: 每个会话独立文件，避免锁竞争
- **配置更新**: 原子写入模式

### 错误恢复

- **会话加载**: JSON 解析失败时返回空会话
- **配置加载**: 使用 Pydantic 验证和默认值
- **Cron 加载**: 失败时使用空存储

### 数据迁移

- **版本字段**: `CronStore.version` 用于格式迁移
- **向后兼容**: 新字段使用默认值
- **渐进式迁移**: 读取时升级，写入时使用新格式

## 性能优化

### 文件格式选择

- **JSONL**: 会话使用 JSONL（每行一个 JSON）便于追加和流式读取
- **JSON**: 配置和 Cron 使用 JSON（完整结构）
- **Markdown**: 记忆和 Bootstrap 使用 Markdown（人类可读）

### 缓存策略

- **会话缓存**: 避免重复读取文件
- **技能元数据**: 解析后缓存
- **配置对象**: 单例模式

### 延迟加载

- **技能内容**: 按需加载（使用 `read_file` 工具）
- **历史消息**: 仅加载最近 N 条（`memory_window`）

## 安全考虑

### 敏感数据

- **API 密钥**: 存储在 `~/.vikingbot/config.json`（用户主目录）
- **权限控制**: 文件权限设置为 `rw-------` (600)
- **环境变量**: 支持通过环境变量覆盖配置

### 路径遍历

- **工作空间限制**: `tools.restrictToWorkspace` 限制文件访问
- **安全路径**: 使用 `Path.expanduser()` 和 `Path.resolve()` 规范化路径
- **路径验证**: 工具执行前验证路径在允许范围内

### 数据备份

- **配置备份**: 用户应手动备份 `~/.nan`bot/`
- **会话历史**: 可通过版本控制管理
- **记忆文件**: 可通过 Git 跟踪变更
