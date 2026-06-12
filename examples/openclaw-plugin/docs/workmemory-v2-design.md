# OpenViking Working Memory v2 — 设计文档

## 文档目标

本文描述 OpenViking Working Memory v2（以下简称 WM v2）的当前实现：设计原则、数据结构、协议、流程，以及对应代码位置。

---

## 当前能力

### afterTurn（每轮对话后自动归档）

| 能力 | 说明 |
|---|---|
| 自动检测归档时机 | `pending_tokens` 滑动窗口，O(1) 计算 |
| 增量更新 WM | tool_call + JSON schema + 服务端 Guards |
| 归档后保留最近消息 | `keep_recent_count`，保持上下文连贯 |

### compact（主动上下文压缩）

| 能力 | 说明 |
|---|---|
| 全量重写 WM 并归档 | `keep_recent_count=0`，彻底压缩 |

### assemble（构建 LLM 上下文）

| 能力 | 说明 |
|---|---|
| WM overview 作为会话摘要 | 结构化 7 段模板 + 旧格式自动升级 |
| 按 archive_id 展开归档原文 | `ov_archive_expand` 工具 / API 读取单个 completed archive 的原始消息 |
| 按关键词跨 archive 回查 | `ov_archive_search` 工具，服务端 grep 命中消息 + archive 标签 |

### 通用

| 能力 | 说明 |
|---|---|
| 信息保留 Guards | 5 个段级保护函数 |

---

## 一、设计方案

### 1.1 设计原则

**原则 1：archive 本体就是 working memory，用固定的结构化模板承载**

archive 的 `.overview.md` 是**固定 7 段结构化模板**（Session Title / Current State / Task & Goals / Key Facts & Decisions / Files & Context / Errors & Corrections / Open Issues）。每段有明确的职责，LLM 不能随意增删段落。

有结构才能做增量更新——LLM 对每个段独立发 `KEEP` / `UPDATE` / `APPEND` 操作，未变化的段发 `KEEP` 由服务端原样复制（零 token 消耗、零信息丢失），变化的段走校验后合并。

向后兼容性的 3 条保证：

- `assemble()` 消费的仍然是 `latest_archive_overview`，无新增数据通路
- `getSessionContext()` 返回字段不变
- 存储结构（`archive_NNN/.overview.md`）不变

**原则 2：信息保留是系统责任，不是 LLM 责任**

LLM 只负责「判断变了什么」，服务端 guard 函数负责「保证不丢信息」。具体机制见 §1.4。

**原则 3：向后兼容与平滑升级**

- 不配置新字段时行为完全不变，`keep_recent_count=0` 等价于全量归档
- 已有旧格式 overview 的会话：服务端自动检测 overview 是否包含 WM 7 段 header。如果是 legacy 格式，走创建路径全量生成 WM，不走 tool_call 增量更新——下一次 commit 时自动完成格式升级，无需手动迁移

### 1.2 WM 数据结构

WM 是一份 Markdown 文档，固定 7 个 section，顺序不变：

```markdown
# Working Memory

## Session Title
_简短独特的 5-10 词标题，信息密集_

## Current State
_当前工作状态、待完成任务、下一步_

## Task & Goals
_用户目标、关键设计决策、解释性上下文_

## Key Facts & Decisions
_重要结论、技术选择及理由、用户偏好与约束_

## Files & Context
_重要文件 / 函数 / 模块及路径_

## Errors & Corrections
_遇到的错误及修复、用户纠正、失败方案_

## Open Issues
_未解决问题、阻塞项、后续风险_
```

每段上限 ~2000 tokens，总 WM 上限 ~12000 tokens（prompt 指引层面的预算约束）。服务端 guard 在单段 ≥ 25 bullets 或 ≥ 1500 tokens 时触发 consolidation 提醒。

### 1.3 增量更新协议

WM 更新通过 **tool_call（function calling）+ JSON schema** 实现：LLM 调用 `update_working_memory` 工具，以结构化 JSON 提交对 7 个段的逐段操作。JSON schema 强约束保证漏段、多段、格式错误在 schema 层直接拦截。

#### tool schema 定义

