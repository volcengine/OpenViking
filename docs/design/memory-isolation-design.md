# Memory Isolation Design - Extract Loop 记忆隔离机制

## 1. 背景与目标

### 1.1 背景

根据 `account-namespace-shared-session-design.md` 设计方案，需要在 `extract_loop` 模块实现记忆隔离机制：

- **两个开关**：`isolate_user_scope_by_agent` 和 `isolate_agent_scope_by_user` 控制目录结构
- **user_id 从 session 消息提取**：支持群聊场景，从对话中动态提取记忆归属的 user_id
- **多用户归属**：events 类型记忆可归属多个 role_id，每个目录都写入一份

### 1.2 目标

1. 从 session 消息中动态提取参与者列表（user_id + agent_id）
2. 校验 LLM 输出的 role_id 是否在参与者范围内
3. 按 account namespace policy 和开关组合决定记忆存储目录
4. 支持一个记忆归属多个 role_id，分别写入对应目录

---

## 2. 核心设计决策

### 2.1 职责分离

创建独立的 `MemoryIsolationHandler` 类，职责：

| 职责 | 说明 |
|------|------|
| 参与者提取 | 从 session messages 解析所有 participant user_id 和 agent_id |
| 目录计算 | 根据 namespace policy + role_id 计算存储目录 |
| 校验 | 校验 role_id 是否在参与者范围内 |
| 多目录写入 | 支持一个记忆写入多个目录 |

### 2.2 使用方

- **ExtractLoop**：初始化 handler，校验 role_id
- **MemoryUpdater**：使用 handler 计算写入目录，执行多目录写入

### 2.3 不涉及

- 不修改 session 的 CRUD 操作
- 不修改现有的 namespace policy 加载逻辑
- 不涉及向量索引的过滤逻辑

---

## 3. 详细设计

### 3.1 类定义

```python
# openviking/session/memory/memory_isolation_handler.py

from dataclasses import dataclass
from typing import List, Optional, Set
from openviking.server.identity import RequestContext

@dataclass
class Participant:
    """参与者信息"""
    role_id: str           # user 或 agent 的 ID
    role_type: str         # "user" 或 "agent"
    account_id: str

@dataclass
class MemoryTarget:
    """记忆写入目标"""
    uri: str               # 完整的 canonical URI
    owner_user_id: Optional[str]
    owner_agent_id: Optional[str]

class MemoryIsolationHandler:
    """记忆隔离处理器"""

    def __init__(self, ctx: RequestContext):
        self.ctx = ctx
        self._participants: List[Participant] = []
        self._participants_loaded = False

    def load_participants_from_messages(self, messages: List[dict]) -> None:
        """
        从 session messages 中提取参与者列表。

        遍历所有消息，按 role 字段区分：
        - role="user" 时，role_id 为 user_id
        - role="assistant" 时，role_id 为 agent_id
        """
        # 解析逻辑见 3.2
        pass

    def get_participant_user_ids(self) -> List[str]:
        """获取所有参与者的 user_id 列表"""
        pass

    def get_participant_agent_ids(self) -> List[str]:
        """获取所有参与者的 agent_id 列表"""
        pass

    def validate_role_id(self, role_id: str, role_type: str) -> bool:
        """
        校验 role_id 是否在参与者范围内。
        - role_type="user" 时，校验 role_id 在 participant user_ids 中
        - role_type="agent" 时，校验 role_id 在 participant agent_ids 中
        """
        pass

    def calculate_memory_targets(
        self,
        role_id: Optional[str],
        role_type: str,
        memory_type: str,
        is_events: bool = False,
        events_range: Optional[dict] = None,
    ) -> List[MemoryTarget]:
        """
        计算记忆的写入目标目录。

        Args:
            role_id: 记忆归属的 role_id（可为 None，表示使用 ctx 默认值）
            role_type: "user" 或 "agent"
            memory_type: 记忆类型（events, preferences, entities 等）
            is_events: 是否为 events 类型
            events_range: events 的时间范围，用于确定涉及的 role_id

        Returns:
            MemoryTarget 列表，每个目标对应一个写入目录
        """
        pass

    def _get_default_target(self, role_type: str) -> MemoryTarget:
        """获取当前 ctx 默认的写入目标"""
        pass

    def _calculate_target_for_role(
        self,
        role_id: str,
        role_type: str,
        memory_type: str,
    ) -> MemoryTarget:
        """为指定 role_id 计算写入目标"""
        pass
```

