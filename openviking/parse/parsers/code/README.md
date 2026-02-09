# 代码解析方案 (Code Parser)

OpenViking 通过 **Code Parser** 模块实现对代码仓库的整体解析与理解。与普通文档的拆解式处理不同，代码解析采用了基于目录结构的整体映射策略，旨在保持代码项目的完整上下文。

## 概览

| 特性 | 策略 | 说明 |
|------|------|------|
| **解析粒度** | 文件级 | 不进行 Chunking 拆分，保持单文件完整性 |
| **目录映射** | 1:1 映射 | 本地目录结构直接映射为 Viking URI 路径 |
| **处理模式** | 异步处理 | Parser 负责搬运，SemanticProcessor 负责理解 |
| **元数据** | 自动提取 | 提取语言、依赖、符号定义等基础信息 |

## 核心设计思考

代码仓库作为一种特殊的资源类型，具有以下显著特征，这些特征直接决定了我们的技术方案：

1.  **文件粒度适中**：大多数代码文件（KB 级）都在大模型上下文窗口范围内（<10k tokens），无需像长文档那样进行物理切分。
2.  **结构即语义**：代码的目录结构（Directory Structure）本身就蕴含了模块划分、层级依赖等重要架构信息，必须严格保留。
3.  **高频迭代**：代码变动频繁，系统需支持增量更新，避免重复索引未变动的文件。
4.  **元数据丰富**：代码中的注释、DocString、Import 语句等包含了高密度的语义信息。

## 上下文映射体系

我们将代码仓库映射到 OpenViking 的标准分层描述体系中。

### 1. Viking URI 映射

假设用户导入了 `OpenViking` 仓库：

```python
client.add_resource(
    "https://github.com/volcengine/OpenViking",
    target="viking://resources/github/volcengine/OpenViking"
)
```

系统将生成如下标准化的目录树结构，能够完整体现深层级的文件路径：

```text
viking://resources/github/volcengine/OpenViking/
├── .abstract.md        # L0: 项目级摘要
├── .overview.md        # L1: 项目级概览
├── docs/
│   ├── .abstract.md
│   ├── .overview.md
│   ├── zh/...
│   └── en/...
├── src/
│   ├── .abstract.md
│   ├── .overview.md
│   └── index/          # 深层目录结构
│       ├── .abstract.md
│       ├── .overview.md
│       └── index/      # 更深层的子模块
│           ├── .abstract.md
│           ├── .overview.md
│           ├── index_engine.cpp    # L2: 具体代码文件（C++）
│           └── ...
└── openviking/
    ├── .abstract.md
    ├── .overview.md
    └── ...
```

在这颗目录树中，每一层目录都会有一个 `.abstract.md` 文件和 `.overview.md` 文件：
*   `.abstract.md`：目录的摘要，介绍本目录的功能和在项目中的作用。
*   `.overview.md`：目录的概览，介绍本目录的文件结构、关键实体的位置等。

### 2. 语义层级 (Context Layers)

*   **L0 (Abstract)**：目录的简短功能描述，用于快速检索。
*   **L1 (Overview)**：目录的详细概览，包含文件结构分析、关键类/函数索引。
*   **L2 (Detail)**：原始代码文件内容。对于代码文件，我们**不进行拆分**，直接存储完整内容。

## 数据处理原则

1. 本方案对于任意编程语言的代码仓库均适用，不应该特殊处理任意编程语言的差异性，需要考虑策略足够通用。
2. 对于代码仓库中的文档，除了图片以外，不要让大模型处理文本以外的其他模态内容，如视频、音频等。
   - **说明**：".md"、".txt"、".rst" 等纯文本格式的文档文件**会被处理**，因为它们属于"文本内容"
   - **排除**：视频（.mp4, .mov, .avi 等）、音频（.mp3, .wav, .m4a 等）等非文本格式**不会被处理**
3. 可以忽略代码仓库中的隐藏文件，如 .git 文件夹下面的内容，__pycache__ 文件夹下面的内容等。
4. 对于代码仓库中的符号链接，我们应当忽略并记录其目标路径，而不是直接解析符号链接。
5. 对于代码仓库中的子目录，我们应当递归地处理，确保所有包含代码的目录，都被正确映射到 Viking URI 路径。

