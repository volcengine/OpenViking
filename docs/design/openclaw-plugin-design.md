# OpenClaw Plugin 架构与流程

## 概述

OpenClaw Plugin 将 OpenViking 注册为 OpenClaw 的 Context Engine，全面接管对话上下文的**组装、写入、压缩**三条主链路，同时提供若干显式工具供 Agent 主动调用。

---

## 整体架构

```
┌──────────────────────────────────────────────────────────┐
│                         OpenClaw                         │
│                                                          │
│   User Message                                           │
│       │                                                  │
│       ├──► assemble()  ──────────────────────────────┐   │
│       │                                              │   │
│       │         LLM Call                             │   │
│       │              │                               │   │
│       └──► afterTurn() ──────────────────────────────┤   │
│                                                      │   │
│   /compact ──► compact()  ───────────────────────────┤   │
│                                                      │   │
└──────────────────────────────────────────────────────┼───┘
                                                       │
                                                       ▼
      ┌────────────────────────────────────────────────────┐
      │                   OpenViking                       │
      │                                                    │
      │  ┌──────────────────────────────────────────────┐  │
      │  │                 OV Session                   │  │
      │  │           addMessage / commit                │  │
      │  └──────────────────┬───────────────────────────┘  │
      │                     │                              │
      │            ┌────────┴────────┐                     │
      │            ▼                 ▼                     │
      │         Archive           Memories                 │
      │        (归档原文)      (长期记忆提取)                │
      │                                                    │
      │  viking://session/{id}                             │
      │  viking://user/memories                            │
      │  viking://agent/memories                           │
      │  viking://resources                                │
      └────────────────────────────────────────────────────┘
```

---

## 三条主链路

### 链路 1：afterTurn — 无损写入

每轮 LLM 返回后执行，将本轮上下文持久化到 OV session，并在积累量达阈值时触发后台记忆提取。

```
afterTurn(messages, prePromptMessageCount)
    │
    ├── autoCapture 未启用 → 返回
    ├── isHeartbeat → 返回
    ├── session 匹配 bypassSessionPatterns → 返回
    │
    ├── extractNewTurnMessages(messages, prePromptMessageCount)
    │       从 prePromptMessageCount 位置切出本轮新增消息
    │       无新消息 → 返回
    │
    ├── 清理 recall 注入块（防止 <relevant-memories> 被二次捕获）
    │
    ├── client.addSessionMessage(ovSessionId, role, parts, agentId)
    │       逐条写入 OV session（user / assistant）
    │
    ├── client.getSession(ovSessionId) → pending_tokens
    │
    ├── pending_tokens < commitTokenThreshold(20k) → 返回
    │
    └── client.commitSession(wait=false) ──► 后台异步执行
            Phase 2: archive + memory extract
            └── pollPhase2ExtractionOutcome
```

---

### 链路 2：assemble — 上下文组装

每次 LLM 调用前执行，根据调用场景走两条不同路径。

```
assemble(sessionId, messages, tokenBudget, runtimeContext)
    │
    ├── session 被 bypass → passthrough（原样返回）
    │
    ├── 判断调用类型
    │   ├── isTransformContextAssemble（无 prompt/availableTools 等字段）
    │   │       → 走「自动召回路径」
    │   └── isMainAssemble（含 prompt/availableTools 等字段）
    │           → 走「完整上下文路径」
    │
    ├──【自动召回路径】
    │   ├── latest message 非 user → passthrough
    │   ├── autoRecall 未启用 → passthrough
    │   ├── 已含 AUTO_RECALL_SOURCE_MARKER → 跳过（防重复注入）
    │   ├── prepareRecallQuery(userMessage) → query（最大 4000 chars）
    │   ├── buildAutoRecallContext
    │   │   ├── 并行检索 viking://user/memories
    │   │   ├── 并行检索 viking://agent/memories
    │   │   └── 过滤：level=2、score≥threshold、总chars≤recallMaxInjectedChars
    │   └── prependRecallToLatestUserMessage → 前插 <relevant-memories> 块
    │
    └──【完整上下文路径】
        ├── client.getSessionContext(ovSessionId, tokenBudget, agentId)
        │       → latest_archive_overview
        │       → pre_archive_abstracts
        │       → messages（活跃消息）
        │
        ├── OV 无数据 OR OV 消息量 < 输入消息量 → passthrough
        │
        ├── allocateContextBudget(tokenBudget)
        │       Archive:   剩余 × 15%，上限 8000 tokens  ← 历史摘要（较旧）
        │       Session:   剩余全部                       ← 活跃消息（较新）
        │       保留区:    max(15%, 20k tokens)           ← 模型输出空间
        │
        ├── buildArchiveMemory → [Session History Summary] 消息
        ├── buildSessionContext → OV messages → AgentMessages（超预算时从头裁剪）
        │
        ├── 消息后处理管道
        │   normalizeAssistantContent
        │       → canonicalizeAgentMessages
        │           → sanitizeToolUseResultPairing
        │               → mergeConsecutiveUsers
        │                   → ensureAlternation
        │
        └── 存在 archive 时注入 systemPromptAddition（Session Context Guide）
```

