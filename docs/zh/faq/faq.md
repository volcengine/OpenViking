# 常见问题

## 基础概念

### OpenViking 是什么？解决什么问题？

OpenViking 是一个专为 AI Agent 设计的开源上下文数据库。它解决了构建 AI Agent 时的核心痛点：

- **上下文碎片化**：记忆、资源、技能散落各处，难以统一管理
- **检索效果不佳**：传统 RAG 平铺式存储缺乏全局视野，难以理解完整语境
- **上下文不可观测**：隐式检索链路如同黑箱，出错时难以调试
- **记忆迭代有限**：缺乏 Agent 相关的任务记忆和自我进化能力

OpenViking 通过文件系统范式统一管理所有上下文，实现分层供给与自我迭代。

### OpenViking 和传统向量数据库有什么本质区别？

| 维度 | 传统向量数据库 | OpenViking |
|------|---------------|------------|
| **存储模型** | 扁平化向量存储 | 层级化文件系统（AGFS） |
| **检索方式** | 单一向量相似度搜索 | 目录递归检索 + 意图分析 + Rerank |
| **输出形式** | 原始分块 | 结构化上下文（L0 摘要/L1 概览/L2 详情） |
| **记忆能力** | 不支持 | 内置 6 种记忆分类，支持自动提取和迭代 |
| **可观测性** | 黑箱 | 检索轨迹完整可追溯 |
| **上下文类型** | 仅文档 | Resource + Memory + Skill 三种类型 |

### 什么是 L0/L1/L2 分层模型？为什么需要它？

L0/L1/L2 是 OpenViking 的渐进式内容加载机制，解决了"海量上下文一次性塞入提示词"的问题：

| 层级 | 名称 | Token 限制 | 用途 |
|------|------|-----------|------|
| **L0** | 摘要 | ~100 tokens | 向量搜索召回、快速过滤、列表展示 |
| **L1** | 概览 | ~2000 tokens | Rerank 精排、内容导航、决策参考 |
| **L2** | 详情 | 无限制 | 完整原始内容、按需深度加载 |

这种设计让 Agent 可以先浏览摘要快速定位，再按需加载详情，显著节省 Token 消耗。

### Viking URI 是什么？有什么作用？

Viking URI 是 OpenViking 的统一资源标识符，格式为 `viking://{scope}/{path}`。它让系统能精准定位任何上下文：

```
viking://
├── resources/              # 知识库：文档、代码、网页等
│   └── my_project/
├── user/                   # 用户上下文
│   └── memories/           # 用户记忆（偏好、实体、事件）
└── agent/                  # Agent 上下文
    ├── skills/             # 可调用技能
    └── memories/           # Agent 记忆（案例、模式）
```

## 安装与配置

### 环境要求是什么？

- **Python 版本**：3.10 或更高
- **必需依赖**：Embedding 模型（推荐火山引擎 Doubao）
- **可选依赖**：
  - VLM（视觉语言模型）：用于多模态内容处理和语义提取
  - Rerank 模型：用于提升检索精度

### 如何安装 OpenViking？

```bash
pip install openviking
```

### 如何配置 OpenViking？

在项目目录创建 `~/.openviking/ov.conf` 配置文件：

```json
{
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "api_key": "your-api-key",
      "model": "doubao-embedding-vision-250615",
      "dimension": 1024,
      "input": "multimodal"
    }
  },
  "vlm": {
    "provider": "volcengine",
    "api_key": "your-api-key",
    "model": "doubao-seed-1-8-251228",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3"
  },
  "rerank": {
    "provider": "volcengine",
    "api_key": "your-api-key",
    "model": "doubao-rerank-250615"
  },
  "storage": {
    "agfs": { "backend": "local", "path": "./data" },
    "vectordb": { "backend": "local", "path": "./data" }
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

支持 Dense、Sparse 和 Hybrid 三种 Embedding 模式。

## 使用指南

### 如何初始化客户端？

```python
import openviking as ov

# 异步客户端（推荐）- 嵌入模式
client = ov.AsyncOpenViking(path="./my_data")
await client.initialize()

# 异步客户端 - 服务模式
client = ov.AsyncHTTPClient(url="http://localhost:1933", api_key="your-key")
await client.initialize()
```

SDK 构造函数仅接受 `url`、`api_key`、`path` 参数。其他配置（embedding、vlm 等）通过 `ov.conf` 配置文件管理。

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
    "./document.pdf",
    reason="项目技术文档",  # 描述资源用途，提升检索质量
    target="viking://resources/docs/"  # 指定存储位置
)

# 添加网页
await client.add_resource(
    "https://example.com/api-docs",
    reason="API 参考文档"
)

# 等待处理完成
await client.wait_processed()
```

### `find()` 和 `search()` 有什么区别？应该用哪个？

| 特性 | `find()` | `search()` |
|------|----------|------------|
| **会话上下文** | 不需要 | 需要 |
| **意图分析** | 不使用 | 使用 LLM 分析生成 0-5 个查询 |
| **延迟** | 低 | 较高 |
| **适用场景** | 简单语义搜索 | 复杂任务、需要理解上下文 |

```python
# find(): 简单直接的语义搜索
results = await client.find(
    "OAuth 认证流程",
    target_uri="viking://resources/"
)

# search(): 复杂任务，需要意图分析
results = await client.search(
    "帮我实现用户登录功能",
    session_info=session
)
```

**选择建议**：
- 明确知道要找什么 → 用 `find()`
- 复杂任务需要多种上下文 → 用 `search()`

