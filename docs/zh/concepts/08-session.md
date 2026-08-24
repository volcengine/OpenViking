# 会话管理

Session 负责管理对话消息、记录上下文使用、提取长期记忆。

## 概览

**生命周期**：创建 → 交互 → 提交

通过 session_id 获取会话时不会创建会话。请先创建会话，再通过
`client.session(session_id=...)` 追加消息或提交会话。

```python
session_info = client.create_session(session_id="chat_001")
session = client.session(session_id=session_info["session_id"])
session.add_message(role="user", content="...")
session.commit()
```

## 核心 API

| 方法 | 说明 |
|------|------|
| `add_message(role, content=None, parts=None, options=None, peer_id=None)` | 添加消息 |
| `commit()` | 提交：归档（同步） + 摘要生成和记忆提取（异步后台） |
| `get_task(task_id)` | 查询后台任务状态 |

### add_message

```python
from openviking_sdk import ContextPart, ImagePart, TextPart

session.add_message(
    role="user",
    content="How to configure embedding?",
)

session.add_message(
    role="assistant",
    parts=[
        TextPart(text="Here's how..."),
        ContextPart(
            uri="viking://~/memories/profile.md",
            context_type="memory",
            abstract="User profile",
        ),
    ]
)

session.add_message(
    role="user",
    parts=[
        TextPart(text="Remember this studio layout."),
        ImagePart(url="https://example.com/studio.png", detail="auto"),
    ]
)
```

### commit

```python
result = session.commit()
# {
#   "status": "accepted",
#   "task_id": "uuid-xxx",
#   "archive_uri": "viking://user/{user_id}/sessions/.../history/archive_001",
#   "archived": True
# }

# 查询后台任务进度
task = client.get_task(task_id=result["task_id"])
# task["status"]: "pending" | "running" | "completed" | "failed"
# sum(task["result"]["memories_extracted"].values()): 3
```

## 消息结构

### Message

```python
@dataclass
class Message:
    id: str              # msg_{UUID}
    role: str            # "user" | "assistant"
    parts: List[Part]    # 消息部分
    created_at: datetime
```

### Part 类型

| 类型 | 说明 |
|------|------|
| `TextPart` | 文本内容 |
| `ImagePart` | 图片 URL 内容。记忆提取时，OpenViking 可以使用已配置的 VLM 将其描述为文本。 |
| `ContextPart` | 上下文引用（URI + 摘要） |
| `ToolPart` | 工具调用（输入 + 输出） |

## 压缩策略

### 归档流程

commit() 分两阶段执行：

**Phase 1（同步，立即完成）**：
1. 递增 compression_index
2. 写入消息到归档目录（`messages.jsonl`）
3. 清空当前消息列表
4. 返回 `task_id`

**Phase 2（异步后台）**：
5. 生成结构化摘要（LLM）→ 写入 `.abstract.md` 和 `.overview.md`
6. 提取长期记忆
7. 写入 `memory_diff.json`（记忆变更审计日志）到归档目录
8. 更新 active_count
9. 写入 `.done` 完成标记

### 摘要格式

```markdown
# 会话摘要

**一句话概述**: [主题]: [意图] | [结果] | [状态]

## Analysis
关键步骤列表

## Primary Request and Intent
用户的核心目标

## Key Concepts
关键技术概念

## Pending Tasks
未完成的任务
```

## 记忆提取

### 记忆类型

提交会话后，OpenViking 会根据对话内容和当前记忆策略，提取对后续交互有价值的信息，并保存到当前用户的记忆空间。当对话涉及稳定的 Peer 时，相关记忆也可以保存到对应的 Peer 空间。

OpenViking 内置 `profile`、`preferences`、`entities`、`events`、`identity`、`soul`、`cases`、`trajectories` 和 `experiences` 等记忆类型，也支持根据业务需要自定义。完整用途与路径见 [上下文类型](./02-context-types.md)。

在 `memory_policy.memory_types` 中，`experiences` 会启用完整的 Agent Evolution 流程，并自动激活 `cases` 和 `trajectories`。如果没有 `experiences`，显式传入的 `cases` 和 `trajectories` 会被静默忽略，不会报错。

