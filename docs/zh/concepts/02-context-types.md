# 上下文类型

OpenViking 管理三类上下文：资源提供参考资料，记忆保存交互中提取的信息，技能描述完成任务的方法。

## 概览

| 类型 | 用途 | 生命周期 | 主动性 |
|------|------|----------|--------|
| **Resource** | 知识和规则 | 保留至更新或删除 | 用户添加 |
| **Memory** | 偏好、事实和任务经验 | 长期，动态更新 | 从会话提取或主动记录 |
| **Skill** | 任务指令和配套资源 | 长期，可更新 | 用户或系统添加 |

## 示例准备

以下示例使用同步 Python SDK，需先启动服务端。`add_resource` 和 `add_skill` 可传 `wait=True`，等处理完成后再返回。会话提交会在记忆提取完成前返回，且没有内置等待参数，因此先用下面的函数查询提交任务，再检索新内容。轮询超时不会取消服务端任务。

```python
import time
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(url="http://localhost:1933", api_key="your-key")


def wait_for_task(task_id):
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        task = client.get_task(task_id)
        if task is None:
            raise RuntimeError(f"Task {task_id} not found")
        if task["status"] == "completed":
            return task
        if task["status"] in {"failed", "cancelled"}:
            raise RuntimeError(task)
        time.sleep(1)
    raise TimeoutError(f"Task {task_id} is still running")
```

## Resource（资源）

资源是 Agent 可以引用的外部知识。

### 特点

- **用户主动**：由用户主动添加的资源类信息，用于补充大模型的知识，比如产品手册、代码仓库
- **显式更新**：内容变化后重新导入；支持 Watch 的远程来源可定时刷新
- **结构化存储**：将按照项目或主题以目录层级组织，并提取出多层信息。

### 示例

- API 文档、产品手册
- FAQ 数据库、代码仓库
- 研究论文、技术规范

### 使用

```python
# 添加资源
client.add_resource(
    path="https://docs.example.com/api.pdf",
    options={"reason": "API 文档"},
    wait=True,
    timeout=300,
)

# 搜索资源
results = client.find(
    query="认证方法",
    target_uri="viking://resources/",
)
```

## Memory（记忆）

记忆是 Agent 从交互和任务执行中学到的持久化知识。记忆存储在当前用户或 Peer 命名空间，不使用独立的 `viking://agent/memories` 目录。

### 特点

- **Agent 主动：**由 Agent 主动提取和记录的记忆信息
- **动态更新：**由 Agent 从交互中持续更新
- **个性化：**针对特定用户和稳定 peer 学习记录

### 内置记忆类型

| 类型 | 默认位置 | 说明 |
|------|----------|------|
| **profile** | `~/memories/profile.md` | 用户基本信息 |
| **preferences** | `~/memories/preferences/` | 按主题组织的用户偏好 |
| **entities** | `~/memories/entities/` | 人物、项目、组织等实体知识 |
| **events** | `~/memories/events/` | 决策、里程碑等事件记录 |
| **identity** | `~/memories/identity.md` | 助手的名称、形象、气质和自我介绍 |
| **soul** | `~/memories/soul.md` | 助手的核心原则、边界、风格和连续性 |
| **cases** | `~/memories/cases/` | 用于训练和评估的任务案例 |
| **trajectories** | `~/memories/trajectories/` | 可复用的任务执行轨迹 |
| **experiences** | `~/memories/experiences/` | 从执行结果中提炼的可复用经验 |

表中的 `~/...` 使用家目录别名 `viking://~`，服务端会按认证身份将其展开为 `viking://user/{user_id}/...`。当记忆策略允许 Peer 记忆时，支持 Peer 的类型会写入 `viking://user/{user_id}/peers/{peer_id}/memories/...`。记忆类型可通过自定义模板扩展或调整。

Schema 定义的 `memories/tools/` 和 `memories/skills/` 类型已禁用。它们与存放在 `viking://user/{user_id}/skills/{skill_name}/SKILL.md` 下的独立 Skill 不同，后者仍然保留并受支持。

