# Code Tools MCP 集成设计文档

**日期**：2026-05-20
**范围**：向 OpenViking 新增 `code_outline`、`code_search`、`code_expand` 三个 MCP 工具

---

## 概述

三个新 MCP 工具通过 OpenViking 现有的 `@mcp.tool()` 端点向 AI Agent 暴露代码结构导航能力。典型使用顺序：

```
code_outline  →  查看文件符号结构
code_search   →  跨目录查找符号位置
code_expand   →  展开完整实现代码
```

**仅支持 `viking://` URI**（不支持直接本地路径），与现有 `read`、`grep`、`list` 等工具保持一致，符合服务端既有安全边界（参见 `local_input_guard.py`：HTTP MCP 端拒绝直接的本地文件系统路径）。需要分析本地代码时，先用 `add_resource` 入库为 `viking://` 资源即可。

---

## 架构

```
mcp_endpoint.py
  ├── code_outline(uri)            →  openviking.parse.parsers.code.ast
  ├── code_search(query, uri)      →  openviking.parse.parsers.code.ast
  └── code_expand(uri, symbol)     →  openviking.parse.parsers.code.ast

openviking/parse/parsers/code/ast/
  ├── skeleton.py        （修改：给 FunctionSig、ClassSkeleton 加行号字段）
  ├── extractor.py       （修改：新增 extract() 返回 CodeSkeleton，extract_skeleton 复用之）
  ├── code_tools.py      （新增：outline_file、search_symbols、expand_symbol）
  └── languages/
      ├── python.py      （修改：读取 node.start_point / end_point）
      ├── js_ts.py       （修改）
      ├── go.py          （修改）
      ├── rust.py        （修改）
      ├── java.py        （修改）
      ├── cpp.py         （修改：3 处函数提取路径都要改）
      └── ...
```

无新增依赖，使用已安装的 `tree-sitter` Python 绑定。

---

## 各组件改动说明

### 1. `skeleton.py` — 新增行号字段

给 `FunctionSig` 和 `ClassSkeleton` 加 `line_start`、`line_end`：

```python
@dataclass
class FunctionSig:
    name: str
    params: str
    return_type: str
    docstring: str
    line_start: int = 0   # 1-indexed，起始行（含）
    line_end: int = 0     # 1-indexed，结束行（含）

@dataclass
class ClassSkeleton:
    name: str
    bases: List[str]
    docstring: str
    methods: List[FunctionSig] = field(default_factory=list)
    line_start: int = 0
    line_end: int = 0
```

默认值 `0` 保持向后兼容，现有调用方（`extract_skeleton`、嵌入流水线）无需改动。

`to_text()` **不改**——它服务于 embedding/LLM 输入场景，行号会污染那条路径。outline 的展示由 `code_tools.outline_file()` 独立实现。

### 2. `extractor.py` — 新增 `extract()` 返回 `CodeSkeleton`

现有 `ASTExtractor.extract_skeleton()` 返回的是格式化文本字符串，不是 `CodeSkeleton` 对象。代码工具需要原始结构，因此在 `ASTExtractor` 上新增公开方法：

```python
def extract(self, file_name: str, content: str) -> Optional[CodeSkeleton]:
    """Return raw CodeSkeleton or None for unsupported/failed extraction."""
    lang = self._detect_language(file_name)
    extractor = self._get_extractor(lang)
    if extractor is None:
        return None
    try:
        return extractor.extract(file_name, content)
    except Exception as e:
        logger.warning(
            "AST extraction failed for '%s' (language: %s): %s", file_name, lang, e
        )
        return None
```

`extract_skeleton()` 重构为先调 `extract()`、再 `.to_text(verbose)`，逻辑完全等价。

### 3. 各语言提取器 — 读取节点行号

每个 `_extract_function` / `_extract_class` / `_extract_struct` 在构造 `FunctionSig` / `ClassSkeleton` 时多传两个参数：

