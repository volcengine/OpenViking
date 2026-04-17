# OpenViking 解析器两层架构重构

| 项目 | 信息 |
|-----|------|
| 状态 | `已完成` |
| 创建日期 | 2026-04-13 |
| 完成日期 | 2026-04-14 |

---

## 概述

将原有的单一层 Parser 架构拆分为 **Accessor（数据访问层）** 和 **Parser（数据解析层）** 两层，实现职责分离和代码复用。

---

## 目录

- [背景与问题](#背景与问题)
- [架构设计](#架构设计)
- [核心抽象](#核心抽象)
- [实现进度](#实现进度)
- [文件结构](#文件结构)

---

## 背景与问题

### 当前架构的问题

| 问题 | 说明 |
|-----|------|
| 平铺式注册 | 所有 Parser 在同一层级，职责不清晰 |
| 后缀冲突 | `.zip` 可被 `CodeRepositoryParser` 和 `ZipParser` 同时处理 |
| URL 处理逻辑分散 | `UnifiedResourceProcessor._process_url()` 和 `ParserRegistry.parse()` 都有 URL 检测 |
| 职责混合 | 部分 Parser 既负责下载又负责解析 |

### 重构目标

1. **职责分离**：数据获取 ≠ 数据解析
2. **代码复用**：HTTP/Git 下载逻辑可被多个 Parser 复用
3. **易于扩展**：新增数据源只需添加 Accessor
4. **解决冲突**：通过优先级机制解决 `.zip` 等后缀冲突

---

## 架构设计

### 两层架构

| 层级 | 抽象接口 | 职责 | 示例 |
|-----|---------|------|------|
| **L1: Accessor** | `DataAccessor` | 获取数据：远程 URL / 特殊路径 → 本地文件/目录 | `GitAccessor`, `HTTPAccessor`, `FeishuAccessor` |
| **L2: Parser** | `BaseParser` | 解析数据：本地文件/目录 → `ParseResult` | `MarkdownParser`, `PDFParser`, `ZipParser` |

### 调用流程

```
add_resource(path)
    ↓
ResourceProcessor.process_resource()
    ↓
UnifiedResourceProcessor.process()
    ↓
┌─────────────────────────────────────────┐
│  第一阶段：数据访问 (Accessor Layer)     │
├─────────────────────────────────────────┤
│  AccessorRegistry.route(source)         │
│    ├─→ GitAccessor    (priority: 80)   │
│    ├─→ FeishuAccessor (priority: 100)  │
│    ├─→ HTTPAccessor   (priority: 50)   │
│    └─→ LocalAccessor  (priority: 10)   │
│         ↓                                │
│  返回: LocalResource                     │
│         (本地路径 + 元数据)              │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│  第二阶段：数据解析 (Parser Layer)       │
├─────────────────────────────────────────┤
│  ParserRegistry.route(local_resource)   │
│    ├─→ 是目录? → DirectoryParser        │
│    ├─→ 是文件? → 按扩展名匹配           │
│    │     ├─→ .md  → MarkdownParser     │
│    │     ├─→ .pdf → PDFParser          │
│    │     ├─→ .zip → ZipParser          │
│    │     └─→ ...                        │
│    └─→ 返回: ParseResult                │
└─────────────────────────────────────────┘
                    ↓
TreeBuilder + SemanticQueue (保持不变)
```

---

## 核心抽象

### 1. LocalResource（数据类）

位置：`openviking/parse/accessors/base.py`

表示一个可在本地访问的资源，是 Accessor 层的输出。

```python
@dataclass
class LocalResource:
    path: Path                    # 本地文件/目录路径
    source_type: str              # 原始来源类型 (SourceType.GIT/HTTP/FEISHU/LOCAL)
    original_source: str           # 原始 source 字符串
    meta: Dict[str, Any]          # 元数据（repo_name, branch, content_type 等）
    is_temporary: bool = True      # 是否为临时文件，解析后可清理

    def cleanup(self) -> None     # 清理临时资源
    def __enter__/__exit__         # 支持上下文管理器
```

### 2. DataAccessor（抽象基类）

位置：`openviking/parse/accessors/base.py`

```python
class DataAccessor(ABC):
    @abstractmethod
    def can_handle(self, source: Union[str, Path]) -> bool
        """判断是否能处理该来源"""

    @abstractmethod
    async def access(self, source: Union[str, Path], **kwargs) -> LocalResource
        """获取数据到本地，返回 LocalResource"""

    @property
    @abstractmethod
    def priority(self) -> int
        """优先级：数字越大优先级越高
           - 100: 特定服务 (Feishu)
           - 80: 版本控制 (Git)
           - 50: 通用协议 (HTTP)
           - 10: 兜底 (Local)
        """

    def cleanup(self, resource: LocalResource) -> None
        """清理资源（默认调用 resource.cleanup()）"""
```

### 3. AccessorRegistry

位置：`openviking/parse/accessors/registry.py`

```python
class AccessorRegistry:
    def __init__(self, register_default: bool = True)
        """初始化注册表，可选是否注册默认 Accessor"""

    def register(self, accessor: DataAccessor) -> None
        """注册 Accessor（按优先级降序插入）"""

    def unregister(self, accessor_name: str) -> bool
        """注销 Accessor"""

    def get_accessor(self, source) -> Optional[DataAccessor]
        """获取能处理该 source 的最高优先级 Accessor"""

    async def access(self, source, **kwargs) -> LocalResource
        """路由到合适的 Accessor 获取数据"""
```

默认注册的 Accessor（按优先级）：
1. `FeishuAccessor` (100) - 处理飞书/ Lark 文档
2. `GitAccessor` (80) - 处理 Git 仓库
3. `HTTPAccessor` (50) - 处理 HTTP/HTTPS URL
4. `LocalAccessor` (10) - 处理本地文件（兜底）

---

## 实现进度

### ✅ 已完成

- [x] 创建 `openviking/parse/accessors/` 目录结构
- [x] 实现 `DataAccessor` 抽象基类和 `LocalResource` 数据类
- [x] 实现 `AccessorRegistry`（含优先级机制）
- [x] 实现 `GitAccessor` - 处理 Git 仓库
- [x] 实现 `HTTPAccessor` - 处理 HTTP URL
- [x] 实现 `FeishuAccessor` - 处理飞书文档
- [x] 实现 `LocalAccessor` - 处理本地文件
- [x] 全局注册表 `get_accessor_registry()`
- [x] 更新 `PDFParser`、`resources.py`、`local_input_guard.py` 使用新架构

---

## 文件结构

```
openviking/parse/
├── accessors/                    # ✅ 新增：数据访问层
│   ├── __init__.py
│   ├── base.py                  # DataAccessor, LocalResource, SourceType
│   ├── registry.py              # AccessorRegistry
│   ├── git_accessor.py          # GitAccessor
│   ├── http_accessor.py         # HTTPAccessor
│   ├── feishu_accessor.py       # FeishuAccessor
│   └── local_accessor.py        # LocalAccessor
├── parsers/                      # 数据解析层（保持不变）
│   ├── base_parser.py
│   ├── markdown.py
│   ├── pdf.py
│   ├── zip_parser.py
│   └── ...
└── registry.py                   # ParserRegistry（保持不变）
```

---

## 使用示例

### 使用 AccessorRegistry

```python
from openviking.parse.accessors import get_accessor_registry

# 获取全局注册表
registry = get_accessor_registry()

# 访问资源（自动路由）
async with await registry.access("https://github.com/user/repo") as resource:
    print(f"本地路径: {resource.path}")
    print(f"来源类型: {resource.source_type}")
    # 使用 resource.path 进行解析...
    # 退出 with 块时自动清理临时资源
```

### 自定义 Accessor

```python
from openviking.parse.accessors import DataAccessor, LocalResource

class MyAccessor(DataAccessor):
    @property
    def priority(self) -> int:
        return 90

    def can_handle(self, source: str) -> bool:
        return source.startswith("myprotocol://")

    async def access(self, source: str, **kwargs) -> LocalResource:
        # 获取数据到本地...
        temp_path = self._create_temp_dir()
        # ... 下载逻辑 ...
        return LocalResource(
            path=temp_path,
            source_type="myprotocol",
            original_source=source,
            meta={},
            is_temporary=True
        )

# 注册
registry = get_accessor_registry()
registry.register(MyAccessor())
```

---

## 相关文档

- [解析系统 README](../../openviking/parse/parsers/README.md)
- [OpenViking 整体架构](../zh/concepts/01-architecture.md)