### 3.2 参与者提取逻辑

```python
def load_participants_from_messages(self, messages: List[dict]) -> None:
    """从 session messages 提取参与者"""
    seen_users: Set[str] = set()
    seen_agents: Set[str] = set()

    for msg in messages:
        role = msg.get("role")
        role_id = msg.get("role_id") or msg.get("role_id")  # 兼容不同字段名

        if not role_id:
            continue

        if role == "user":
            if role_id not in seen_users:
                seen_users.add(role_id)
                self._participants.append(Participant(
                    role_id=role_id,
                    role_type="user",
                    account_id=self.ctx.account_id,
                ))
        elif role == "assistant":
            if role_id not in seen_agents:
                seen_agents.add(role_id)
                self._participants.append(Participant(
                    role_id=role_id,
                    role_type="agent",
                    account_id=self.ctx.account_id,
                ))

    # 如果没有从消息中提取到参与者，使用 ctx 默认值
    if not self._participants:
        self._participants.append(Participant(
            role_id=self.ctx.user.user_id,
            role_type="user",
            account_id=self.ctx.account_id,
        ))
        self._participants.append(Participant(
            role_id=self.ctx.user.agent_id,
            role_type="agent",
            account_id=self.ctx.account_id,
        ))

    self._participants_loaded = True
```

### 3.3 目录计算逻辑

根据 namespace policy 的两个开关组合，计算存储目录：

```python
def _calculate_target_for_role(
    self,
    role_id: str,
    role_type: str,
    memory_type: str,
) -> MemoryTarget:
    """为指定 role_id 计算写入目标"""
    policy = self.ctx.namespace_policy
    account_id = self.ctx.account_id

    if role_type == "user":
        if policy.isolate_user_scope_by_agent:
            # 需要额外 agent 维度，从 participants 中找 agent
            agent_id = self._get_default_agent_id()
            base_uri = f"viking://user/{role_id}/agent/{agent_id}"
        else:
            base_uri = f"viking://user/{role_id}"

        return MemoryTarget(
            uri=f"{base_uri}/memories/{memory_type}",
            owner_user_id=role_id,
            owner_agent_id=agent_id if policy.isolate_user_scope_by_agent else None,
        )

    else:  # role_type == "agent"
        if policy.isolate_agent_scope_by_user:
            # 需要额外 user 维度，从 participants 中找 user
            user_id = self._get_default_user_id()
            base_uri = f"viking://agent/{role_id}/user/{user_id}"
        else:
            base_uri = f"viking://agent/{role_id}"

        return MemoryTarget(
            uri=f"{base_uri}/memories/{memory_type}",
            owner_agent_id=role_id,
            owner_user_id=user_id if policy.isolate_agent_scope_by_user else None,
        )
```

### 3.4 Events 多归属逻辑

```python
def calculate_memory_targets(
        self,
        role_id: Optional[str],
        role_type: str,
        memory_type: str,
        is_events: bool = False,
        events_range: Optional[dict] = None,
) -> List[MemoryTarget]:
    """计算记忆的写入目标目录"""

    # Case 1: 非 events 类型
    if not is_events:
        if role_id is None:
            # 使用 ctx 默认值
            return [self._get_default_target(role_type)]

        # 校验 role_id
        if not self.validate_role_id(role_id, role_type):
            raise ValueError(
                f"role_id '{role_id}' is not in session participants. "
                f"Valid {role_type} participants: {self._get_valid_role_ids(role_type)}"
            )

        return [self._calculate_target_for_role(role_id, role_type, memory_type)]

    # Case 2: events 类型 - 多归属
    # 从 events_range 涉及的 messages 中提取所有 role_id
    target_role_ids = self._extract_role_ids_from_messages_range(events_range)

    # 如果无法从 range 确定，则使用所有参与者
    if not target_role_ids:
        target_role_ids = self._get_valid_role_ids("user")

    targets = []
    for uid in target_role_ids:
        targets.append(self._calculate_target_for_role(uid, "user", memory_type))

    return targets
```

### 3.5 校验逻辑

```python
def validate_role_id(self, role_id: str, role_type: str) -> bool:
    """校验 role_id 是否在参与者范围内"""
    if role_type == "user":
        return role_id in [p.role_id for p in self._participants if p.role_type == "user"]
    else:
        return role_id in [p.role_id for p in self._participants if p.role_type == "agent"]

def _get_valid_role_ids(self, role_type: str) -> List[str]:
    """获取指定类型的有效 role_id 列表"""
    return [p.role_id for p in self._participants if p.role_type == role_type]
```

