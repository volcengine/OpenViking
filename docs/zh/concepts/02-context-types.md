# 上下文类型

基于对人类认知模式的简化映射与工程化思考，OpenViking 将上下文抽象为 **资源、记忆、能力三种**基本类型，每种类型在 Agent 中有不同的用途。

## 概览

| 类型 | 用途 | 生命周期 | 主动性 |
|------|------|----------|--------|
| **Resource** | 知识和规则 | 长期，相对静态 | 用户添加 |
| **Memory** | Agent 的认知 | 长期，动态更新 | Agent 记录 |
| **Skill** | 可声明的 agent 能动性配置（AgentDefinedContextType） | 长期，静态 | 用户或系统添加 |

## Resource（资源）

资源是 Agent 可以引用的外部知识。

### 特点

- **用户主动**：由用户主动添加的资源类信息，用于补充大模型的知识，比如产品手册、代码仓库
- **静态内容**：添加后内容很少发生变化，通常为用户主动修改
- **结构化存储**：将按照项目或主题以目录层级组织，并提取出多层信息。

### 示例

- API 文档、产品手册
- FAQ 数据库、代码仓库
- 研究论文、技术规范

### 使用

```python
# 添加资源
client.add_resource(
    "https://docs.example.com/api.pdf",
    reason="API 文档"
)

# 搜索资源
results = client.find(
    "认证方法",
    target_uri="viking://resources/"
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
| **profile** | `user/memories/profile.md` | 用户基本信息 |
| **preferences** | `user/memories/preferences/` | 按主题组织的用户偏好 |
| **entities** | `user/memories/entities/` | 人物、项目、组织等实体知识 |
| **events** | `user/memories/events/` | 决策、里程碑等事件记录 |
| **identity** | `user/memories/identity.md` | 助手的名称、形象、气质和自我介绍 |
| **soul** | `user/memories/soul.md` | 助手的核心原则、边界、风格和连续性 |
| **cases** | `user/memories/cases/` | 用于训练和评估的任务案例 |
| **trajectories** | `user/memories/trajectories/` | 可复用的任务执行轨迹 |
| **experiences** | `user/memories/experiences/` | 从执行结果中提炼的可复用经验 |
| **tools** | `user/memories/tools/` | 工具使用经验与最佳实践 |
| **skills** | `user/memories/skills/` | 技能执行经验与工作流策略 |

表中的 `user/...` 是当前用户短路径，服务端会将其解析为 `viking://user/{user_id}/...`。当记忆策略允许 Peer 记忆时，支持 Peer 的类型会写入 `viking://user/{user_id}/peers/{peer_id}/memories/...`。记忆类型可通过自定义模板扩展或调整。

### 使用

```python
# 记忆从会话中自动提取
session = client.session()
await session.add_message("user", [{"type": "text", "text": "我喜欢深色模式"}])
commit = await session.commit()  # 启动后台记忆提取
task = await client.get_task(commit["task_id"])  # 轮询直到 task["status"] == "completed"

# 搜索记忆
results = await client.find(
    "用户界面偏好",
    target_uri="viking://user/memories/"
)
```

## Skill（技能 / AgentDefinedContextType）

技能（Skill）是 Agent 可以调用的能力，属于 AgentDefinedContextType 范畴。包括传统工作流定义、通信端点、工具配置和支付能力等。它们的共同特征是：**定义了 agent 如何与外部系统交互**，运行时定义相对静态，但调用经验会在 Memory 中更新。

### 特点

- **定义的能力：**用于完成某项工作的工具定义
- **相对静态：**运行时技能定义不变，但和工具相关的使用记忆会在记忆中更新
- **可调用：**Agent 决定何时使用哪种技能

### 存储位置

```
viking://user/skills/{skill-name}/  # 默认存储路径
├── .abstract.md          # L0: 简短描述
├── SKILL.md              # L1: 详细概览
└── scripts               # L2: 完整定义

viking://agent/skills/{skill-name}/  # 通过 --uri 覆盖，公开共享（account 全局）
├── .abstract.md          # L0: 简短描述
├── SKILL.md              # L1: 详细概览
└── scripts               # L2: 完整定义
```

### AgentDefinedContextType 子类型

AgentDefinedContextType 包含以下子类型，均存储于 `viking://agent/` 作用域：

| 子类型 | 位置 | 说明 |
|--------|------|------|
| **Skill** | `agent/skills/` | 传统工作流定义，如搜索、代码生成 |
| **Endpoint** | `agent/endpoints/` | 通信端点配置（a2a, anp 等）（规划中） |
| **Tool** | `agent/tools/` | 工具配置（mcp 等）（规划中） |
| **Payment** | `agent/payments/` | 支付能力配置（ap2 等）（规划中） |

### 使用

```python
# 添加技能（默认写入 viking://user/skills/）
await client.add_skill({
    "name": "search-web",
    "description": "搜索网络获取信息",
    "content": "# search-web\n..."
})

# 通过 -p 指定写入全局 agent 技能根（公开共享）
ov skills add search-web -p viking://agent/skills

# 搜索用户技能
results = await client.find(
    "网络搜索",
    target_uri="viking://user/skills/"
)

# 搜索全局 agent 技能
results = await client.find(
    "网络搜索",
    target_uri="viking://agent/skills/"
)
```

## 统一检索

根据Agent的需求需求，支持对三种上下文类型统一搜索，提供全面信息：

```python
# 跨所有上下文类型搜索
results = await client.find("用户认证")

for ctx in results.memories:
    print(f"记忆: {ctx.uri}")
for ctx in results.resources:
    print(f"资源: {ctx.uri}")
for ctx in results.skills:
    print(f"技能: {ctx.uri}")
```

## 相关文档

- [架构概述](./01-architecture.md) - 系统整体架构
- [上下文层级](./03-context-layers.md) - L0/L1/L2 模型
- [Viking URI](./04-viking-uri.md) - URI 规范
- [会话管理](./08-session.md) - 记忆提取机制