### 提取流程

```
消息 → LLM 提取 → 候选记忆
         ↓
向量预过滤 → 找相似记忆
         ↓
LLM 去重决策 → candidate(skip/create/none) + item(merge/delete)
         ↓
写入 AGFS → 向量化
```

### 去重决策

| 层级 | 决策 | 说明 |
|------|------|------|
| Candidate | `skip` | 候选记忆重复，直接跳过 |
| Candidate | `create` | 创建候选记忆；必要时先删除冲突旧记忆 |
| Candidate | `none` | 不创建候选记忆，只处理已有记忆 |
| Existing item | `merge` | 将候选内容合并到指定已有记忆 |
| Existing item | `delete` | 删除冲突的已有记忆 |

## 记忆变更记录

每次 `session.commit()` 会在归档目录写入 `memory_diff.json`，记录本次提交的所有记忆变更，便于审计和回溯。

```json
{
  "archive_uri": "viking://user/{user_id}/sessions/{session_id}/history/archive_001",
  "extracted_at": "2026-04-21T10:00:00Z",
  "operations": {
    "adds": [
      {
        "uri": "memory/user/xxx/identity.md",
        "memory_type": "identity",
        "after": "新创建的文件内容"
      }
    ],
    "updates": [
      {
        "uri": "memory/user/xxx/context/project.md",
        "memory_type": "context",
        "before": "修改前的文件内容",
        "after": "修改后的文件内容"
      }
    ],
    "deletes": [
      {
        "uri": "memory/user/xxx/context/old.md",
        "memory_type": "context",
        "deleted_content": "被删除的文件内容"
      }
    ]
  },
  "skipped_operations": [
    {
      "memory_type": "events",
      "page_id": 101,
      "reason_code": "invalid_ranges",
      "reason": "无法解析出有效的事件范围"
    }
  ],
  "summary": {
    "total_adds": 1,
    "total_updates": 1,
    "total_deletes": 1,
    "total_skipped": 1
  }
}
```

| 字段 | 说明 |
|------|------|
| `archive_uri` | 本次提交的归档目录 URI |
| `extracted_at` | 提取时间的 ISO 8601 格式 |
| `operations.adds` | 新增的记忆（无 `before`） |
| `operations.updates` | 修改的记忆（含 `before` 和 `after`） |
| `operations.deletes` | 删除的记忆（含 `deleted_content`） |
| `skipped_operations` | 策略性跳过的操作及稳定原因码；不代表文件变更 |
| `summary` | 各操作类型的计数 |

如果没有实际变更或策略性跳过，也会写入空结构的 `memory_diff.json`（所有计数为零）。

## 存储结构

```
viking://user/{user_id}/sessions/{session_id}/
├── messages.jsonl            # 当前消息
├── .abstract.md              # 当前摘要
├── .overview.md              # 当前概览
├── history/
│   ├── archive_001/
│   │   ├── messages.jsonl    # Phase 1 写入
│   │   ├── .abstract.md      # Phase 2 写入（后台）
│   │   ├── .overview.md      # Phase 2 写入（后台）
│   │   ├── memory_diff.json  # Phase 2 写入（后台，记忆变更审计）
│   │   └── .done             # Phase 2 完成标记
│   └── archive_NNN/
└── tools/
    └── {tool_id}/tool.json

viking://~/memories/
├── profile.md
├── identity.md
├── soul.md
├── preferences/
├── entities/
├── events/
├── cases/
├── trajectories/
└── experiences/
```

`viking://~/sessions/{session_id}` 使用家目录别名，服务端会按认证身份将其展开为
`viking://user/{user_id}/sessions/{session_id}`。无 uid 的写法
`viking://user/sessions/{session_id}` 不再被接受，请求会报错并提示改用 `viking://~/...`。
`viking://session/{session_id}` 仍会作为同一个 session 路径的向后兼容别名被接受，
不是独立的存储根。

## 相关文档

- [架构概述](./01-architecture.md) - 系统整体架构
- [上下文类型](./02-context-types.md) - 三种上下文类型
- [上下文提取](./06-extraction.md) - 提取流程
- [上下文层级](./03-context-layers.md) - L0/L1/L2 模型
