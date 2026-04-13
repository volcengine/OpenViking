# VikingBot Sessions 前端 — 实现状态与后续计划

## 已完成（P0 — 基础对话）

### 新增文件
| 文件 | 职责 |
|------|------|
| `src/routes/sessions/-lib/convert-message.ts` | Message → ThreadMessageLike 转换 + 流式消息合成 |
| `src/routes/sessions/-hooks/use-thread-list-adapter.ts` | useSessions → ExternalStoreThreadListAdapter 桥接，支持搜索过滤 |
| `src/routes/sessions/-hooks/use-assistant-runtime.ts` | useChat → useExternalStoreRuntime 核心适配器 |
| `src/routes/sessions/-hooks/use-session-titles.ts` | localStorage session 标题管理 + useSyncExternalStore 跨组件同步 |
| `src/routes/sessions/-lib/generate-title.ts` | AI 生成会话标题（非流式 sendChat） |
| `src/routes/sessions/index.tsx` | 双栏布局页面（Session List + Chat Area）+ 快捷键 |
| `src/components/assistant-ui/thread.tsx` | 对话组件（消息流、Markdown、Reasoning、ToolCall） |
| `src/components/assistant-ui/thread-list.tsx` | Session 列表组件（搜索/新建/切换/删除/skeleton） |

### 修改文件
| 文件 | 变更 |
|------|------|
| `src/routes/sessions/route.tsx` | 改为 Outlet 布局路由 |
| `src/routes/sessions/-hooks/use-chat.ts` | 首轮对话后触发 session 标题生成 |
| `src/i18n/locales/en.ts` | 添加 sessions 翻译 |
| `src/i18n/locales/zh-CN.ts` | 同上中文翻译 |
| `package.json` | 新增 `@assistant-ui/react` + `@assistant-ui/react-markdown` |

### 保留不动的数据层
- `src/routes/sessions/-hooks/use-chat.ts` — SSE 流式引擎（仅追加了标题生成逻辑）
- `src/routes/sessions/-hooks/use-sessions.ts` — React Query session CRUD
- `src/routes/sessions/-lib/api.ts` — API 层
- `src/routes/sessions/-lib/sse.ts` — SSE 解析器
- `src/routes/sessions/-types/*.ts` — 类型定义

---

## 已完成（P1 — Agent 透明度）

### 1. 工具调用折叠卡片 ✅
- `AssistantToolCall` 接收 `ToolCallMessagePartProps`
- 显示工具名、运行状态（spinner/check/error 图标）
- 可展开查看输入参数 (JSON) 和输出结果
- 错误时结果区域用 destructive 背景

### 2. 推理过程可展开块 ✅
- `AssistantReasoning` 接收 `ReasoningMessagePartProps`
- 根据 `status.type` 显示 "Thinking..." / "Thought process"
- 运行中自动展开 + spinner 动画

### 3. 迭代标记 ✅
- `AssistantMessage` 通过 `useMessage` selector 读取 `metadata.custom.iteration`
- iteration > 1 时显示 badge

---

## 已完成（P2 — 管理与体验）

### 1. Session 标题系统 ✅
- `use-session-titles.ts` — localStorage 存取 + `useSyncExternalStore` 实时同步
- `generate-title.ts` — 无 session_id 的非流式调用生成标题
- 新建 session 显示 "新会话"，首次发消息立即设为消息前 20 字
- AI 回复后异步生成更好标题覆盖
- 标题显示在 thread list + 聊天区顶部标题栏

### 2. Session 搜索/过滤 ✅
- ThreadList 顶部搜索框，按标题和 session_id 模糊匹配
- Esc 清空搜索并失焦，X 按钮清空
- adapter 层过滤 threads 数组

### 3. 快捷键 ✅
- `Cmd+N` 新建 Session
- `Cmd+K` 聚焦搜索框

### 4. 加载/错误状态 ✅
- ThreadList 加载时显示 skeleton 占位
- 加载失败显示错误提示
- 无 session 时显示引导卡片 + 快捷键提示

