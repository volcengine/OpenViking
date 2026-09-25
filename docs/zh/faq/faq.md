# 常见问题

## 基础概念

### OpenViking 是什么？解决什么问题？

OpenViking 用文件系统组织 Agent 的资源、记忆和技能，支持按路径浏览、语义检索和按需读取。应用可以把会话中提取的偏好与经验保存下来，在后续任务中复用。

例如，用户让 Agent 修改部署方案时，Agent 可以检索项目文档，再读取相关配置；上次会话中已确认的部署约束，如果已提取为记忆，也可以在这次任务中检索和复用。应用需要接入检索与会话提交流程，安装数据库本身不会让 Agent 自动获得这些上下文。首次使用可从[导入一份资料并检索](../getting-started/02-quickstart.md)开始，再按需要接入[Agent 工具](../agent-integrations/01-overview.md)。

### OpenViking 和传统向量数据库有什么本质区别？

向量数据库主要提供向量存储和相似度检索。OpenViking 在向量检索之上，提供上下文目录、分层摘要、资源导入、会话和记忆管理。已有向量数据库的能力因产品而异，选型时应按具体需求比较。

| 需求 | OpenViking 提供的能力 |
| --- | --- |
| 组织上下文 | 用 `viking://` 路径管理资源、记忆和技能 |
| 检索内容 | 结合向量检索、目录遍历，以及可配置的意图分析和 Rerank |
| 控制读取量 | 先读目录摘要或概览，再按需读取详细内容 |
| 复用会话经验 | 提交会话后，按记忆策略提取和更新记忆 |
| 排查检索问题 | 使用日志与 telemetry 查看处理和检索过程 |

如果应用只需要对已有向量做相似度查询，应先评估现有数据库是否够用。需要目录浏览、摘要与详情分层读取，或跨会话记忆管理时，再评估 OpenViking。用自己的资料和代表性问题比较召回内容、读取量、延迟和处理成本；收益取决于数据、模型与配置。

### 什么是 L0/L1/L2 分层模型？为什么需要它？

Agent 可以先读摘要定位内容，再按需读全文，减少不相关内容进入上下文。

| 层级 | 内容 | 默认正文上限 | 用途 |
| --- | --- | --- | --- |
| L0 | 目录摘要 `.abstract.md` | 256 字符 | 检索与快速筛选 |
| L1 | 目录概览 `.overview.md` | 4,000 字符 | 导航与精排 |
| L2 | 原始文件或解析后的内容 | 无统一上限 | 按需读取详情 |

L0/L1 是目录级附属文件，是否可用取决于处理状态和配置。上限按字符计算，可通过 `semantic.abstract_max_chars` 和 `semantic.overview_max_chars` 调整，详见[上下文层级](../concepts/03-context-layers.md)。

### Viking URI 是什么？有什么作用？

Viking URI 是 OpenViking 的统一资源标识符，格式为 `viking://{scope}/{path}`。它让系统能精准定位任何上下文：

```
viking://
├── resources/              # 知识库：文档、代码、网页等
│   └── my_project/
├── user/
│   └── {user_id}/          # 用户私有上下文
│       ├── memories/       # 用户记忆
│       ├── resources/      # 用户私有资源
│       ├── skills/         # 用户私有技能（默认）
│       ├── peers/{peer_id}/
│       │   ├── memories/   # Peer 记忆
│       │   └── resources/  # Peer 资源
│       └── sessions/       # 会话与历史归档
└── agent/                  # 可选的 account 全局能力
    └── skills/             # 共享技能
```

## 安装与配置

### 环境要求是什么？

- **Python 版本**：3.10 或更高
- **编译工具**（如果从源码安装或在不支持的平台上）：Rust/Cargo, GCC 9+ 或 Clang 11+
- **必需依赖**：Embedding 模型（推荐火山引擎 Doubao）
- **可选依赖**：
  - VLM（视觉语言模型）：用于多模态内容处理和语义提取
  - Rerank 模型：用于提升检索精度

### OpenViking 是如何访问 AGFS 文件系统的？

OpenViking 通过 Rust 绑定（`ragfs_python` / `RAGFSBindingClient`）在 Python 进程内直接运行 RAGFS 文件系统逻辑。文件系统调用在进程内完成；使用远程存储后端时仍会访问网络。RAGFS 共享库随预编译 wheel 提供，也可从源码构建。