### 如何使用会话管理？

会话管理是 OpenViking 的核心能力，支持对话追踪和记忆提取：

```python
# 创建会话
session = client.session()

# 添加对话消息
await session.add_message("user", [{"type": "text", "text": "帮我分析这段代码的性能问题"}])
await session.add_message("assistant", [{"type": "text", "text": "我来分析一下..."}])

# 标记使用的上下文（用于追踪）
await session.used(["viking://resources/code/main.py"])

# 提交会话，触发记忆提取
await session.commit()
```

### OpenViking 支持哪些记忆类型？

OpenViking 内置 6 种记忆分类，在会话提交时自动提取：

| 分类 | 归属 | 说明 |
|------|------|------|
| **profile** | user | 用户基本信息（姓名、角色等） |
| **preferences** | user | 用户偏好（代码风格、工具选择等） |
| **entities** | user | 实体记忆（人物、项目、组织等） |
| **events** | user | 事件记录（决策、里程碑等） |
| **cases** | agent | Agent 学习的案例 |
| **patterns** | agent | Agent 学习的模式 |

### 如何使用类 Unix 的文件系统 API？

```python
# 列出目录内容
items = await client.ls("viking://resources/")

# 读取完整内容（L2）
content = await client.read("viking://resources/doc.md")

# 获取摘要（L0）
abstract = await client.abstract("viking://resources")

# 获取概览（L1）
overview = await client.overview("viking://resources")
```

## 检索优化

### 如何提升检索质量？

1. **使用 Rerank 模型**：配置 Rerank 可显著提升精排效果
2. **提供有意义的 `reason`**：添加资源时描述用途，帮助系统理解资源价值
3. **合理组织目录结构**：使用 `target` 参数将相关资源放在一起
4. **使用会话上下文**：`search()` 会利用会话历史进行意图分析
5. **选择合适的 Embedding 模式**：多模态内容使用 `multimodal` 输入

### 检索结果的分数是如何计算的？

OpenViking 使用分数传播机制：

```
最终分数 = 0.5 × Embedding 相似度 + 0.5 × 父目录分数
```

这种设计让高分目录下的内容获得加成，体现了"上下文语境"的重要性。

### 什么是目录递归检索？

目录递归检索是 OpenViking 的创新检索策略：

1. **意图分析**：分析查询生成多个检索条件
2. **初始定位**：向量检索定位高分目录
3. **精细探索**：在高分目录下进行二次检索
4. **递归下探**：逐层递归直到收敛
5. **结果汇总**：返回最相关的上下文

这种策略能找到语义匹配的片段，同时理解信息的完整语境。

## 故障排除

### 资源添加后没有被索引

**可能原因及解决方案**：

1. **未等待处理完成**
   ```python
   await client.add_resource("./doc.pdf")
   await client.wait_processed()  # 必须等待
   ```

2. **Embedding 模型配置错误**
   - 检查 `~/.openviking/ov.conf` 中的 `api_key` 是否正确
   - 确认模型名称和 endpoint 配置正确

3. **文件格式不支持**
   - 检查文件扩展名是否在支持列表中
   - 确认文件内容有效且未损坏

4. **查看处理日志**
   ```python
   import logging
   logging.basicConfig(level=logging.DEBUG)
   ```

### 搜索没有返回预期结果

**排查步骤**：

1. **确认资源已处理完成**
   ```python
   # 检查资源是否存在
   items = await client.ls("viking://resources/")
   ```

2. **检查 `target_uri` 过滤条件**
   - 确保搜索范围包含目标资源
   - 尝试扩大搜索范围

3. **尝试不同的查询方式**
   - 使用更具体或更宽泛的关键词
   - 尝试 `find()` 和 `search()` 对比效果

4. **检查 L0 摘要质量**
   ```python
   abstract = await client.abstract("viking://resources/your-doc")
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

4. **查看提取的记忆**
   ```python
   memories = await client.find("", target_uri="viking://user/memories/")
   ```

### 性能问题

**优化建议**：

1. **批量处理**：一次添加多个资源比逐个添加更高效
2. **合理设置 `batch_size`**：Embedding 配置中调整批处理大小
3. **使用本地存储**：开发阶段使用 `local` 后端减少网络延迟
4. **异步操作**：充分利用 `AsyncOpenViking` / `AsyncHTTPClient` 的异步特性

## 部署相关

### 嵌入式模式和服务模式有什么区别？

| 模式 | 适用场景 | 特点 |
|------|----------|------|
| **嵌入式** | 本地开发、单进程应用 | 自动启动 AGFS 子进程，使用本地向量索引 |
| **服务模式** | 生产环境、分布式部署 | 连接远程服务，支持多实例并发，可独立扩展 |

```python
# 嵌入式模式
client = ov.AsyncOpenViking(path="./data")

# 服务模式
client = ov.AsyncHTTPClient(url="http://localhost:1933", api_key="your-key")
```

### OpenViking 是开源的吗？

是的，OpenViking 完全开源，采用 Apache 2.0 许可证。

## 相关文档

- [简介](../getting-started/01-introduction.md) - 了解 OpenViking 的设计理念
- [快速开始](../getting-started/02-quickstart.md) - 5 分钟上手教程
- [架构概述](../concepts/01-architecture.md) - 深入理解系统设计
- [检索机制](../concepts/07-retrieval.md) - 检索流程详解
- [配置指南](../guides/01-configuration.md) - 完整配置参考