### 使用

```python
from openviking_sdk import TextPart

# 记忆从会话中自动提取
session_info = client.create_session()
session = client.session(session_id=session_info["session_id"])
session.add_message(
    role="user",
    parts=[TextPart(text="我喜欢深色模式")],
)
commit = session.commit()  # 启动后台记忆提取
if commit.get("task_id"):
    wait_for_task(commit["task_id"])

# 搜索记忆
results = client.find(
    query="用户界面偏好",
    target_uri="viking://~/memories/"
)
```

## Skill（技能 / AgentDefinedContextType）

技能（Skill）通过 `SKILL.md` 和配套文件描述一类任务的步骤、约束和资源。Agent 读取技能后，使用自己的工具执行任务。调用经验可以单独保存为记忆。

### 特点

- **任务说明：**记录完成某项工作的流程和约束
- **可维护：**技能内容可以更新，执行经验单独存为记忆
- **按需读取：**Agent 按当前任务选择技能

### 存储位置

```
viking://~/skills/{skill-name}/  # 默认存储路径
├── .abstract.md          # L0: 简短描述
├── .overview.md          # L1: 目录概览（生成后）
├── SKILL.md              # L2: 技能定义
└── scripts               # L2: 附加实现

viking://agent/skills/{skill-name}/  # 通过 -p/--parent-auto-create 覆盖，公开共享（account 全局）
├── .abstract.md          # L0: 简短描述
├── .overview.md          # L1: 目录概览（生成后）
├── SKILL.md              # L2: 技能定义
└── scripts               # L2: 附加实现
```

### AgentDefinedContextType 子类型

下表列出共享能力的设计分类。当前支持 Skill，默认安装到用户私有目录，也可显式安装到 `viking://agent/skills/`。其余分类是规划，不表示已提供对应接口：

| 子类型 | 位置 | 说明 |
|--------|------|------|
| **Skill** | `agent/skills/` | 传统工作流定义，如搜索、代码生成 |
| **Endpoint** | `agent/endpoints/` | 通信端点配置（a2a, anp 等）（规划中） |
| **Tool** | `agent/tools/` | 工具配置（mcp 等）（规划中） |
| **Payment** | `agent/payments/` | 支付能力配置（ap2 等）（规划中） |

### 使用

```python
# 添加技能（默认写入 viking://~/skills/）
client.add_skill(
    data={
        "name": "search-web",
        "description": "搜索网络获取信息",
        "content": "# search-web\n...",
    },
    wait=True,
    timeout=300,
)

# 搜索用户技能
results = client.find(
    query="网络搜索",
    target_uri="viking://~/skills/"
)

# 搜索全局 agent 技能
results = client.find(
    query="网络搜索",
    target_uri="viking://agent/skills/",
)
```

通过 CLI 安装到账户共享技能目录（需要该路径的写入权限）：

```bash
ov skills add ./skills/search-web -p viking://agent/skills
```

## 统一检索

一次检索可返回搜索范围内的资源、记忆和技能。默认范围包含当前用户空间和共享资源；如需同时查找共享技能，应显式加入 `viking://agent/skills`：

```python
# 跨所有上下文类型搜索
results = client.find(
    query="用户认证",
    target_uri=["viking://~", "viking://resources", "viking://agent/skills"],
)

for context in results.get("memories", []):
    print(f"记忆: {context['uri']}")
for context in results.get("resources", []):
    print(f"资源: {context['uri']}")
for context in results.get("skills", []):
    print(f"技能: {context['uri']}")
```

操作结束后关闭客户端：

```python
client.close()
```

## 相关文档

- [架构概述](./01-architecture.md) - 系统整体架构
- [上下文层级](./03-context-layers.md) - L0/L1/L2 模型
- [Viking URI](./04-viking-uri.md) - URI 规范
- [会话管理](./08-session.md) - 记忆提取机制
