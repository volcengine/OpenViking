# OpenViking 解析系统

OpenViking 的解析系统负责将各种格式的原始文档转换为结构化的上下文信息，遵循 L0/L1/L2 三层信息模型。该系统采用模块化设计，支持多种文档格式，并允许用户通过协议扩展自定义解析器。

## 核心架构

### 三层信息模型 (L0/L1/L2)

OpenViking 采用独特的三层信息模型来组织上下文内容，针对不同使用场景提供不同粒度的信息：

| 层级 | 文件 | 大小 | 用途 |
|------|------|------|------|
| **L0** | `.abstract.md` | <200 token | 摘要，支持向量化检索、用于目录下的快速浏览 |
| **L1** | `.overview.md` | <1000 token | 决策参考、内容导览 |
| **L2** | `content.md` 或原始文件 | 文件完整内容 | 供详细阅读（仅遍历终点）|

**设计出发点**：不同场景需要不同粒度的内容。通过上下文信息的分层，我们允许 Agent 按需读取需要的信息并节省上下文窗口的 token 消耗。

### 举例说明

一份技术文档的三层内容示例：

```
L0: "这是一份介绍 OpenViking 上下文数据库的技术文档，该文档主要介绍其中的上下文提取流程，涵盖资源解析和信息提取、存储和索引的流程规范，其中重点介绍了..."

L1: """
# 目录 (Line 1 ~ Line 10) 
目录部分提供了本文的目录结构，共划分为 4 个一级章节，分别是资源解析、信息提取、存储机制、语义索引机制。

# 资源解析 (Line 11 ~ Line 50) 
资源解析部分介绍了如何解析外部知识，包括文档、图片、视频等，包括具体的用例...

# 信息提取 (Line 51 ~ Line 100) 
信息提取部分介绍了如何从资源中提取摘要和导览信息，以文本文件、图片、视频文件等作为具体示例...

# 存储机制 (Line 101 ~ Line 150) 
存储机制部分介绍了如何将上下文存储到 VikingFS 中，包括文件系统、索引结构等。

# 语义索引机制 (Line 151 ~ Line 200) 
语义索引机制介绍了如何将上下文存储到向量库中，支持语义检索。
"""
```

### 解析流程 (v5.0 架构)

