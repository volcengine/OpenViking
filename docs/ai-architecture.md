# OpenViking AI Architecture

> 本文档基于 `new-frontend` 分支最新代码（含 PR #13 `fix(bot): stream LLM responses as SSE content deltas`）自动生成。

---

## 概览

OpenViking 的 AI 能力分布在两层：

| 层 | 模块 | 职责 |
|----|------|------|
| **openviking-server** | `openviking/server/routers/` | HTTP API — 语义搜索、Session 记忆提取、内容重索引、Bot 代理 |
| **vikingbot** | `bot/vikingbot/` | Agent 引擎 — LLM 调用、工具执行、流式推送、多 Channel 接入 |

前端 web-studio 通过生成的 SDK（`@hey-api/openapi-ts`）调用 openviking-server，server 通过 httpx 代理将 `/bot/v1/*` 请求转发到 vikingbot。

---

## 1. Agent Loop — 核心处理引擎

**文件**: `bot/vikingbot/agent/loop.py`

```
用户消息 → MessageBus → AgentLoop._process_message()
    │
    ├─ Session 管理（创建/加载/consolidate）
    ├─ 命令处理（/new /compact /remember /help）
    ├─ ContextBuilder.build_messages()
    │
    └─ _run_agent_loop()  ← 核心循环
        │
        ├─ 发布 ITERATION 事件
        ├─ provider.chat(on_content_delta=..., on_reasoning_delta=...)
        │   ├─ 逐 token 回调 → CONTENT_DELTA / REASONING_DELTA 事件
        │   └─ 返回完整 LLMResponse（含 tool_calls）
        │
        ├─ 有 tool_calls → 并行执行 → TOOL_CALL / TOOL_RESULT 事件 → 下一轮
        └─ 无 tool_calls → final_content → RESPONSE 事件 → 结束
```

### AgentLoop 初始化参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `max_iterations` | 50 | 单条消息最大工具执行轮数 |
| `memory_window` | 50 | Session 消息数上限，超出触发 consolidation |
| `gen_image_model` | `openai/doubao-seedream-4-5-251128` | 图片生成模型 |
| `eval` | False | 评测模式（跳过历史注入） |

### 关键行为

- **工具并行执行**：`asyncio.gather()` 并发执行同一轮所有 tool_calls
- **长耗时监控**：每 40 秒发送 `processing_tick` 元数据事件
- **Memory Consolidation**：Session 过大时克隆→裁剪→后台用 LLM 提炼记忆

---

## 2. 流式架构（PR #13）

### 设计原则

在 `provider.chat()` 上加可选回调 `on_content_delta` / `on_reasoning_delta`，传了就自动开 `stream=True`。**一次 LLM 调用**同时支持 streaming 和 tool_calls，不浪费请求。

### 事件类型

```python
class OutboundEventType(str, Enum):
    RESPONSE        = "response"          # 最终完整回复
    CONTENT_DELTA   = "content_delta"     # 增量内容 chunk
    REASONING_DELTA = "reasoning_delta"   # 增量推理 chunk
    DONE            = "done"              # 流终止标记
    TOOL_CALL       = "tool_call"         # 工具调用
    TOOL_RESULT     = "tool_result"       # 工具结果
    REASONING       = "reasoning"         # 完整推理块
    ITERATION       = "iteration"         # 迭代标记
    NO_REPLY        = "no_reply"          # 无需回复
```

### SSE 事件流时序

简单对话：
```
iteration          {"data": "Iteration 1/50"}
content_delta      {"data": "你"}          ← token 级
content_delta      {"data": "好"}
content_delta      {"data": "！"}
...
response           {"data": "你好！..."}   ← 完整内容
done               {}                      ← 流结束
```

带工具调用：
```
iteration          {"data": "Iteration 1/50"}
content_delta      {"data": "让我搜索..."}   ← LLM 可能先说一句
tool_call          {"data": "openviking_search({...})"}
tool_result        {"data": "{memories: [...]}"}
iteration          {"data": "Iteration 2/50"}
content_delta      {"data": "根据"}
content_delta      {"data": "搜索结果"}
...
response           {"data": "根据搜索结果..."}
done               {}
```

### consume_stream 实现

**文件**: `bot/vikingbot/providers/base.py`

```python
async def consume_stream(
    stream_iter: AsyncIterator[Any],
    on_content_delta: DeltaCallback | None,
    on_reasoning_delta: DeltaCallback | None,
) -> LLMResponse:
```

- 逐 chunk 迭代 OpenAI 格式的流式响应
- `delta.content` → 调用 `on_content_delta` 回调
- `delta.reasoning_content` → 调用 `on_reasoning_delta` 回调
- `delta.tool_calls` → 按 `index` 聚合分片的 `function.name` / `function.arguments`
- 累积 usage，最终返回完整 `LLMResponse`
- 回调异常只 debug log，不中断生成