---

## 4. 接口变更

### 4.1 ExtractLoop 改动

```python
# openviking/session/memory/extract_loop.py

class ExtractLoop:
    def __init__(self, ..., enable_isolation: bool = True):
        # 新增
        self.enable_isolation = enable_isolation
        self._isolation_handler: Optional[MemoryIsolationHandler] = None

    async def run(self):
        # 在 run() 开始时初始化 handler
        if self.enable_isolation:
            self._isolation_handler = MemoryIsolationHandler(self.ctx)
            # 从 provider 获取 session messages
            messages = self.context_provider.get_session_messages()
            self._isolation_handler.load_participants_from_messages(messages)

        # ... 原有逻辑 ...

    def _validate_operations(self, operations: Any) -> None:
        # 原有校验逻辑
        super()._validate_operations(operations)

        # 新增: 校验 role_id
        if self._isolation_handler:
            self._isolation_handler.validate_operations_role_ids(operations)
```

### 4.2 MemoryUpdater 改动

```python
# openviking/session/memory/memory_updater.py

class MemoryUpdater:
    def __init__(self, ..., isolation_handler: Optional[MemoryIsolationHandler] = None):
        self.isolation_handler = isolation_handler

    async def execute_operations(self, operations):
        # 对于每个 operation，计算目标目录列表
        if self.isolation_handler:
            targets = self.isolation_handler.calculate_memory_targets(...)
            # 遍历 targets 分别写入
            for target in targets:
                await self._write_to_target(operation, target)
        else:
            # 原有逻辑
            await self._write_operations(operations)
```

---

## 5. 数据流

```
Session Messages
      │
      ▼
┌─────────────────┐
│ MemoryIsolation │ ◄── 初始化时注入 ctx
│    Handler      │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  提取 Participants │
│  (user_id list,  │
│   agent_id list) │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ ExtractLoop     │
│  _validate_ops  │ ◄── 校验 LLM 输出的 role_id
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ MemoryUpdater   │
│  execute_ops    │ ◄── 计算多目标，写入多个目录
└─────────────────┘
```

---

## 6. 测试计划

### 6.1 单元测试

| 测试用例 | 说明 |
|---------|------|
| `test_extract_participants_from_messages` | 从消息列表正确提取 user/agent |
| `test_validate_role_id_valid` | 合法 role_id 通过校验 |
| `test_validate_role_id_invalid` | 非法 role_id 抛出异常 |
| `test_calculate_targets_single` | 单 role_id 计算目标目录 |
| `test_calculate_targets_multiple` | 多 role_id 返回多个目标 |
| `test_namespace_policy_false_false` | 两个开关都为 false 的目录 |
| `test_namespace_policy_true_false` | isolate_user_scope_by_agent=true |
| `test_namespace_policy_false_true` | isolate_agent_scope_by_user=true |
| `test_namespace_policy_true_true` | 两个开关都为 true |

### 6.2 集成测试

| 测试用例 | 说明 |
|---------|------|
| `test_extract_loop_with_isolation` | 端到端隔离写入 |
| `test_events_multiple_users` | events 写入多个用户目录 |
| `test_invalid_role_id_rejected` | 无效 role_id 被拒绝 |

---

## 7. 风险与边界

### 7.1 风险

- **消息中无 role_id**：如果 session 消息没有 role_id 字段，回退到 ctx 默认值
- **events range 解析失败**：无法解析时，使用所有参与者
- **目录冲突**：多用户写入时可能存在竞争，需要事务支持（已有）

### 7.2 边界

- 不涉及 session 创建时的 participant 记录
- 不涉及向量索引的过滤逻辑修改
- 不支持跨 account 的记忆共享

---

## 8. 推荐落地顺序

1. 创建 `MemoryIsolationHandler` 类
2. 实现 participants 提取逻辑
3. 实现目录计算逻辑
4. 实现校验逻辑
5. 集成到 ExtractLoop
6. 集成到 MemoryUpdater
7. 编写单元测试

---

## 9. 文件清单

| 文件 | 操作 |
|------|------|
| `openviking/session/memory/memory_isolation_handler.py` | 新建 |
| `openviking/session/memory/extract_loop.py` | 修改 |
| `openviking/session/memory/memory_updater.py` | 修改 |
| `tests/session/memory/test_memory_isolation_handler.py` | 新建 |