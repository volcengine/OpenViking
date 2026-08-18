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
| 代码 | CodeRepositoryParser | github 代码仓库等 | 遵循 `.gitignore` 并忽略常见非代码目录 |
| 图片 | ImageParser | .png, .jpg 等 |  |
| 视频 | VideoParser | .mp4, .avi, .mov, .mkv, .webm, .flv, .wmv |  |
| 音频 | AudioParser | .mp3, .wav, .ogg, .flac, .aac, .m4a, .opus |  |

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
    scope="resources",  # resources/user
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

### 处理限制

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `max_concurrent_llm` | 10 | 并发 LLM 调用数 |
| `max_images_per_call` | 10 | 单次 VLM 最大图片数 |
| `max_sections_per_call` | 20 | 单次 VLM 最大章节数 |

## 代码骨架提取

对于代码文件，OpenViking 使用固定的代码骨架提取路线。该路线内置在代码摘要流程中，不再通过逐语言解析参数选择或调节。

### 代码骨架内容

骨架可包含 import、类、方法、函数及其他语言级符号。具体输出取决于该语言维护中的 query 或通用解析结果，但提取路线本身是固定的。

### 提取路线

代码骨架提取按以下固定顺序执行：

1. 语言存在维护中的 `tags.scm` 时，优先使用 tags query。
2. 不存在对应的 `tags.scm` 时，使用 `tree-sitter-language-pack.process()`。
3. 两种提取方式都无可用结果时，才将 `semantic.code_summary` 作为兜底处理。

长短代码文件都遵循同一路由。

## 三种上下文提取

### 流程对比

| 环节 | Resource | Memory | Skill |
|------|----------|--------|-------|
| **Parser** | 通用流程 | 通用流程 | 通用流程 |
| **基础 URI** | `viking://resources` | `viking://user/memories` | `viking://user/skills` |
| **TreeBuilder scope** | resources | user | user |
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

# 流程: 直接写入 viking://user/skills/{name}/ → SemanticQueue
```

### 记忆提取

```python
# 记忆从会话自动提取
await session.commit()

# 流程: SessionCompressorV3 → ExtractLoop → MemoryUpdater → SemanticQueue
```

V3 只提供一个提取入口。它先提取启用的用户记忆 schema（包括 `cases`）；
只有本次提取实际产生至少一个 case，才会继续训练 trajectory、experience，
以及可选的可执行 session skill。没有 case 的会话不会生成这些执行派生记忆。

## 相关文档

- [架构概述](./01-architecture.md) - 系统整体架构
- [上下文层级](./03-context-layers.md) - L0/L1/L2 模型
- [存储架构](./05-storage.md) - AGFS 和向量库
- [会话管理](./08-session.md) - 记忆提取详解