---

## 3. LLM Provider 体系

### 基类

**文件**: `bot/vikingbot/providers/base.py`

```python
class LLMProvider(ABC):
    async def chat(
        messages, tools=None, model=None,
        max_tokens=4096, temperature=0.7, session_id=None,
        on_content_delta=None,     # 传了就开 streaming
        on_reasoning_delta=None,
    ) -> LLMResponse
```

```python
@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCallRequest]
    finish_reason: str              # "stop" | "tool_calls" | "error"
    usage: dict[str, int]           # prompt_tokens, completion_tokens, total_tokens
    reasoning_content: str | None   # DeepSeek-R1, Kimi 等推理模型
```

### LiteLLMProvider

**文件**: `bot/vikingbot/providers/litellm_provider.py`

通过 LiteLLM 统一路由所有 LLM 提供商。

| 功能 | 实现 |
|------|------|
| 模型解析 | `_resolve_model()` — 自动补 litellm 前缀 |
| 参数覆盖 | `_apply_model_overrides()` — 模型级 temperature 等 |
| System 消息兼容 | `_handle_system_message()` — MiniMax 等不支持 system role 的 provider |
| 环境变量 | `_setup_env()` — 设置 `OPENAI_API_KEY`、`OPENAI_API_BASE` 等 |
| Streaming | 有回调 → `acompletion(stream=True, stream_options={"include_usage": True})` |
| 非 Streaming | 无回调 → `acompletion()` + `_parse_response()` |
| 可观测性 | Langfuse 集成（span 级别追踪） |

### OpenAICompatibleProvider

**文件**: `bot/vikingbot/providers/openai_compatible_provider.py`

直接用 OpenAI SDK 的 `AsyncOpenAI` 客户端，更轻量。网关拒绝 `stream_options` 时自动去掉重试。

### Provider 注册表

**文件**: `bot/vikingbot/providers/registry.py`

| Provider | 关键词 | API Key 前缀 | Gateway |
|----------|--------|-------------|---------|
| OpenRouter | `openrouter` | `sk-or-` | Yes |
| AiHubMix | `aihubmix` | — | Yes |
| Anthropic | `claude` | — | No |
| OpenAI | `gpt` | — | No |
| DeepSeek | `deepseek` | — | No |
| Gemini | `gemini` | — | No |
| Moonshot/Kimi | `kimi` | — | No |
| DashScope/Qwen | `qwen` | — | No |
| Zhipu/GLM | `glm` | — | No |
| MiniMax | `minimax` | — | No |
| VolcEngine | `ark` | — | No |
| vLLM/Local | `vllm` | — | Local |
| Groq | `groq` | — | No |

---

## 4. Context 构建

**文件**: `bot/vikingbot/agent/context.py`

### System Prompt 结构

```
# vikingbot 🐈
VikingBot 角色定义 + 工作空间路径 + 运行时信息

---

## Sandbox Environment        ← 沙箱模式时

---

## SOUL.md / TOOLS.md / ...   ← Bootstrap 文件

---

# Active Skills              ← always=true 的技能全文

---

# Skills                     ← 其余技能摘要（XML 格式）
<skills>
  <skill available="true">
    <name>weather</name>
    <description>...</description>
    <location>/path/to/SKILL.md</location>
  </skill>
</skills>

---

## Current user's information ← 从 OpenViking 读取的用户画像
```

### User Message 上下文

在实际 user message 之前注入：

```
## Current Time: 2026-04-12 17:00 (Saturday) (CST)

---

## Current Session
Channel: cli

---

## openviking_search(query=[user_query])
<memory index="1" type="full">
  <uri>viking://memories/...</uri>
  <score>0.85</score>
  <content>用户之前提到过...</content>
</memory>

---

Reply in the same language as the user's query.
User's query:
```

---

## 5. 工具系统

### 注册机制

**文件**: `bot/vikingbot/agent/tools/factory.py` → `register_default_tools()`

```python
class Tool(ABC):
    name: str                      # 工具名
    description: str               # 给 LLM 的描述
    parameters: dict[str, Any]     # JSON Schema
    async execute(context, **kwargs) -> str
```

### 内置工具清单

| 类别 | 工具名 | 说明 |
|------|--------|------|
| **OpenViking** | `openviking_search` | 语义搜索 |
| | `openviking_multi_read` | 多文档读取（abstract/overview/full） |
| | `openviking_list` | 列出资源 |
| | `openviking_grep` | 正则搜索（支持并发多 pattern） |
| | `openviking_glob` | Glob 匹配 |
| | `openviking_memory_commit` | 写入记忆 |
| | `openviking_add_resource` | 添加资源 |
| **文件** | `read_file` | 读文件 |
| | `write_file` | 写文件 |
| | `edit_file` | 编辑文件 |
| | `list_dir` | 列目录 |
| **Shell** | `exec` | 执行命令（可配置超时） |
| **Web** | `web_search` | 网页搜索（Brave/Exa/Tavily/DDGS） |
| | `web_fetch` | 获取网页内容 |
| **图片** | `generate_image` | 生成图片 |
| **通信** | `message` | 向 Channel 发送消息 |
| **子代理** | `spawn` | 启动后台子代理 |
| **定时** | `cron` | 定时任务 |