```python
WM_SEVEN_SECTIONS = [
    "Session Title", "Current State", "Task & Goals",
    "Key Facts & Decisions", "Files & Context",
    "Errors & Corrections", "Open Issues",
]

WM_UPDATE_TOOL = {
    "type": "function",
    "function": {
        "name": "update_working_memory",
        "parameters": {
            "type": "object",
            "required": ["sections"],
            "additionalProperties": False,
            "properties": {
                "sections": {
                    "type": "object",
                    "required": list(WM_SEVEN_SECTIONS),     # 7 段全部必填
                    "additionalProperties": False,
                    "properties": {name: _WM_SECTION_OP_SCHEMA
                                   for name in WM_SEVEN_SECTIONS},
                }
            },
        },
    },
}
```

每段的操作（`_WM_SECTION_OP_SCHEMA`）用 `oneOf` 约束为三种形状之一：

- `{"op": "KEEP"}` — 原样保留
- `{"op": "UPDATE", "content": "..."}` — 全段替换
- `{"op": "APPEND", "items": ["...", "..."]}` — 追加条目

`op` 字段使用 `"type": "string", "enum": ["KEEP"]` 形式，兼容更多 JSON Schema 版本。`additionalProperties: false` + `required` 把 LLM 输出严格钉在这个 schema 里。

#### 段级合并

服务端 `_merge_wm_sections(old_wm, ops)` 按 `WM_SEVEN_SECTIONS` 常量遍历 7 段：

- **KEEP** → 原样复制旧内容
- **UPDATE** → 用 LLM 提供的 content 替换（先经过该段的 guard 校验）
- **APPEND** → 旧内容 + LLM 提供的 items（渲染为 `- item`）
- 漏段 / 未知 op → 兜底 KEEP

关键实现：`session.py: _merge_wm_sections()` + `_parse_wm_sections()`

### 1.4 服务端 Guards

Guards 是服务端在合并 LLM 提交的操作时按段执行的语义校验函数：**即使 LLM 说 UPDATE，服务端也根据段的特性决定是否接受**。

7 个段的保护策略：

| 段 | 数据特点 | Guard | 规则 |
|---|---|---|---|
| Session Title | **锚定型**：会话身份标识，不应随意变更 | `_wm_enforce_title_stability` | UPDATE 与旧 title meaningful-word overlap < 1 → 回退 KEEP |
| Current State | **易变型**：每轮反映当前状态 | 无 | LLM 可自由 UPDATE |
| Task & Goals | **易变型**：目标随会话推进自然变化 | 无 | LLM 可自由 UPDATE |
| Key Facts & Decisions | **累积型**：重要结论不断积累，丢失代价高 | `_wm_enforce_key_facts_consolidation` | 双阈值验证：bullet count ≥ 旧 15% 且 lexical anchor coverage ≥ 70%。被拒时提取新 items 做 APPEND |
| Files & Context | **引用型**：文件路径一旦提及不应消失 | `_wm_enforce_files_no_regression` | UPDATE 丢失旧路径 → KEEP + APPEND 新路径 |
| Errors & Corrections | **只增型**：错误记录只增不删 | `_wm_enforce_append_only` | UPDATE 降级为 APPEND，去重后只追加新条目 |
| Open Issues | **跟踪型**：未解决项不应被静默丢弃 | `_wm_enforce_open_issues_resolved` | silently drop 的 item → 加 `[restored]` 标签恢复 |

Errors 是纯 append-only（UPDATE 总被降级为 APPEND）；Key Facts 允许「受控合并」——LLM 提交的合并 UPDATE 通过双阈值验证后可被接受。

关键实现：`session.py: _wm_enforce_*()` 5 个函数。单元测试覆盖在 `tests/unit/session/test_wm_v2_guards.py`（共 107 用例覆盖 5 个 guard + growth + 通用 schema）。

### 1.5 滑动窗口与 pending_tokens

`SessionMeta` 维护 `pending_tokens: int` 和 `keep_recent_count: int`，持久化到 `.meta.json`。

- `add_message` 时：新消息进入保留窗口尾部，窗口头部被挤出的消息 token 累加到 `pending_tokens`
- `commit` 时 `pending_tokens` 归零
- `GET /sessions/{id}` 直接读 meta，O(1)

服务端有防御性 clamp：`pending_tokens` 与 `keep_recent_count` 都 `max(0, ...)`。`CommitRequest.keep_recent_count` 在 router 层有 `ge=0, le=10_000` 约束。

关键实现：`session.py: add_message()` + `SessionMeta`、`routers/sessions.py: CommitRequest`

### 1.6 保留最近消息

commit 归档时不全量清空消息，保留最近 N 条维持上下文连贯。

