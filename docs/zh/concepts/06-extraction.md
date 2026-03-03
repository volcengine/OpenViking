# 上下文提取

OpenViking 采用三层异步架构处理文档解析和上下文提取。

## 概览

```
输入文件 → Parser → TreeBuilder → SemanticQueue → 向量库
           ↓           ↓              ↓
        解析转换    文件移动     L0/L1 生成
        (无 LLM)   入队语义      (LLM 异步)
```

**设计原则**：解析与语义分离，Parser 不调用 LLM，语义生成异步进行。

## Parser（解析器）

Parser 负责文档格式转换和结构化，在临时目录创建文件结构。

### 支持格式

| 格式 | 解析器 | 扩展名 | 支持情况 |
|------|--------|--------|------|
| Markdown | MarkdownParser | .md, .markdown | 已支持 |
| 纯文本 | TextParser | .txt | 已支持 |
| PDF | PDFParser | .pdf | 已支持 |
| HTML | HTMLParser | .html, .htm | 已支持 |
| 代码 | CodeRepositoryParser | github 代码仓库等 | 已支持 |
| 图片 | ImageParser | .png, .jpg 等 |  |
| 视频 | AudioParser | .mp3, .wav 等 |  |
| 音频 | VideoParser | .mp4, .avi 等 |  |

### 核心流程 (以文档为例)

```python
# 1. 解析文件
parse_result = registry.parse("/path/to/doc.md")

# 2. 返回临时目录 URI
parse_result.temp_dir_path  # viking://temp/abc123
```

### 智能分割

```
如果 document_tokens <= 1024:
    → 保存为单文件
否则:
    → 按标题分割
    → 小节 < 512 tokens → 合并
    → 大节 > 1024 tokens → 创建子目录
```

### 返回结果

```python
ParseResult(
    temp_dir_path: str,    # 临时目录 URI
    source_format: str,    # pdf/markdown/html
    parser_name: str,      # 解析器名称
    parse_time: float,     # 耗时（秒）
    meta: Dict,            # 元数据
)
```

## TreeBuilder（树构建器）

TreeBuilder 负责将临时目录移动到 AGFS，并入队语义处理。

### 核心流程

```python
building_tree = tree_builder.finalize_from_temp(
    temp_dir_path="viking://temp/abc123",
    scope="resources",  # resources/user/agent
)
```

### 5 阶段处理

1. **查找文档根目录**：确保临时目录下恰好 1 个子目录
2. **确定目标 URI**：根据 scope 映射基础 URI
3. **递归移动目录树**：复制所有文件到 AGFS
4. **清理临时目录**：删除临时文件
5. **入队语义生成**：提交 SemanticMsg 到队列

### URI 映射

| scope | 基础 URI |
|-------|----------|
| resources | `viking://resources` |
| user | `viking://user` |
| agent | `viking://agent` |

## SemanticQueue（语义队列）

SemanticQueue 异步处理 L0/L1 生成和向量化。

### 消息结构

```python
SemanticMsg(
    id: str,           # UUID
    uri: str,          # 目录 URI
    context_type: str, # resource/memory/skill
    status: str,       # pending/processing/completed
)
```

### 处理流程（自底向上）

```
叶子目录 → 父目录 → 根目录
```

### 单目录处理步骤

1. **并发生成文件摘要**：限制并发数 10
2. **收集子目录摘要**：读取已生成的 .abstract.md
3. **生成 .overview.md**：LLM 生成 L1 概览
4. **提取 .abstract.md**：从 overview 提取 L0 摘要
5. **写入文件**：保存到 AGFS
6. **向量化**：创建 Context 并入队 EmbeddingQueue

### 配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `max_concurrent_llm` | 10 | 并发 LLM 调用数 |
| `max_images_per_call` | 10 | 单次 VLM 最大图片数 |
| `max_sections_per_call` | 20 | 单次 VLM 最大章节数 |

## 代码骨架提取（AST 模式）

对于代码文件，OpenViking 支持基于 tree-sitter 的 AST 骨架提取，作为 LLM 摘要的轻量替代方案，可显著降低处理成本。

### 工作模式

在 `ov.conf` 中通过 `code_summary_mode` 字段控制（参见[配置文档](../guides/01-configuration.md#code)），支持三种模式：

| 模式 | 说明 |
|------|------|
| `"ast"` | 对 ≥100 行的代码文件提取结构骨架，跳过 LLM 调用（**默认**） |
| `"llm"` | 全部走 LLM 生成摘要（原有行为） |
| `"ast_llm"` | 先提取 AST 骨架，再将骨架作为上下文辅助 LLM 生成摘要 |

### AST 提取内容

提取的骨架包含：

- 模块级 docstring（首行）
- import 语句列表
- 类名、继承关系及方法签名（`ast` 模式仅保留 docstring 首行，`ast_llm` 模式保留完整 docstring）
- 顶层函数签名

### 支持语言

以下语言有专属 extractor，基于 tree-sitter 实现精确提取：

| 语言 | 说明 |
|------|------|
| Python | 完整支持 |
| JavaScript / TypeScript | 完整支持 |
| Rust | 完整支持 |
| Go | 完整支持 |
| Java | 完整支持 |
| C / C++ | 完整支持 |

其他语言不在支持列表内，自动 fallback 到 LLM。

### Fallback 机制

以下情况自动回退到 LLM，并在日志中记录原因，整体流程不受影响：

- 语言不在支持列表中
- 文件行数 < 100
- AST 解析报错
- 提取结果为空骨架

### 文件结构

```
openviking/parse/parsers/code/ast/
├── extractor.py      # 语言检测 + 分发入口
├── skeleton.py       # CodeSkeleton / FunctionSig / ClassSkeleton 数据结构
└── languages/        # 各语言专属 extractor
```

## 三种上下文提取

### 流程对比

| 环节 | Resource | Memory | Skill |
|------|----------|--------|-------|
| **Parser** | 通用流程 | 通用流程 | 通用流程 |
| **基础 URI** | `viking://resources` | `viking://user/memories` | `viking://agent/skills` |
| **TreeBuilder scope** | resources | user/agent | agent |
| **SemanticMsg type** | resource | memory | skill |

### 资源提取

```python
# 添加资源
await client.add_resource(
    "/path/to/doc.pdf",
    reason="API 文档"
)

# 流程: Parser → TreeBuilder(scope=resources) → SemanticQueue
```

### 技能提取

```python
# 添加技能
await client.add_skill({
    "name": "search-web",
    "content": "# search-web\\n..."
})

# 流程: 直接写入 viking://agent/skills/{name}/ → SemanticQueue
```

### 记忆提取

```python
# 记忆从会话自动提取
await session.commit()

# 流程: MemoryExtractor → TreeBuilder(scope=user) → SemanticQueue
```

## 相关文档

- [架构概述](./01-architecture.md) - 系统整体架构
- [上下文层级](./03-context-layers.md) - L0/L1/L2 模型
- [存储架构](./05-storage.md) - AGFS 和向量库
- [会话管理](./08-session.md) - 记忆提取详解
