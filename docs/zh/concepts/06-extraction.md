# 上下文提取

资源导入依次经过解析、目标路径确定、内容写入、语义生成和向量化。记忆和技能有各自的入口，也会使用语义处理和向量索引。

## 概览

```text
输入 → Parser → TreeBuilder → 内容写入 → SemanticQueue → EmbeddingQueue → 向量库
        解析产物    目标 URI                 L0/L1 生成
```

解析与目录语义生成分开处理。具体解析器可能调用模型或外部解析服务，因此不能把解析阶段视为一律不使用 LLM。导入接口通常先返回任务 ID；需要立即检索时，应先确认该任务完成，见[任务管理](../api/17-tasks.md)。

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
| 图片 | ImageParser | .png, .jpg 等 | 已支持；处理依赖媒体配置 |
| 视频 | VideoParser | .mp4, .avi, .mov, .mkv, .webm, .flv, .wmv, .ts（仅 MPEG-TS 内容） | 已支持；处理依赖媒体配置 |
| 音频 | AudioParser | .mp3, .wav, .ogg, .flac, .aac, .m4a, .opus, .ac3 | 已支持；处理依赖媒体配置 |

### 解析产物

内部 `registry.parse()` 是异步接口，返回 `ParseResult`。解析产物可以存放在本地临时目录或 AGFS；`temp_dir_path` 不一定是 Viking URI，`artifact_ref` 用来标识其存储后端。这些是服务端实现细节，客户端应使用资源导入 API。

| 字段 | 含义 |
| --- | --- |
| `root` | 解析后的资源树根节点 |
| `temp_dir_path` | 临时产物位置 |
| `artifact_ref` | 产物后端及根路径 |
| `source_format` / `parser_name` | 源格式和解析器名称 |
| `parse_time` | 解析耗时（秒） |
| `meta` / `warnings` | 元数据及解析警告 |

### 文档分节

Markdown 解析器按标题和大小组织章节，合并较短小节，拆分过长内容。默认 `max_section_size` 为 2048 tokens，`max_section_chars` 为 6000 字符；`section_size_flexibility` 默认为 0.3，允许为保持内容连贯而适度超出 token 目标。过长的单个表格行可以保持完整。这些值是分节目标，不能当作每个输出文件的绝对上限。

不同解析器和 `parse_mode` 的行为不同。需要保持单文件时，可在支持的导入中使用 `parse_mode="no_split"`，详见[资源管理](../api/02-resources.md)。

## TreeBuilder（树构建器）

`TreeBuilder.finalize_from_temp()` 检查解析产物并确定最终 URI，返回包含根节点和临时位置的 `BuildingTree`。它本身不复制文件、不清理产物，也不提交语义任务。

### 导入中的后续步骤

1. 根据解析产物、`to` 或 `parent` 确定目标 URI，并检查目标路径。
2. 导入处理器将内容写入最终存储，处理资源锁和已有内容更新。
3. 提交后续语义处理和向量化，按产物所属后端清理临时数据。

默认资源根目录是 `viking://resources`。个人资源应显式指定 `viking://~/resources/...`；`viking://user` 是用户空间容器，不能当作自己的资源根。

## SemanticQueue（语义队列）

SemanticQueue 异步处理 L0/L1 生成和向量化。

### 消息结构

下面列出部分内部消息字段，不是客户端提交格式：

| 字段 | 含义 |
| --- | --- |
| `id` | 消息 UUID |
| `uri` | 待处理目录 |
| `context_type` | resource、memory、skill 或 session |
| `status` | 队列处理状态 |
| `recursive` | 是否处理子目录 |
| `propagate_to_parent` | 完成后是否允许安排父目录刷新 |

### 处理流程（自底向上）

```
叶子目录 → 父目录 → 根目录
```

### 单目录处理步骤

1. **并发生成文件摘要**：并发上限由 `vlm.max_concurrent` 控制
2. **收集子目录摘要**：读取已生成的 .abstract.md
3. **生成 .overview.md**：LLM 生成 L1 概览
4. **提取 .abstract.md**：从 overview 提取 L0 摘要
5. **写入文件**：以 OKF Markdown 保存正文和受保护元数据
6. **向量化**：创建 Context 并入队 EmbeddingQueue

L0/L1 是目录级 sidecar，不是 per-file sidecar。生成父目录摘要时只使用子目录 L0 的正文，OKF frontmatter 不进入 prompt。Embedding 使用正文和白名单中的 `directory`；`source`、`generated_by`、`freshness` 不进入向量输入。

### Freshness、采样与父级刷新

每次生成都会记录直接子项覆盖情况，超过 `semantic.overview_sample_limit`（默认 32）时使用稳定采样。resource/skill 的父级刷新取决于子目录 L0 正文变化和 freshness 阈值，L0 正文不变时不向上传播。`pending_child_changes` 统计等待刷新的变化事件，同一子项重复变化也会分别计数。阈值、手动刷新和延后更新的规则见[上下文层级](03-context-layers.md)。

### 处理限制

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `vlm.max_concurrent` | 32 | 语义处理并发上限，传入 `SemanticProcessor.max_concurrent_llm` |
| `max_images_per_call` | 10 | `VLMProcessor` 构造参数：单次最大图片数，非 `ov.conf` 字段 |
| `max_sections_per_call` | 20 | `VLMProcessor` 构造参数：单次最大章节数，非 `ov.conf` 字段 |
| `semantic.overview_sample_limit` | 32 | 单个目录摘要使用的直接子项样本上限 |

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
| **入口** | 资源解析器 | 会话提取、记忆更新 | 技能导入 |
| **基础 URI** | `viking://resources` | `viking://~/memories` | `viking://~/skills` |
| **SemanticMsg type** | resource | memory | skill |

以下示例使用已配置的同步 Python SDK 客户端 `client`。

### 资源提取

```python
# 添加资源
client.add_resource(
    path="/path/to/doc.pdf",
    options={"reason": "API 文档"},
)

# 流程: Parser → TreeBuilder(scope=resources) → SemanticQueue
```

### 技能提取

```python
# 添加技能
client.add_skill(
    data={
        "name": "search-web",
        "content": "# search-web\n...",
    },
)

# 流程: 直接写入 viking://~/skills/{name}/ → SemanticQueue
```

### 记忆提取

```python
# 记忆从会话自动提取
client.commit_session(session_id)

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