- 参数 `keep_recent_count` 由插件在 commit API body 中传入
- `afterTurn` 路径默认 10，`compact` 路径硬编码 0
- OV 存储模型保证 `tool_use` / `tool_result` 配对完整性（ToolPart 自包含）

关键实现：`session.py: commit_async(keep_recent_count)`、`routers/sessions.py: CommitRequest`、`context-engine.ts`、`client.ts`

---

## 二、流程

### 2.1 afterTurn 流程

插件端不变，commit 在服务端完成：

```
[插件] afterTurn
  ├── extractNewTurnMessages → 提取新消息
  ├── addSessionMessage → 逐条 POST /sessions/{id}/messages
  │     服务端: append msg + 滑动窗口更新 pending_tokens + save meta
  ├── GET /sessions/{id} → 返回 pending_tokens（O(1)）
  └── pending_tokens >= commitTokenThreshold?
        │
        YES → commitSession(wait=false, keepRecentCount=cfg.commitKeepRecentCount)
              │
              [服务端 commit_async]
              │
              ├── Phase 1（同步，不阻塞返回）
              │    ├── split_idx = total - keep_recent_count
              │    ├── 归档 messages[:split_idx] → archive_NNN/
              │    ├── 保留 messages[split_idx:]
              │    └── pending_tokens = 0, 更新 meta
              │
              └── Phase 2（asyncio.create_task 后台执行，包在
                  request_wait_tracker.register_request / wait_for_request /
                  cleanup 包络内，确保所有下游 enqueue 都被等待）
                   ├── 读旧 WM: _get_latest_completed_archive_overview()
                   ├── 有旧 WM?
                   │   YES → ov_wm_v2_update prompt + tool_call
                   │          → guards 检查每段决策
                   │          → _merge_wm_sections 段级合并
                   │   NO  → ov_wm_v2 prompt 全量创建
                   ├── 写入 archive_NNN/.overview.md + .abstract.md + .meta.json
                   ├── 提取 long-term memory（compressor_v2，需 archive_uri 才能写 memory_diff.json）
                   ├── 等待 embedding / semantic 队列排空（wait_for_request）
                   └── 写入 .done（最后写，标志该 archive 全部状态终结）
```

Phase 2 关键细节：

- **格式检测**：读取旧 overview 后，先检查是否包含 WM 7 段 header（`any(f"## {s}" in overview for s in WM_SEVEN_SECTIONS)`）。如果是 legacy 格式，走创建路径而非 tool_call 更新——保证平滑升级
- **Section reminders**：更新路径中，`_build_wm_section_reminders()` 从旧 WM 提取每段当前状态摘要，注入到 update prompt 的 `wm_section_reminders` 变量
- **完整回退链**：tool_call 缺失 → `_fallback_generate_wm_creation` 重跑（传入旧 WM 作为上下文）；JSON parse 失败 → 正则 recovery → 段级 guard 兜底 KEEP；VLM 不可用 → 占位 summary
- **Phase 2 队列等待**：`register_request` + `wait_for_request(timeout=_PHASE2_QUEUE_WAIT_TIMEOUT_SECONDS=1800s)` 是必需的——否则下游 `compressor` / `memory_updater` 通过 `register_*_root` 注册的 embedding / semantic 队列无人 await，会让 `tracker.complete()` 与 `.done` 在向量化 / 语义入库**之前**就触发，导致调用方看到 commit 完成但 memory 不可检索

### 2.2 compact 流程

```
[插件] compact
  └── commitSession(wait=true, keepRecentCount=0)
        ├── Phase 1: 全部消息 → archive, messages.clear()
        ├── Phase 2: 读旧 WM → 创建/更新 → 写入
        └── 返回 → getSessionContext → 回读最新 WM
```

### 2.3 assemble（上下文组装）

instruction / archive / session 三分区：

```
┌──────────── System Prompt ────────────────────┐
│ systemPromptAddition（语义示意，非逐字）：       │
│   1. [Session History Summary] 是压缩摘要      │
│   2. Active messages 是最新未压缩上下文        │
│   3. 二者冲突时优先 active messages            │
│   4. 缺细节时询问用户，不要猜                   │
│ + 原始 system prompt                           │
└────────────────────────────────────────────────┘

┌──── Layer 1: Archive Memory (≤8K tokens) ─────┐
│  [user] [Session History Summary]              │
│  # Working Memory                              │
│  ## Session Title                              │
│  ## Current State                              │
│  ## Task & Goals                               │
│  ## Key Facts & Decisions                      │
│  ## Files & Context                            │
│  ## Errors & Corrections                       │
│  ## Open Issues                                │
└────────────────────────────────────────────────┘

┌──── Layer 2: Session Context ─────────────────┐
│  server 侧合并后的 ctx.messages:               │
│  - 未完成 archive 的 pending messages          │
│  - 当前 live session messages                  │
└────────────────────────────────────────────────┘

┌──── Layer 3: Reserved (≥20K tokens) ──────────┐
│  LLM 回复空间                                  │
└────────────────────────────────────────────────┘
```