```python
# tree-sitter node.start_point = (row, col)，0-indexed
line_start = node.start_point[0] + 1
line_end   = node.end_point[0] + 1
```

**特别注意 `cpp.py`**：该文件有三处函数构造路径——`_extract_function_declarator`、`_extract_function`、`_extract_function_proto`——三处都要传行号。其他语言每个文件改 2 处（函数 + 类/struct）即可。

### 4. `code_tools.py` — 新增模块

新建 `openviking/parse/parsers/code/ast/code_tools.py`，三个纯函数，无 I/O 无异步，输入字符串、返回格式化字符串。I/O 与逻辑分离便于独立测试。

```python
def outline_file(content: str, file_name: str) -> str:
    """返回源文件的符号结构（含行号、总行数）。
    内部：ASTExtractor.extract() -> CodeSkeleton -> 专用 outline 格式器。"""

def search_symbols(query: str, files: list[tuple[str, str]]) -> str:
    """在多个 (content, file_name) 中搜索符号名包含 query 的符号
    （大小写不敏感子串匹配）。"""

def expand_symbol(content: str, file_name: str, symbol: str) -> str:
    """返回符号完整源码。symbol 支持两种形式：
       - 'foo'         匹配任意位置同名函数/类/方法
       - 'Foo.bar'     精确匹配 Foo 类下的 bar 方法
    同名多个返回第一个；区分大小写。"""
```

### 5. `mcp_endpoint.py` — 新增三个工具

```python
@mcp.tool()
async def code_outline(uri: str) -> str:
    """展示源文件的符号结构——类、函数、方法及其行号范围。
    uri 必须是 viking:// URI。"""

@mcp.tool()
async def code_search(query: str, uri: str) -> str:
    """在 viking:// 目录下按名称搜索符号。query 对符号名做大小写不敏感
    子串匹配，返回匹配符号及其文件位置和行号。最多扫描 200 个文件。
    uri 必填——不提供默认值以避免误扫整个 VikingFS。"""

@mcp.tool()
async def code_expand(uri: str, symbol: str) -> str:
    """返回源文件中指定符号（函数、类或方法）的完整源码。
    symbol 支持 'bar' 和 'Foo.bar' 两种形式。"""
```

---

## URI 解析

仅处理 `viking://` URI，通过 `service.fs` 调用：

```
viking://resources/owner/repo/src/auth.py
  → service.fs.read(uri, ctx=ctx)                        # 文件内容

viking://resources/owner/repo/src/
  → service.fs.ls(uri, ctx=ctx, recursive=True, output="original")
    返回 dict 列表，字段：name、isDir、uri（camelCase）
```

辅助函数 `_resolve_code_dir(uri, service, ctx)` 完成 ls + 按扩展名过滤 + 并发读取。

支持的扩展名（与 `extractor._EXT_MAP` 对齐）：
`.py .js .jsx .ts .tsx .java .c .cpp .cc .h .hpp .rs .go .cs .php .lua`

---

## 数据流

### `code_outline(uri)`

```
uri
 → service.fs.read()              → content
 → ASTExtractor.extract()         → CodeSkeleton（或 None）
 → outline_file()                 → 格式化字符串
```

None 时直接返回 `"不支持的语言：{file_name}"` 或 `"解析 {file_name} 失败"`。

### `code_search(query, uri)`

```
uri（目录）
 → service.fs.ls(recursive=True, output="original")
 → 按扩展名过滤，截断到 200 个                    → list[uri]
 → asyncio.gather + Semaphore(10) 并发 service.fs.read()
                                                  → list[(content, file_name)]
 → 逐文件 ASTExtractor.extract()                 → list[CodeSkeleton]
 → search_symbols(query, ...)                    → 格式化字符串
```

并发模式参考已有 `read` 工具（`mcp_endpoint.py:290`）。解析失败的文件跳过、记录 warning，不中断整体搜索。

### `code_expand(uri, symbol)`