### 工具执行流程

```
LLM 返回 tool_calls
    ↓
asyncio.gather() 并行执行所有工具
    ↓
ToolRegistry.execute(name, params, session_key)
    ├─ 参数校验（JSON Schema）
    ├─ 创建 ToolContext（session_key, sandbox, sender_id）
    ├─ Langfuse tracing span
    ├─ tool.execute(context, **params)
    ├─ Hook 执行（tool.post_call）
    └─ 返回 result 字符串
```

---

## 6. 记忆系统

### 双层架构

| 层 | 存储 | 用途 |
|----|------|------|
| 本地 | `workspace/memory/MEMORY.md` | 长期事实（偏好、技术决策） |
| 本地 | `workspace/memory/HISTORY.md` | 时间索引的对话摘要日志 |
| Viking | `viking://memories/` | 语义检索记忆（向量化） |

### Consolidation 流程

当 `session.messages > memory_window` 时触发：

1. 克隆 session → 后台处理
2. 格式化旧消息为文本（含工具调用标记）
3. LLM 提炼为 `{"history_entry": "...", "memory_update": "..."}`
4. 追加到 HISTORY.md，更新 MEMORY.md
5. Hook `message.compact` → 同步到 Viking

### Viking 记忆注入

每次构建 user context 时，用当前消息做语义搜索：

```python
await self.memory.get_viking_memory_context(
    current_message=msg,
    workspace_id=workspace_id,
    sender_id=sender_id,
)
```

- 搜索 user_memory + agent_memory
- 按 score 过滤（阈值 0.35）
- 高分记忆读取全文，低分只保留摘要
- 总量限制 4000 字符

---

## 7. Channel 体系

### 支持的 Channel

| Channel | 类型 | 说明 |
|---------|------|------|
| OpenAPI | HTTP REST + SSE | Web 端 chat，支持流式 |
| BotChannel | HTTP REST + SSE | 多租户 API（per channel_id 隔离） |
| Telegram | Webhook | |
| Feishu/飞书 | Webhook | |
| Discord | WebSocket | |
| WhatsApp | Bridge | |
| DingTalk | Webhook | |
| Slack | WebSocket | |
| QQ | — | |
| Email | IMAP/SMTP | |

### OpenAPI Channel SSE 机制

**文件**: `bot/vikingbot/channels/openapi.py`

```python
class PendingResponse:
    stream_queue: asyncio.Queue[ChatStreamEvent | None]

    async def add_event(event_type, data)  # 推入队列
    def set_final(content)                  # 标记完成
    async def close_stream()                # 放入 None 终止
```

`send()` 方法路由所有 `OutboundEventType`：

| 事件 | SSE event | 行为 |
|------|-----------|------|
| RESPONSE | `response` | 推入队列 + set_final + close |
| CONTENT_DELTA | `content_delta` | 推入队列 |
| REASONING_DELTA | `reasoning_delta` | 推入队列 |
| DONE | `done` | 由 generator 自动追加 |
| TOOL_CALL | `tool_call` | 推入队列 |
| TOOL_RESULT | `tool_result` | 推入队列 |
| ITERATION | `iteration` | 推入队列 |

---

## 8. openviking-server Bot 代理层

**文件**: `openviking/server/routers/bot.py`

openviking-server 通过 httpx 将 `/bot/v1/*` 转发到 vikingbot 进程。

### 代理的端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/bot/v1/health` | GET | 健康检查 |
| `/bot/v1/chat` | POST | 同步聊天（300s 超时） |
| `/bot/v1/chat/stream` | POST | SSE 流式聊天 |

### 未代理的端点（仅 vikingbot 内部可访问）

| 端点 | 说明 |
|------|------|
| `POST /bot/v1/chat/channel` | 多 BotChannel 同步聊天 |
| `POST /bot/v1/chat/channel/stream` | 多 BotChannel 流式聊天 |
| `GET /bot/v1/sessions` | Bot Session 列表 |
| `POST /bot/v1/sessions` | 创建 Bot Session |
| `GET /bot/v1/sessions/{id}` | Bot Session 详情 |
| `DELETE /bot/v1/sessions/{id}` | 删除 Bot Session |

---

## 9. openviking-server AI 端点

### 语义搜索