实现要点：

- `pre_archive_abstracts` 字段保留在 API（向后兼容），但服务端固定返回空数组，插件侧 `buildArchiveMemory()` 只消费 `latest_archive_overview`
- 若需要具体 archive 原文，模型走两条路径：按 `archive_id` 用 `ov_archive_expand` 展开；或用 `ov_archive_search` 按关键词跨 archive grep（见 §三）

---

## 三、归档对话回查工具

OpenViking 在插件侧暴露两个独立的 archive 回查工具。

### 3.1 `ov_archive_expand`

- **插件工具**：`ov_archive_expand`，参数 `archiveId: string`
- **服务端 API**：`GET /api/v1/sessions/{session_id}/archives/{archive_id}`，server 返回 `{archive_id, abstract, overview, messages}`
- **工具输出给 LLM**：archive header（`## archive_id` + `**Summary**: abstract` + `**Messages**: N`） + 全部原始 messages（faithful 文本格式）。`overview` 不重复输出（已经在主上下文 `[Session History Summary]` 里）
- **工具 description 文本**：`"Retrieve original messages from a compressed session archive. Use when a session summary lacks specific details such as exact commands, file paths, code snippets, or config values. Check [Archive Index] to find the right archive ID."`

### 3.2 `ov_archive_search`

- **插件工具**：`ov_archive_search`，参数 `query: string` + 可选 `archiveId: string`
- **客户端封装**：`client.grepSessionArchives(sessionId, pattern, options)`
- **服务端 API**：`POST /api/v1/search/grep`，body `{uri, pattern, case_insensitive}`。`uri` 默认 `viking://session/{sessionId}/history`（覆盖所有 archive）；指定 `archiveId` 时收窄为 `viking://session/{sessionId}/history/{archiveId}`
- **工具输出给 LLM**：最多 12 条命中消息，每条最多 1500 字符，附 archive 标签（如 `archive_005`）和行号
- **行为约束**：
  - 默认遍历所有 archive（新到旧）
  - 默认永远不返回完整 archive 原文
  - case-insensitive；正则元字符自动转义为字面量匹配
- **工具 description 文本**：`"Keyword-grep across all archived original conversation messages of the current session. Use this whenever the [Session History Summary] does not contain the specific detail the user is asking about. Extract 2-3 concrete entity words from the question (names, places, objects, dates) and search each separately. Only conclude information is unavailable after trying at least 2 different keyword variations."`

---

## 四、关键代码索引

| 主题 | 路径 |
|---|---|
| WM 7 段常量与 schema | `openviking/session/session.py: WM_SEVEN_SECTIONS / _WM_SECTION_OP_SCHEMA / WM_UPDATE_TOOL` |
| 段级合并 | `openviking/session/session.py: _merge_wm_sections() / _parse_wm_sections()` |
| 5 个 Guards | `openviking/session/session.py: _wm_enforce_*()` |
| Phase 2 主循环 | `openviking/session/session.py: _run_memory_extraction()` |
| 滑动窗口 / pending_tokens | `openviking/session/session.py: SessionMeta / add_message()` |
| commit API + keep_recent_count clamp | `openviking/server/routers/sessions.py: CommitRequest` |
| WM v2 prompt 模板 | `prompts/templates/compression/ov_wm_v2.yaml`、`ov_wm_v2_update.yaml` |
| 插件 commit / afterTurn / compact | `examples/openclaw-plugin/context-engine.ts` |
| 插件 ov_archive_search 工具 | `examples/openclaw-plugin/index.ts: ov_archive_search` |
| 插件 ov_archive_expand 工具 | `examples/openclaw-plugin/index.ts: ov_archive_expand` |
| 单元测试 | `tests/unit/session/test_wm_v2_guards.py`、`test_working_memory_growth.py`、`test_working_memory_v2.py`（共 107 用例） |

---

> **创建**：2026-05-02  