**Token 预算分层**

```
                                               ↑ 旧（输入）
┌─────────────────────────────────────────────┐  tokenBudget
│  Archive Memory（历史摘要）                  │  15%，max 8k
├─────────────────────────────────────────────┤
│  Session Context（活跃消息）                 │  剩余全部
├─────────────────────────────────────────────┤
│  Reserved（保留区）                          │  15%，min 20k
└─────────────────────────────────────────────┘
                                               ↓ 新（模型输出）
```

---

### 链路 3：compact — 归档提交

由 OpenClaw 触发（用户 /compact 或上下文超限），同步执行完整归档流程。

```
compact(sessionId, tokenBudget, currentTokenCount)
    │
    ├── session 被 bypass → { ok:true, compacted:false }
    │
    ├── 获取 tokensBefore
    │   ├── 有 currentTokenCount → 直接使用
    │   └── 无 → getSessionContext 估算
    │
    ├── client.commitSession(wait=true)  ← 同步等待 Phase 2
    │   ├── status=failed  → { ok:false, compacted:false }
    │   ├── status=timeout → { ok:false, compacted:false }
    │   └── archived=false → { ok:true, compacted:false, reason:"commit_no_archive" }
    │
    ├── client.getSessionContext → latest_archive_overview（summary）
    │
    └── 返回 CompactResult
            ok: true
            compacted: true
            result:
              summary: latest_archive_overview
              firstKeptEntryId: archive_uri 末段
              tokensBefore / tokensAfter
```

---

## Session 身份映射

OpenClaw session 标识 → OV storage id 的转换规则：

```
输入: sessionId, sessionKey
    │
    ├── sessionId 是 UUID 格式
    │       → 小写直接使用
    │
    ├── sessionKey 非空
    │       → sha256(sessionKey)
    │
    ├── sessionId 含 Windows 非法字符（: < > " \ 等）
    │       → sha256("openclaw-session:" + sessionId)
    │
    └── 其他 sessionId
            → 直接使用
```

**Agent ID 解析优先级**

```
sessionKey 格式 "agent:<id>:<rest>"  提取 agentId
    └──► agent_prefix + "_" + agentId  （sanitize 为 [a-zA-Z0-9_-]）

runtimeContext.agentId
    └──► agent_prefix + "_" + agentId

无任何来源
    └──► "main"（或 "<agent_prefix>_main"）
```

---

## 工具注册

| 工具 | 触发场景 |
|------|---------|
| `memory_recall` | 主动检索 user/agent memories（可选 resources） |
| `memory_store` | 用户明确要求记住某内容，立即写入并同步 commit |
| `memory_forget` | 按 URI 删除，或 query 搜索后高置信度自动删除 |
| `add_resource` | 导入文档 / URL / Git 仓库到 viking://resources |
| `add_skill` | 导入 SKILL.md 到 viking://agent/skills |
| `memory_search` | 搜索 resources + agent/skills |
| `ov_archive_search` | 关键词 grep 当前 session 所有归档原始消息 |
| `ov_archive_expand` | 按 archive_id 展开归档原始消息列表 |

`memory_recall` 的搜索范围：

```
并行检索
    ├── viking://user/memories
    ├── viking://agent/memories
    └── viking://resources（需 recallResources=true）

后处理：去重 → 只保留 level=2 叶子节点 → score 过滤 → 按 query 重排
```

---

## 关键配置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `baseUrl` | `http://127.0.0.1:1933` | OV server 地址 |
| `agent_prefix` | `""` | Agent ID 前缀 |
| `autoCapture` | `true` | afterTurn 自动写入开关 |
| `autoRecall` | `true` | assemble 自动召回开关 |
| `commitTokenThreshold` | `20000` | 触发后台 commit 的 pending_tokens 阈值 |
| `commitKeepRecentCount` | `10` | afterTurn commit 后服务端保留的活跃消息数 |
| `recallLimit` | `6` | 自动召回最大记忆条数 |
| `recallScoreThreshold` | `0.15` | 召回分数过滤阈值 |
| `recallMaxInjectedChars` | `4000` | 自动召回注入的最大总字符数 |
| `recallPreferAbstract` | `false` | 优先使用摘要而非读取全文 |
| `bypassSessionPatterns` | `[]` | 完全跳过 OV 的 session key 模式 |