| 端点 | 说明 |
|------|------|
| `POST /api/v1/search/find` | 语义搜索（无 session 上下文） |
| `POST /api/v1/search/search` | 语义搜索（带 session 上下文增强） |

### Session 记忆提取

| 端点 | 说明 |
|------|------|
| `POST /api/v1/sessions/{id}/commit` | 归档 + 后台 LLM 提取记忆（返回 task_id） |
| `POST /api/v1/sessions/{id}/extract` | 直接 LLM 提取记忆 |

### 内容重索引

| 端点 | 说明 |
|------|------|
| `POST /api/v1/content/reindex` | `regenerate=true` 时调 LLM 重新生成 L0/L1 摘要 |

---

## 10. 配置参考

```jsonc
{
  "vlm": {
    "api_base": "https://api.example.com/v1",
    "api_key": "sk-...",
    "provider": "openai",
    "model": "gpt-5.2"
  },
  "bot": {
    "agents": {
      "model": "openai/gpt-5.2",
      "provider": "openai",
      "api_key": "sk-...",
      "api_base": "https://api.example.com/v1",
      "maxToolIterations": 50,
      "memoryWindow": 50
    },
    "channels": [
      {
        "type": "telegram",
        "enabled": true,
        "token": "BOT_TOKEN",
        "allowFrom": ["user_id"],
        "ovToolsEnable": true
      },
      {
        "type": "bot_api",
        "enabled": true,
        "channelId": "my-bot",
        "apiKey": "secret"
      }
    ],
    "tools": {
      "web": { "search": { "apiKey": "BRAVE_KEY" } },
      "exec": { "timeout": 60 }
    },
    "sandbox": {
      "backend": "direct",
      "mode": "per-session"
    },
    "ovServer": {
      "mode": "remote",
      "serverUrl": "http://127.0.0.1:1933",
      "rootApiKey": "...",
      "accountId": "default",
      "adminUserId": "default"
    },
    "mode": "normal"
  }
}
```

### 模式说明

| BotMode | 行为 |
|---------|------|
| `normal` | 所有用户可用所有命令 |
| `readonly` | 仅 allow_from 用户可操作，其余只读 |
| `debug` | 只记录消息到 session，不处理不回复 |

---

## 11. 子代理系统

**文件**: `bot/vikingbot/agent/subagent.py`

```python
SubagentManager.spawn(task, session_key, label) -> task_id
```

- 最多 15 轮迭代
- 受限工具集（无 message/spawn/cron）
- 完成后通过 InboundMessage 回报主代理
- 主代理收到后正常处理回报内容

---

## 12. 沙箱系统

**文件**: `bot/vikingbot/sandbox/manager.py`

| 模式 | workspace_id | 说明 |
|------|-------------|------|
| `per-session` | `session_key.safe_name()` | 每个对话隔离 |
| `per-channel` | `session_key.channel_key()` | 每个 Channel 一个 |
| `shared` | `"shared"` | 所有对话共享 |

后端支持 Direct / Docker / SRT / OpenSandbox / AioSandbox。

Bootstrap 文件（AGENTS.md, SOUL.md, TOOLS.md, USER.md, IDENTITY.md）在创建沙箱时自动复制。

---

## 13. 完整数据流

```
┌──────────┐    HTTP/WebSocket     ┌─────────────────┐     httpx proxy      ┌──────────────┐
│  前端     │ ──────────────────→  │ openviking-server │ ─────────────────→  │   vikingbot   │
│ web-studio│    /bot/v1/chat/     │  (FastAPI)        │   /bot/v1/chat/     │  (AgentLoop)  │
│           │    stream            │                   │   stream            │               │
└──────────┘                      └─────────────────┘                      └──────┬───────┘
                                                                                  │
                                         ┌────────────────────────────────────────┤
                                         ▼                                        ▼
                                  ┌──────────────┐                    ┌──────────────────┐
                                  │  LLM Provider │                    │  Tool Execution   │
                                  │  (LiteLLM)    │                    │  (asyncio.gather) │
                                  │               │                    │                   │
                                  │  stream=True  │                    │  OpenViking tools  │
                                  │  ──→ deltas   │                    │  File tools        │
                                  │  ──→ tool_calls│                    │  Web tools         │
                                  └──────────────┘                    │  Shell tools       │
                                                                      └──────────────────┘
                                                                                │
                                         ┌──────────────────────────────────────┘
                                         ▼
                                  ┌──────────────┐
                                  │  MessageBus   │
                                  │               │
                                  │  CONTENT_DELTA│──→ SSE stream
                                  │  TOOL_CALL    │──→ SSE stream
                                  │  TOOL_RESULT  │──→ SSE stream
                                  │  RESPONSE     │──→ SSE stream
                                  │  DONE         │──→ SSE stream
                                  └──────────────┘
```