```
uri
 → service.fs.read()              → content
 → ASTExtractor.extract()         → CodeSkeleton
 → expand_symbol()                → content 的 [line_start-1 : line_end] 行 + 位置标注
```

---

## 输出格式

### `code_outline`

`outline_file()` 不复用 `CodeSkeleton.to_text()`（那是嵌入流水线格式），自带格式器：

```
auth.py  [Python, 120 lines]

imports: os, typing.Optional, fastapi.HTTPException

class AuthHandler  L18-62
  + __init__(self, secret: str)  L20-25
  + authenticate(self, token: str) -> Optional[User]  L27-45
  + verify_scope(self, user: User, scope: str) -> bool  L47-60

def get_handler() -> AuthHandler  L65-70
```

- 头部行的语言名直接用 `CodeSkeleton.language`（Python/JavaScript/TypeScript 等，保留 `extractor` 已有 casing）
- 总行数由 `outline_file()` 内 `content.count("\n") + 1` 计算
- 不输出 docstring，行号是导航主信息

### `code_search`

```
2 matches for "authenticate" (scanned 47 files)

src/auth.py
  AuthHandler.authenticate  L27-45
  authenticate_request  L75-88
```

如果到达 200 文件上限，输出末尾追加 `(scanning stopped at 200-file cap; narrow uri to search more)`。

### `code_expand`

```
# src/auth.py  L27-45

def authenticate(self, token: str) -> Optional[User]:
    payload = jwt.decode(token, self.secret)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401)
    return db.get(User, user_id)
```

---

## 错误处理

| 场景 | 工具 | 返回内容 |
|------|------|---------|
| 不支持的语言（扩展名不在 `_EXT_MAP`） | outline / expand | `"不支持的语言：{file_name}"` |
| 不支持的语言 | search | 静默跳过该文件 |
| 解析失败 | outline / expand | `"解析 {file_name} 失败：{reason}"`（明确错误，**不** "继续"） |
| 解析失败 | search | 记录 warning，跳过该文件，继续 |
| 符号未找到 | expand | `"在 {file_name} 中未找到符号 '{symbol}'"` |
| URI 不存在 | 全部 | 透传 `AGFSNotFoundError` 或 `FileNotFoundError` |
| 目录为空 / 无可解析文件 | search | `"在 {uri} 中未找到支持的源文件"` |
| 传入非 viking:// URI | 全部 | `"仅支持 viking:// URI；本地路径请先 add_resource"` |

---

## 涉及文件清单

| 文件 | 改动类型 |
|------|---------|
| `openviking/parse/parsers/code/ast/skeleton.py` | 新增 `line_start`、`line_end` 字段（默认 0） |
| `openviking/parse/parsers/code/ast/extractor.py` | 新增 `extract()` 返回 `CodeSkeleton`；`extract_skeleton` 重构复用之 |
| `openviking/parse/parsers/code/ast/languages/python.py` | 读取节点行号（2 处） |
| `openviking/parse/parsers/code/ast/languages/js_ts.py` | 读取节点行号（2 处） |
| `openviking/parse/parsers/code/ast/languages/go.py` | 读取节点行号（2 处：function、struct） |
| `openviking/parse/parsers/code/ast/languages/rust.py` | 读取节点行号（2 处） |
| `openviking/parse/parsers/code/ast/languages/java.py` | 读取节点行号（2 处） |
| `openviking/parse/parsers/code/ast/languages/cpp.py` | 读取节点行号（**3 处**：declarator、function、proto） |
| `openviking/parse/parsers/code/ast/languages/csharp.py` | 读取节点行号（2 处） |
| `openviking/parse/parsers/code/ast/languages/php.py` | 读取节点行号（2 处） |
| `openviking/parse/parsers/code/ast/languages/lua.py` | 读取节点行号（2 处） |
| `openviking/parse/parsers/code/ast/code_tools.py` | **新增文件** |
| `openviking/server/mcp_endpoint.py` | 新增 3 个工具 + viking:// 校验辅助 |