> [!WARNING]
> OpenViking 已不再支持 AGFS HTTP client 模式。当前 AGFS / RAGFS 文件系统访问仅通过 Rust binding（`RAGFSBindingClient`）在进程内完成。这不影响 OpenViking server 的 HTTP API、`ov` CLI，或 `AsyncHTTPClient` / `SyncHTTPClient` 访问 OpenViking 服务端的能力。

### 遇到 "AGFS binding library not found" 错误怎么办？

这通常表示 RAGFS 共享库缺失或无法加载。先检查 Python 版本和平台是否有对应的预编译 wheel，并尝试重新安装。只有平台缺少 wheel 或需要修改源码时才[从源码构建](https://github.com/volcengine/OpenViking/blob/main/CONTRIBUTING_CN.md)，届时需准备原生编译工具链。

### 如何安装 OpenViking？

```bash
pip install openviking --upgrade --force-reinstall
```

### 如何配置 OpenViking？

在用户主目录下创建 `~/.openviking/ov.conf`，将示例中的模型和凭据替换为实际配置：

```json
{
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "api_key": "your-api-key",
      "model": "doubao-embedding-vision-251215",
      "dimension": 1024,
      "input": "multimodal"
    }
  },
  "vlm": {
    "provider": "volcengine",
    "api_key": "your-api-key",
    "model": "doubao-seed-2-0-lite-260428",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3"
  },
  "rerank": {
    "provider": "volcengine",
    "api_key": "your-api-key",
    "model": "doubao-rerank-250615"
  },
  "storage": {
    "workspace": "./data",
    "agfs": { "backend": "local" },
    "vectordb": { "backend": "local" }
  }
}
```

配置文件放在默认路径 `~/.openviking/ov.conf` 时自动加载；也可通过环境变量 `OPENVIKING_CONFIG_FILE` 或命令行 `--config` 指定其他路径。详见 [配置指南](../guides/01-configuration.md)。

### 支持哪些 Embedding Provider？

| Provider | 说明 |
|------|------|
| `volcengine` | 火山引擎 Embedding API（推荐） |
| `openai` | OpenAI Embedding API |
| `vikingdb` | VikingDB Embedding API |
| `jina` | Jina AI Embedding API |
| `ollama` | Ollama（本地 OpenAI 兼容服务器，无需 API Key） |

支持 Dense、Sparse 和 Hybrid 三种 Embedding 模式。

## 使用指南

### 如何初始化客户端？

```python
from openviking_sdk import AsyncHTTPClient

client = AsyncHTTPClient(url="http://localhost:1933", api_key="your-key")
await client.initialize()
```

Embedding、VLM、存储等服务配置由 OpenViking Server 通过 `ov.conf` 管理。

### 支持哪些文件格式？

| 类型 | 支持格式 |
|------|----------|
| **文本** | `.txt`、`.md`、`.json`、`.yaml` |
| **代码** | `.py`、`.js`、`.ts`、`.go`、`.java`、`.cpp` 等 |
| **文档** | `.pdf`、`.docx` |
| **图片** | `.png`、`.jpg`、`.jpeg`、`.gif`、`.webp` |
| **视频** | `.mp4`、`.mov`、`.avi` |
| **音频** | `.mp3`、`.wav`、`.m4a` |

### 如何添加资源？

```python
# 添加单个文件
await client.add_resource(
    path="./document.pdf",
    parent="viking://resources",  # 存到这个目录下面，文件名由来源决定
    options={"reason": "项目技术文档"},  # 未传 instruction 时用于生成 L0/L1 摘要，也用于资源相关的记忆提取
)

# 添加网页
await client.add_resource(
    path="https://example.com/api-docs",
    options={"reason": "API 参考文档"},
)

# 等待处理完成
await client.wait_processed()
```

### `to` 和 `parent` 有什么区别？该用哪个？

|  | `to` | `parent` |
|---|---|---|
| 传什么 | 完整最终 URI，**含叶子名** | 一个**已存在的目录**，叶子名由来源决定 |
| 撞名怎么办 | 不改名。目标已存在时按新来源同步，来源里没有的可见条目会被删除 | 不覆盖。退到 `name_1`、`name_2`……并返回一条 warning |
| 什么时候用 | 名字已知且必须逐字生效；或者要原地更新一个已有资源 | 叶子名由服务端派生（URL / 仓库导入、大文件切分），或者目标下已有的内容一点都不能动 |

两个都留空 = 目录和叶子名都从来源推导，撞名行为同 `parent`。

`to` 和 `parent` 不能同时传，会直接报错。

### `to` 指到一个已存在的目录会发生什么？

内容被同步成新来源的样子，metadata 保留。具体是：

- **点号开头的条目原样保留** —— `.abstract.md`、`.overview.md`、`.search_tags.json`、`.image_mappings.json` 等；同步时两侧都不枚举它们，所以既不会被删也不会被覆盖。
- **其余可见内容和新来源对齐** —— 来源里没有的删掉，变了的覆盖，没变的留在原地（URI 不变，挂在上面的向量和 tags 都还在）。

所以这是「保留 metadata、替换内容本身」，不是把目录删掉重建。不想动目标里已有的东西就用 `parent`。

注意：`processing_mode="vectors_only"` 不跑语义处理，保留下来的 `.abstract.md` / `.overview.md` **不会重算**，会继续描述已经被替换掉的旧内容。需要摘要跟着更新，就用默认的 `semantic_and_vectors`。

### `find()` 和 `search()` 有什么区别？应该用哪个？

| 特性 | `find()` | `search()` |
|------|----------|------------|
| **会话上下文** | 不使用 | 可选，传入 `session_id` 时使用 |
| **意图分析** | 不使用 | 有会话内容且启用意图分析时使用 LLM |
| **延迟** | 低 | 较高 |
| **适用场景** | 简单语义搜索 | 复杂任务、需要理解上下文 |

```python
# find(): 简单直接的语义搜索
results = await client.find(
    query="OAuth 认证流程",
    target_uri="viking://resources/",
)

# search(): 复杂任务，需要意图分析
results = await client.search(
    query="帮我实现用户登录功能",
    session_id=session.session_id,
)
```

**选择建议**：
- 明确知道要找什么 → 用 `find()`
- 复杂任务需要多种上下文 → 用 `search()`

### 如何使用会话管理？

会话管理是 OpenViking 的核心能力，支持对话追踪和记忆提取：

```python
from openviking_sdk import TextPart

# 创建会话
session_info = await client.create_session()
session = client.session(session_id=session_info["session_id"])

# 添加对话消息
await session.add_message(
    role="user",
    parts=[TextPart(text="帮我分析这段代码的性能问题")],
)
await session.add_message(
    role="assistant",
    parts=[TextPart(text="我来分析一下...")],
)

# 提交会话，触发记忆提取
await session.commit()
```

### OpenViking 支持哪些记忆类型？

OpenViking 内置 `profile`、`preferences`、`entities`、`events`、`identity`、`soul`、`cases`、`trajectories`、`experiences` 等记忆类型。提交会话后，系统会按当前记忆策略提取适用内容；也可以根据业务需要扩展或调整记忆类型。

记忆存储在当前用户或 Peer 命名空间，不存在当前可写的 `viking://agent/memories` 目录。完整类型与路径见 [上下文类型](../concepts/02-context-types.md)。

### 如何使用类 Unix 的文件系统 API？

```python
# 列出目录内容
items = await client.ls(uri="viking://resources/")

# 读取完整内容（L2）
content = await client.read(uri="viking://resources/doc.md")

# 获取摘要（L0）
abstract = await client.abstract(uri="viking://resources")

# 获取概览（L1）
overview = await client.overview(uri="viking://resources")
```

## 检索优化

### 如何提升检索质量？

1. **评估 Rerank 模型**：对代表性查询比较排序结果，再决定是否启用
2. **检查摘要**：确认 L0/L1 是否准确反映来源；需要调整时使用导入的 `instruction` 或摘要模板
3. **组织目录结构**：导入时用 `parent` 指定现有父目录，或用 `to` 指定最终 URI
4. **使用会话上下文**：保持 `retrieval.enable_intent` 开启（默认），并向 `search()` 传入有内容的会话
5. **选择合适的 Embedding 模式**：多模态内容使用 `multimodal` 输入

### 检索结果的分数是如何计算的？

目录递归检索会传播父目录分数。传播权重由 `retrieval.score_propagation_alpha` 配置，默认值为 `1.0`，即只使用当前候选自身的分数：

```
传播分数 = α × 当前候选分数 + (1 − α) × 父目录分数
```

当前候选分数可来自向量检索或 Rerank，取决于检索模式和配置。最终排序还可能受热度等因素影响，不能仅用这一公式解释所有返回分数。详见[检索机制](../concepts/07-retrieval.md)。

### 什么是目录递归检索？

目录递归检索先定位相关目录，再向下查找：

1. **准备查询**：`find()` 直接使用查询，`search()` 可先分析意图
2. **初始定位**：向量检索定位高分目录
3. **精细探索**：在高分目录下进行二次检索
4. **递归下探**：逐层递归直到收敛
5. **结果汇总**：返回最相关的上下文

目录分数参与下层候选排序。实际召回效果取决于来源组织、摘要、模型和查询。

## 故障排除

### 资源添加后没有被索引

**可能原因及解决方案**：

1. **未等待处理完成**
   ```python
   result = await client.add_resource(path="./doc.pdf", wait=True)
   print(result)
   ```
   新导入时可用 `wait=True` 等待。排查已经提交的导入时，用返回的 `task_id` 查询任务，不必重复导入。状态仍为 `pending` 或 `running` 时需要继续等待；`failed` 或 `cancelled` 时查看任务详情。请求超时也不能据此判断后台任务已经失败，详见[异步任务](../api/17-tasks.md)。

2. **Embedding 模型配置错误**
   - 检查 `~/.openviking/ov.conf` 中的 `api_key` 是否正确
   - 确认模型名称和 endpoint 配置正确

3. **文件格式不支持**
   - 检查文件扩展名是否在支持列表中
   - 确认文件内容有效且未损坏

4. **查看服务端处理日志**
   在运行服务端的终端或日志系统中查看对应任务的错误。客户端的 Python 日志设置不会开启远程服务端日志。

### 搜索没有返回预期结果

**排查步骤**：

1. **确认资源存在，再查询导入任务状态**
   用导入时返回的 `task_id` 查询任务；`completed` 表示处理完成，`failed` 或 `cancelled` 需先检查原因。
   ```python
   # 检查资源是否存在
   items = await client.ls(uri="viking://resources/")
   task = await client.get_task("<导入时返回的 task_id>")
   print(task["status"])
   ```

2. **检查 `target_uri` 过滤条件**
   - 确保搜索范围包含目标资源
   - 尝试扩大搜索范围

3. **尝试不同的查询方式**
   - 使用更具体或更宽泛的关键词
   - 尝试 `find()` 和 `search()` 对比效果

4. **检查 L0 摘要质量**
   ```python
   abstract = await client.abstract(uri="viking://resources/your-doc")
   print(abstract)  # 确认摘要是否准确反映内容
   ```

### 记忆提取不工作

**排查步骤**：

1. **确保调用了 `commit()`**
   ```python
   await session.commit()  # 触发记忆提取
   ```

2. **检查 VLM 配置**
   - 记忆提取需要 VLM 模型
   - 确认 `vlm` 配置正确

3. **确认对话内容有意义**
   - 闲聊内容可能不会产生记忆
   - 需要包含可提取的信息（偏好、实体、事件等）

4. **查看记忆目录**
   ```python
   memories = await client.ls(uri="viking://~/memories/")
   ```

### 性能问题

**优化建议**：

1. **定位瓶颈**：先检查处理队列、模型延迟和存储耗时，再调整并发
2. **合理设置 `batch_size`**：Embedding 配置中调整批处理大小
3. **使用本地存储**：开发阶段使用 `local` 后端减少网络延迟
4. **异步操作**：充分利用 `AsyncHTTPClient` 的异步特性

## 部署相关

### OpenViking 是开源的吗？

OpenViking 主体采用 AGPLv3，CLI 和大部分示例采用 Apache 2.0。各组件及例外见仓库的[许可证说明](https://github.com/volcengine/OpenViking#license)。

## 相关文档

- [简介](../getting-started/01-introduction.md) - 了解 OpenViking 的设计理念
- [快速开始](../getting-started/02-quickstart.md) - 首次导入与检索
- [架构概述](../concepts/01-architecture.md) - 深入理解系统设计
- [检索机制](../concepts/07-retrieval.md) - 检索流程详解
- [配置指南](../guides/01-configuration.md) - 完整配置参考