## 技术实现方案

### 1. 仓库识别与拉取

扩展 `URLTypeDetector` 以支持代码仓库识别：

*   **识别逻辑**：检测 URL 是否为 GitHub/GitLab 一级仓库地址（如 `https://github.com/org/repo` 或 `*.git`）。
*   **拉取策略**：
    *   **Git Clone**：优先使用 `git clone --depth 1` 进行浅克隆，速度最快。
    *   **Zip Download**：作为降级方案，下载 `main.zip` 或 `master.zip`。
*   **过滤机制**：内置过滤规则，自动忽略 `.git`, `.idea`, `__pycache__`, `node_modules` 等非代码资源。

### 2. 解析流程 (CodeRepositoryParser)

解析器遵循 V5.0 的异步处理架构：

1.  **物理搬运 (Parser Phase)**：
    *   将拉取到的代码仓库（经过过滤）完整上传到 `viking://temp/{uuid}/` 临时目录。
    *   在此阶段**不进行**任何 LLM 调用，确保 `add_resource` 接口能快速返回。
    *   仅进行基础的静态分析（如文件类型识别）。

2.  **异步理解 (Semantic Phase)**：
    *   `TreeBuilder` 将临时目录移入正式路径（如 `viking://resources/...`）。
    *   系统自动生成 `SemanticMsg` 并推入 `SemanticQueue`。
    *   后台 `SemanticProcessor` 消费消息，遍历目录树，异步生成各级目录的 `.abstract.md` 和 `.overview.md`。

### 3. 使用示例

```python
# 导入代码仓库
client.add_resource(
    "https://github.com/volcengine/OpenViking",
    target="viking://resources/github/volcengine/OpenViking",
    reason="引入 OpenViking 源码作为参考"
)

# 搜索代码逻辑
results = client.find(
    "OpenViking 和 VikingDB 的关系是什么？",
    target_uri="viking://resources/github/volcengine/OpenViking/OpenViking/docs/zh/"
)
```

> 考虑到当前性能不佳，可以用小一点的仓库测试：https://github.com/msgpack/msgpack-python

## 实现细节

### 文件过滤规则

代码解析器实现了以下过滤规则：

1. **隐藏目录忽略**：自动忽略 `.git`, `.idea`, `__pycache__`, `node_modules` 等非代码目录
2. **二进制文件忽略**：跳过 `.pyc`, `.so`, `.dll`, `.exe`, `.bin` 等编译文件
3. **媒体文件忽略**：不处理视频（.mp4, .mov, .avi 等）、音频（.mp3, .wav, .m4a 等）等非文本内容
4. **文档文件处理**：`.md`, `.txt`, `.rst` 等纯文本格式的文档文件**会被处理**，因为它们属于"文本内容"
5. **符号链接处理**：检测并跳过符号链接，记录目标路径但不解析内容
6. **文件大小限制**：跳过大于 10MB 的文件和零字节文件

### 文件类型检测

解析器包含辅助方法 `_detect_file_type()` 用于检测文件类型，可返回：
- `"code"`：编程语言文件（.py, .java, .js, .cpp 等）
- `"documentation"`：文档文件（.md, .txt, .rst 等）
- `"other"`：其他文本文件
- `"binary"`：二进制文件（已通过 `IGNORE_EXTENSIONS` 过滤）

### 测试验证

包含完整的测试文件 `tests/misc/test_code_parser.py` 验证：
- `IGNORE_DIRS` 包含所有必需的目录
- `IGNORE_EXTENSIONS` 包含所有必需的格式
- 符号链接处理正确实现
- 文件类型检测逻辑准确

### 优化 TODO
- 支持采用更轻量的模型进行文件摘要，加快处理速度
- 设计长任务的追踪机制，帮助观测任务队列中的任务归属，提供处理任务的统计信息
- 支持增量解析，只解析新增或变动的文件，避免重复解析已处理文件
- 大幅提升端到端的处理性能！

## 相关文档

*   [上下文类型](docs/zh/concepts/context-types.md)
*   [Viking URI](docs/zh/concepts/viking-uri.md)
*   [上下文层级](docs/zh/concepts/context-layers.md)