```
┌─────────────────────────────────────────────────────────────────────┐
│                         资源提取流程 (v5.0)                          │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────┐    ┌─────────────────────────────────────────────┐    │
│  │  文件   │───▶│                  Parser                      │    │
│  │ PDF/MD  │    │  ┌─────────────────────────────────────┐    │    │
│  │ 图片... │    │  │  解析 + 创建文件和目录结构          │    │    │
│  └─────────┘    │  │  (无 LLM 调用)                       │    │    │
│                 │  └──────────────┬──────────────────────┘    │    │
│                 └─────────────────┼───────────────────────────┘    │
│                                   │                                │
│                                   ▼ ParseResult                    │
│                                   │ (temp_dir_path)                │
│                 ┌─────────────────┴───────────────────────────┐    │
│                 │               TreeBuilder                    │    │
│                 │  ┌─────────┐  ┌──────────────────────────┐  │    │
│                 │  │ 1.移动  │─▶│ 2.入队 SemanticQueue     │  │    │
│                 │  │ 到AGFS  │  │   (自底向上处理)          │  │    │
│                 │  └─────────┘  └──────────────────────────┘  │    │
│                 └─────────────────────┬───────────────────────┘    │
│                                       │                             │
│                                       ▼ BuildingTree                │
│                 ┌─────────────────────┴───────────────────────┐    │
│                 │            SemanticQueue (异步)              │    │
│                 │  ┌─────────────────────────────────────┐    │    │
│                 │  │  SemanticProcessor (自底向上):      │    │    │
│                 │  │  1. 收集子目录 abstract             │    │    │
│                 │  │  2. 生成文件 summary (并发LLM)      │    │    │
│                 │  │  3. 生成 .abstract.md (L0)          │    │    │
│                 │  │  4. 生成 .overview.md (L1)          │    │    │
│                 │  │  5. 直接调用向量化写入              │    │    │
│                 │  └─────────────────────────────────────┘    │    │
│                 └─────────────────────┬───────────────────────┘    │
│                                       │                             │
│                                       ▼                             │
│                 ┌─────────────────────┴───────────────────────┐    │
│                 │                 AGFS + 向量库                │    │
│                 │     L0/L1/L2 文件  +  向量化索引             │    │
│                 └─────────────────────────────────────────────┘    │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

## 解析器类型

### 1. MarkdownParser (`markdown.py`)
**支持格式**: `.md`, `.markdown`, `.mdown`, `.mkd`

核心文档解析器，所有文本类文档最终都通过 MarkdownParser 处理。采用 v5.0 简化架构：

#### 解析逻辑（单阶段）
- 按标题层级（#, ##, ###）拆分章节，支持混合目录结构（文件 + 子目录）
- 切分规则：
  - 小文件（< 4000 tokens）：保留原文件名，直接保存
  - 大文件（> 4000 tokens）：按章节切分
  - 章节有子章节且总大小 > 4000：章节变目录，直接内容视为第一个虚拟子章节参与合并
  - 章节无子章节但超长：创建目录，按段落切分为 `章节名_1.md`, `章节名_2.md`
  - 正常章节：保存为 `章节名.md`
- 小章节合并规则（< 800 tokens）：
  - 连续的小章节会合并，直到总大小 >= 800 tokens 或无法与下一个章节合并
  - 合并后文件名用下划线拼接所有章节名：`章节A_章节B_章节C.md`
  - 直接内容（标题前或子标题前的内容）视为虚拟章节，使用父级名称参与合并
- 直接写入临时目录的文件和目录结构
- **无 LLM 调用**：语义生成移到 SemanticQueue 异步处理

#### 语义生成（异步，由 SemanticProcessor 处理）
- TreeBuilder 将目录移到 AGFS 后，入队到 SemanticQueue
- SemanticProcessor 自底向上处理每个目录：
  - 收集子目录的 `.abstract.md`
  - 并发生成文件 summary（LLM）
  - 生成当前目录的 `.abstract.md` (L0) 和 `.overview.md` (L1)
  - 直接调用向量化写入

### 2. PDFParser (`pdf.py`)
**支持格式**: `.pdf`

统一 PDF 解析器，采用双策略转换：
- **本地策略**: 使用 `pdfplumber` 进行文本和表格提取
- **远程策略**: 使用 `MinerU API` 进行高级 PDF 处理
- **自动策略**: 先尝试本地，失败时回退到 MinerU（如果配置了）

处理流程：`PDF → Markdown → ParseResult`，最终通过 MarkdownParser 处理。

### 3. HTMLParser (`html.py`)
**支持格式**: `.html`, `.htm`

使用 `readabilipy` 库提取可读内容，去除广告、导航等噪音元素，转换为 Markdown 后通过 MarkdownParser 处理。

### 4. TextParser (`text.py`)
**支持格式**: `.txt`, `.text`, `.log`, `.csv`, `.tsv`, `.json`, `.yaml`, `.yml`, `.xml`, `.ini`, `.cfg`, `.conf`

纯文本解析器，支持多种文本格式。对于结构化格式（JSON、YAML、XML）会尝试提取结构化信息。

### 5. CodeRepositoryParser (`code/*`)
**支持来源**: github 代码仓库等

代码解析器，支持语法高亮和代码结构分析。能识别函数、类、方法等代码元素。

### 6. MediaParser (`media/*.py`)

**支持格式**:
- 图片: `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`
- 视频: `.mp4`, `.mov`, `.avi`, `.webm`
- 音频: `.mp3`, `.wav`, `.m4a`, `.flac`

多媒体解析器，使用 VLM（视觉语言模型）分析图像、视频和音频内容，生成文本描述。多媒体解析器当且仅当 add-resource 调用时只添加上述文件类型时生效。即：
1. 当添加目录时，系统将对多媒体文件暂不生成单独目录和文本描述，仅存储和进行递归摘要。
2. 当单独添加多媒体文件时，多媒体解析器会直接解析该文件，然后通过单独目录存放，在目录下生成文本描述。

#### 存储组织策略

多媒体文件的存储采用以下层级结构：

```
viking://resource/
├── images/     # 图片文件
│   └── 20240820/  # 上传日期（YYYYMMDD）
│       └── 20240820_123456_jpg/  # 文件文件夹（文件名_扩展名）
│           ├── .abstract.md    # L0 摘要
│           ├── .overview.md    # L1 概览
│           └── 20240820_123456.jpg  # 原始文件
├── audio/      # 音频文件
│   └── 20240820/
│       └── my_song_mp3/
│           ├── .abstract.md
│           ├── .overview.md
│           └── my_song.mp3
└── video/      # 视频文件
    └── 20240820/
        └── my_video_mp4/
            ├── .abstract.md
            ├── .overview.md
            └── my_video.mp4
```

详细说明：

1. **媒体子目录**: 在 `viking://resource` 下按类型划分为三个子目录
   - `viking://resource/images`: 存储未明确指定目标路径的图片文件
   - `viking://resource/audio`: 存储未明确指定目标路径的音频文件
   - `viking://resource/video`: 存储未明确指定目标路径的视频文件

2. **日期子目录**: 每次上传的文件按当前日期（格式：YYYYMMDD）组织，而非文件内部元信息的时间
   - 例如：`viking://resource/images/20240820/` 存储 2024年8月20日上传的所有图片

3. **文件文件夹**: 为每个多媒体文件创建一个专属文件夹，命名规则为：`文件名_扩展名`（扩展名不含点）
   - 示例：上传 `20240820_123456.jpg` → 创建文件夹 `20240820_123456_jpg`

4. **文件夹内容**: 每个文件文件夹内包含：
   - **原始文件**: 保持原始文件名，空格字符替换为下划线 `_`（因为 OpenViking URI 不允许包含空格）
     - 示例：`photo 1.jpg` → 保存为 `photo_1.jpg`
   - `.abstract.md` (L0 层): 摘要信息（<200 token）
     - 图片：文件名、内容描述、画面风格等
     - 音频：文件名、时长、内容概述等
     - 视频：文件名、时长、内容概述等
   - `.overview.md` (L1 层): 概览信息
     - 图片：除摘要外，还包含尺寸、OCR 识别结果、场景和主体描述等
     - 音频：除摘要外，还包含语音/歌词识别结果、章节时间线等
     - 视频：除摘要外，还包含使用场景等（未来会支持切分视频，递归存储子文件）


## 核心组件

### BaseParser (`base_parser.py`)
所有解析器的抽象基类，定义了解析器的标准接口：

```python
class BaseParser(ABC):
    @abstractmethod
    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs) -> ParseResult:
        """从文件路径或内容字符串解析文档"""
        pass
    
    @abstractmethod
    async def parse_content(self, content: str, source_path: Optional[str] = None, instruction: str = "", **kwargs) -> ParseResult:
        """直接解析文档内容"""
        pass
    
    @property
    @abstractmethod
    def supported_extensions(self) -> List[str]:
        """支持的文件扩展名列表"""
        pass
```

### ParserRegistry (`registry.py`)
解析器注册表，提供自动解析器选择和注册机制：

```python
class ParserRegistry:
    def __init__(self, register_optional: bool = True):
        """初始化注册表并注册默认解析器"""
        self._parsers: Dict[str, BaseParser] = {}
        self._extension_map: Dict[str, str] = {}
        
        # 注册核心解析器
        self.register("text", TextParser())
        self.register("markdown", MarkdownParser())
        self.register("pdf", PDFParser())
        self.register("html", HTMLParser())
        self.register("code", CodeRepositoryParser())
    
    def get_parser_for_file(self, path: Union[str, Path]) -> Optional[BaseParser]:
        """根据文件扩展名获取合适的解析器"""
        pass
    
    def register_custom(self, handler: "CustomParserProtocol", extensions: Optional[List[str]] = None, name: Optional[str] = None):
        """注册自定义解析器（协议方式）"""
        pass
```

### TreeBuilder (`tree_builder.py`)
树构建器，负责将临时目录移动到 AGFS 并入队语义处理：

```python
class TreeBuilder:
    async def finalize_from_temp(
        self,
        temp_dir_path: str,
        scope: str,
        base_uri: Optional[str] = None,
        source_path: Optional[str] = None,
        source_format: Optional[str] = None,
    ) -> "BuildingTree":
        """
        从临时目录最终化树结构：
        1. 移动文件到 AGFS
        2. 入队到 SemanticQueue（自底向上处理）
        3. 扫描创建 Resource 对象
        """
        pass
```

### 资源类型
解析系统使用统一的 Resource 类型。

## 使用示例

### 基本使用

```python
from openviking.parse.parsers.markdown import MarkdownParser
from openviking.parse.registry import get_registry

# 方式1：直接使用解析器
parser = MarkdownParser()
result = await parser.parse("document.md")

# 方式2：通过注册表自动选择
registry = get_registry()
result = await registry.parse("document.pdf")  # 自动选择 PDFParser
```

### 自定义解析器

```python
from openviking.parse.parsers.base_parser import BaseParser
from openviking.parse.base import ParseResult, ResourceNode, NodeType, create_parse_result

class CustomParser(BaseParser):
    @property
    def supported_extensions(self) -> List[str]:
        return [".xyz"]
    
    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs) -> ParseResult:
        content = self._read_file(source)
        root = ResourceNode(type=NodeType.ROOT, title="Custom Document")
        return create_parse_result(root=root, source_path=str(source), source_format="custom")

# 注册自定义解析器
registry = get_registry()
registry.register("custom", CustomParser())
```

### 协议方式扩展

```python
from openviking.parse.custom import CustomParserProtocol
from typing import List

class MyParser:
    @property
    def supported_extensions(self) -> List[str]:
        return [".myformat"]
    
    def can_handle(self, source) -> bool:
        return str(source).endswith(".myformat")
    
    async def parse(self, source, **kwargs):
        # 实现解析逻辑
        pass

# 注册（自动包装为 BaseParser）
registry.register_custom(MyParser(), name="my_parser")
```

## 扩展指南

### 1. 继承 BaseParser（推荐）
适合需要完整控制解析流程的场景：

```python
from openviking.parse.parsers.base_parser import BaseParser

class MyParser(BaseParser):
    @property
    def supported_extensions(self) -> List[str]:
        return [".myformat"]
    
    async def parse(self, source, instruction="", **kwargs):
        # 实现三阶段解析
        # 1. 解析原始内容
        # 2. 生成 L0/L1
        # 3. 写入临时目录
        pass
    
    async def parse_content(self, content, source_path=None, instruction="", **kwargs):
        # 类似实现
        pass
```

### 2. 实现 CustomParserProtocol
适合已有解析逻辑，需要快速集成：

```python
from openviking.parse.custom import CustomParserProtocol

class ExistingParser:
    def parse_file(self, path):
        # 现有解析逻辑
        pass

# 适配器模式
class Adapter(CustomParserProtocol):
    def __init__(self, existing_parser):
        self.parser = existing_parser
    
    @property
    def supported_extensions(self):
        return [".existing"]
    
    def can_handle(self, source):
        return True
    
    async def parse(self, source, **kwargs):
        result = self.parser.parse_file(source)
        # 转换为 ParseResult
        pass
```

### 3. 回调函数方式
适合简单转换场景：

```python
from openviking.parse.registry import ParserRegistry

async def simple_parser(source, **kwargs):
    content = Path(source).read_text()
    # 简单处理
    return create_parse_result(...)

registry = get_registry()
registry.register_callback(".simple", simple_parser, name="simple_parser")
```

## 设计原则

### 1. 解析与语义分离原则
- 解析器完成：解析 + 文件/目录创建（零 LLM 调用）
- 语义生成：异步 SemanticQueue 处理（自底向上）
- TreeBuilder 只负责文件移动和入队
- 减少解析阻塞，降低内存压力

### 2. 异步语义生成
- Parser 输出临时目录后即可返回
- SemanticQueue 后台处理语义信息生成
- 支持并发文件 summary 生成
- 直接向量化写入，不经过 EmbeddingQueue

### 3. 混合目录结构
- 一个目录下可同时包含文件和子目录
- 章节有子章节时，直接内容保存为同名文件
- 灵活的内容组织，符合自然文档结构

### 4. 临时目录架构
- 每个解析器在临时目录中构建完整结构
- TreeBuilder 只负责移动和入队
- 支持并发解析，避免内存冲突

### 5. 统一 Markdown 处理
- 所有文档格式先转成 Markdown
- 统一通过 MarkdownParser 处理结构
- 保证一致的解析质量和输出格式

### 6. 轻量级索引
- Context 对象不存储内容，只存 URI 和元数据
- 内容通过 `get_abstract()` / `get_overview()` / `get_detail()` 按需加载
- 加载后缓存在内存中，避免重复读取

## 存储结构

### 最终 AGFS 结构

混合目录结构示例（章节有子章节时，小章节合并）：

```
viking://resources/Python_异步编程指南/
├── .abstract.md                              # L0: 目录摘要
├── .overview.md                              # L1: 目录概览
├── Python_异步编程指南_第一章_asyncio_基础.md  # 直接内容 + 第一章合并（均 < 800 tokens）
├── 第二章_高级模式/                           # > 4000 tokens 且有子章节，变目录
│   ├── .abstract.md
│   ├── .overview.md
│   ├── 第二章_高级模式_并发控制.md            # 直接内容 + 并发控制合并
│   └── 错误处理.md                           # 单独保存（>= 800 tokens）
└── 附录.md                                   # < 4000 tokens，直接文件
```

超大章节无子章节时的切分：

```
第二章_高级模式/
├── .abstract.md
├── .overview.md
├── 第二章_高级模式_1.md            # 按段落切分
├── 第二章_高级模式_2.md
└── 第二章_高级模式_3.md
```

### 临时目录结构

```
viking://temp/abc123/
└── document/
    ├── Python_异步编程指南.md
    ├── 第一章_asyncio_基础.md
    ├── 第二章_高级模式/
    │   ├── 第二章_高级模式.md
    │   ├── 并发控制.md
    │   └── 错误处理.md
    └── 附录.md
```

注：临时目录只包含文件和目录结构，`.abstract.md` 和 `.overview.md` 由 SemanticProcessor 异步生成。

## 性能优化

### 1. 并发处理
- 支持多个文档同时解析
- 异步 I/O 操作
- 智能资源调度

### 2. 缓存机制
- 解析结果缓存
- 向量化结果复用
- 内容按需加载

### 3. 增量更新
- 支持文档部分更新
- 智能重新解析
- 最小化计算开销

## 故障排除

### 常见问题

1. **解析失败**
   - 检查文件格式是否支持
   - 验证文件完整性
   - 检查依赖库版本

2. **LLM 调用失败**
   - 检查 API 密钥配置
   - 验证网络连接
   - 检查请求配额

3. **内存不足**
   - 启用临时目录模式
   - 减少并发解析数量
   - 优化解析策略

### 调试建议

```python
import logging
logging.basicConfig(level=logging.DEBUG)

# 启用详细日志
parser = MarkdownParser()
result = await parser.parse("test.md", debug=True)
```

## 版本历史

| 版本 | 日期 | 更新内容 |
|------|------|----------|
| v5.0 | 2026-01-19 | 解析与语义分离，引入 SemanticQueue，支持混合目录结构 |
| v4.0 | 2026-01-07 | 引入临时目录架构，解析器自包含设计 |
| v3.0 | 2026-01-07 | 统一 Markdown 处理流程 |
| v2.0 | 2026-01-05 | 增加多媒体解析器支持 |
| v1.0 | 2026-01-04 | 初始解析系统架构 |

## 相关文档

- [OpenViking 整体架构](../../../docs/zh/concepts/01-architecture.md)
- [上下文提取流程](../../../docs//zh/concepts/07-extraction.md)
- [存储系统设计](../../../docs/zh/concepts/05-storage.md)
- [配置指南](../../../docs/zh/configuration/configuration.md)