### 5. 消息头像 + UI 布局 ✅
- User/Assistant 消息带头像（UserIcon / BotIcon）
- 用户气泡不对称圆角
- Sidebar/Thread 标题栏高度对齐（h-12）
- Active session 高亮（group-data-[active]）

---

## 已知问题

### 1. 消息持久化双请求
`use-chat.ts`：每次对话完成后会发两个 `POST /sessions/{id}/messages` 请求（先 user 后 assistant）。
- **原因**：bot `/chat/stream` 不会自动写入 session
- **如果后端已自动存储**：在 `useAssistantRuntime` 中把 `persistMessages` 改为 `false`
- **如果需要保留**：可考虑后端加批量 addMessages 接口合并为一个请求

### 2. 标题生成触发条件
- `isFirstExchange` 在 `send` 开头快照 `messagesRef.current.length === 0`
- 如果 session 有历史消息（从 API 加载），不会重复生成标题
- AI 标题生成不传 session_id，不污染会话历史

---

## 待实现

### P3 — 移动端响应式
- Session 列表改为抽屉/Sheet
- 输入框适配触控

### P3 — Session 重命名
- **依赖后端** `PATCH /api/v1/sessions/{id}` 端点
- 当前后端不支持，暂不实现

---

## 架构图

```
┌─ SessionsPage (index.tsx) ────────────────────────────────────────┐
│  AssistantRuntimeProvider                                         │
│  ┌──────────────────┬────────────────────────────────────────┐   │
│  │ ThreadList        │ Thread                                 │   │
│  │ (thread-list.tsx) │ (thread.tsx)                           │   │
│  │                   │                                        │   │
│  │ 搜索框            │ 标题栏 (session title)                  │   │
│  │ useSessionList    │ UserMessage / AssistantMessage          │   │
│  │ useSessionTitles  │   ├─ AssistantText (Markdown)          │   │
│  │                   │   ├─ AssistantReasoning (折叠块)        │   │
│  │                   │   └─ AssistantToolCall (折叠卡片)       │   │
│  │                   │                                        │   │
│  │                   │ Composer (输入框 + 发送/停止)            │   │
│  └──────────────────┴────────────────────────────────────────┘   │
│                                                                   │
│  useAssistantRuntime ← useChat + useSessionMessages               │
│    ├─ convertMessage()     (Message → ThreadMessageLike)          │
│    ├─ buildStreamingMessage() (流式状态 → 合成消息)                │
│    ├─ useSessionTitles     (localStorage 标题管理)                │
│    └─ ExternalStoreRuntime (桥接 assistant-ui)                    │
│                                                                   │
│  数据层                                                           │
│    ├─ use-chat.ts          SSE 流式引擎 + 标题触发                │
│    ├─ use-sessions.ts      React Query CRUD                       │
│    ├─ use-session-titles.ts localStorage 标题存取                 │
│    ├─ generate-title.ts    AI 标题生成                             │
│    ├─ api.ts               API 请求                               │
│    ├─ sse.ts               SSE 解析器                             │
│    └─ -types/*.ts          类型定义                                │
└───────────────────────────────────────────────────────────────────┘
```

## 后端 SSE 事件 → 前端映射

| SSE 事件 | useChat 处理 | assistant-ui 展示 |
|----------|-------------|------------------|
| `iteration` | `setIteration(n)` | `metadata.custom.iteration` → 迭代 badge |
| `content_delta` | 累积到 `streamingContent` | `{ type: 'text', text }` → Markdown 流式渲染 |
| `reasoning_delta` | 累积到 `streamingReasoning` | `{ type: 'reasoning', text }` → 可展开思考块 |
| `tool_call` | 追加到 `streamingToolCalls[]` | `{ type: 'tool-call', toolName, args }` → 折叠卡片 |
| `tool_result` | 更新 `lastToolCall.result` | 同上卡片更新结果 |
| `response` | 覆盖 `streamingContent` | 最终文本 |
| `done` | 流结束，构建完整 Message | status → `{ type: 'complete' }` |
