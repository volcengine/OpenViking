# codelocate 结构化定位与本地代码源设计实现

日期：2026-06-25
状态：已实现

## 背景

`codelocate` 的目标是在 bug 修复这类代码修改任务中，以低 token、确定性、可解释的方式给 coding agent 提供“先定位再修复”的入口。它不调用模型做 rerank，而是基于 query 文本、文件路径、符号、内容片段、测试提示和诊断信息做启发式 ranking。

本次实现把早期 `codesearch`/`codeoutline`/`codeexpand` 的源码浏览能力扩展为一个更适合 agent harness 的定位工具：

- 输出支持 `text`、`json`、`both`，其中 JSON 使用稳定 schema。
- source 支持 `viking` 和显式开启后的 `local`，local 模式读取当前 checkout。
- ranking 输出区分可编辑候选和行为参考，避免测试文件挤占实现文件。
- 对诊断 wording 类 issue 增加 PATCH FIRST staged action，引导 agent 先改最小生产诊断点。
- `code_search` 与 `code_locate` 的源码扫描上限统一为 1000 个文件。

## 入口与配置

### HTTP API

HTTP 入口是 `POST /api/v1/code/locate`，请求模型在 `openviking/server/routers/code.py` 中定义：

```python
class CodeLocateSource(BaseModel):
    type: Literal["local", "viking"]
    path: str | None = None
    uri: str | None = None


class CodeLocateHintInput(BaseModel):
    paths: list[str] = Field(default_factory=list)
    path_terms: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    imports: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class CodeLocateRequest(BaseModel):
    source: CodeLocateSource
    query: str
    terms: list[str] = Field(default_factory=list)
    hints: CodeLocateHintInput = Field(default_factory=CodeLocateHintInput)
    failing_tests: list[str] = Field(default_factory=list)
    output_format: Literal["text", "json", "both"] = "text"
    debug: bool = False
    max_edit: int = 5
    max_references: int = 3
```

规则：

- `source.type == "viking"` 时必须传 `source.uri`，且不能传 `source.path`。
- `source.type == "local"` 时必须传 `source.path`，且不能传 `source.uri`。
- `query` 不能为空。

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `source` | object | 是 | 代码来源描述。决定工具从 OpenViking 资源还是本地 checkout 读取代码，也决定输出 location 使用 `uri` 还是 `path`。 |
| `source.type` | `"local"` / `"viking"` | 是 | 代码来源类型。`local` 读取服务端本地路径；`viking` 读取 OpenViking 中的资源 URI。 |
| `source.path` | string / null | local 必填 | 本地源码文件或目录的绝对路径。仅 `source.type=local` 可用。 |
| `source.uri` | string / null | viking 必填 | OpenViking 资源 URI。仅 `source.type=viking` 可用。 |
| `query` | string | 是 | agent 传入的问题描述或定位查询。ranking 会从中抽取路径、符号、错误信息、诊断词和普通关键词。 |
| `terms` | list[string] | 否 | agent 或 harness 已结构化抽取出的关键词。它们会补充 query ranking，并参与 scan cap 前的候选选择。 |
| `hints` | object | 否 | agent 或 harness 提供的结构化定位提示。所有子字段均可省略。 |
| `hints.paths` | list[string] | 否 | issue 中明确出现的文件路径或文件名。属于强路径信号。 |
| `hints.path_terms` | list[string] | 否 | issue 暗示的目录、模块或功能区域。属于弱路径信号，不应猜测不存在的具体路径。 |
| `hints.symbols` | list[string] | 否 | issue 明确出现的类、函数、方法、变量或配置名。 |
| `hints.imports` | list[string] | 否 | issue 明确出现的包名或模块名。 |
| `hints.errors` | list[string] | 否 | issue 明确出现的 warning、error、exception 或 traceback 文本。 |
| `failing_tests` | list[string] | 否 | 已知失败测试名称、node id 或相关测试路径。它只作为定位、排序和验证建议的提示，不会由 `codelocate` 自动执行。 |
| `output_format` | `"text"` / `"json"` / `"both"` | 否 | 输出格式。`text` 面向直接展示；`json` 面向插件和 agent harness 解析；`both` 返回 JSON 并补充 `summary_text`。 |
| `debug` | bool | 否 | 是否返回调试信息。默认关闭，避免 agent 默认上下文变长。 |
| `max_edit` | int | 否 | 最多返回多少个可编辑候选。用于控制输出 token 和候选噪声。 |
| `max_references` | int | 否 | 最多返回多少个行为参考候选，通常是测试或断言文件。 |

### MCP 工具

MCP 入口是 `openviking/server/mcp_endpoint.py` 中的 `code_locate`：

```python
async def code_locate(
    query: str,
    source: dict,
    failing_tests: Optional[list[str]] = None,
    terms: Optional[list[str]] = None,
    hints: Optional[CodeLocateHintInput] = None,
    output_format: str = "text",
    debug: bool = False,
    max_edit: int = 5,
    max_references: int = 3,
) -> str:
```

MCP 与 HTTP 共享同一套纯 ranking/formatting 实现，仅 I/O 层不同。MCP 的 `failing_tests` 保持第三个位置参数以兼容旧的直接 Python 调用；`terms` 和 `hints` 是后续追加的可选结构化输入。实际 MCP JSON payload 中的 `hints` 可能以普通 object 进入服务端，入口会先规范化为 `CodeLocateHintInput` 再参与 ranking。

### 本地路径安全开关

本地代码源会读取服务端所在机器的文件系统，因此默认关闭。开关位于 `openviking/server/config.py`：

```python
class ServerConfig(BaseModel):
    allow_local_code_source_paths: bool = False
```

当 `allow_local_code_source_paths == False` 时：

- HTTP local source 返回结构化 warning，code 为 `local_source_disabled`。
- MCP local source 返回错误文本 `Error: local code source paths are disabled...`。

只有明确设置 `server.allow_local_code_source_paths=true` 后，HTTP/MCP local source 才会递归读取本地源码。这个开关用于本机 agent harness 或可信单用户调测场景，不应在不受信任的远程多用户服务上默认开启。

## Source 语义

### Viking source

Viking source 通过 `service.fs.ls(..., recursive=True, output="original")` 枚举文件，再通过 `service.fs.read` 读取源码。

输出 location 只包含 viking URI：

```json
{
  "type": "viking",
  "uri": "viking://resources/owner/repo/samplepkg/utils/pretty.py",
  "relative_path": "samplepkg/utils/pretty.py"
}
```

Viking source 没有本地 checkout 映射时，验证建议不会生成可执行本地命令。若有相关测试 reference，可以返回 `command: null` 的 `narrow_tests` target 记录；同时返回 setup note 说明需要先建立本地 checkout 映射。

### Local source

Local source 读取当前 checkout 文件，解决 OpenViking 入库快照与 agent 已修改工作区不同步的问题。

规则：

- `path` 可以是源码文件或目录。
- 文件 source 只读取该文件，父目录作为 `source.root` 和验证命令 `cwd`。
- 目录 source 递归扫描支持的源码文件。
- 跳过重型或生成目录：`.git`、`.tox`、`.venv`、`venv`、`node_modules`、`__pycache__`、`build`、`dist`。
- 读取时优先 UTF-8；遇到 decode 问题时用 replacement 兜底。
- 读取失败会计入 `skipped_unreadable_files` warning。

输出 location 只包含本地路径：

```json
{
  "type": "local",
  "path": "/abs/repo/path/samplepkg/utils/pretty.py",
  "relative_path": "samplepkg/utils/pretty.py"
}
```

输入 source 类型决定输出 location 类型。同一个候选项不会同时出现 `path` 和 `uri`，避免 agent 混淆应该读写哪一种路径。

## 扫描上限

源码扫描统一使用：

```python
CODE_SEARCH_FILE_CAP = 1000
CODE_LOCATE_FILE_CAP = 1000
```

`select_code_uris` 和 `select_code_paths` 都支持显式 `cap` 参数。当前 `code_search`、`code_locate`、local locate 和调测用 stub 都使用 1000 文件语义。

超过 cap 时会先按 query 路径相关度排序，再裁剪；如果已选实现文件存在相关测试文件，`_keep_related_tests_under_cap` 会尝试保留对应测试文件。

## Ranking 设计

核心纯逻辑在 `openviking/parse/parsers/code/ast/code_tools.py`：

```python
def locate_code_structured(
    query: str,
    files: list[CodeLocateFile],
    failing_tests: list[str] | None = None,
    *,
    terms: list[str] | None = None,
    hints: CodeLocateHints | None = None,
    max_edit: int = CODE_LOCATE_EDIT_LIMIT,
    max_references: int = CODE_LOCATE_REFERENCE_LIMIT,
    debug: bool = False,
    source_root: str | None = None,
    allow_viking_commands: bool = False,
) -> CodeLocateResult:
```

I/O 层只负责把 viking/local 文件转换为 `CodeLocateFile`，ranking 层不读文件系统、不访问网络、不调用模型。

主要 ranking 信号：

- query terms：从 issue/query 文本抽取普通词、标识符、下划线拆分词。
- structured terms：调用方传入的 `terms` 会补充 query terms，也会参与扫描 cap 前的候选排序。
- structured hints：调用方传入的 `hints.paths/path_terms/symbols/imports/errors` 分别影响路径、符号、import 和诊断文本 ranking。
- exact identifiers：对函数名、配置名、错误码等精确标识符加权。
- path relevance：文件路径、basename、测试路径与 query 的重合度。
- symbol relevance：类、函数、方法名与 query/failing tests 的重合度。
- content relevance：源码行中多 query term 聚合、相邻命中、精确短语。
- failing test hints：失败测试 node id 或测试名与文件/符号的关联。
- diagnostic signals：warning/error/exception/traceback 类 query 的诊断发射点和断言。
- related tests：优先实现文件对应的 test 文件作为行为参考。

输出分为两个列表：

- `edit_candidates`：非测试文件，优先作为修改入口。
- `behavior_references`：测试文件或断言文件，作为行为证据和验证参考。

## JSON 输出契约

顶层 schema：

```json
{
  "schema_version": "code-locate/v1",
  "source": {
    "type": "local",
    "root": "/abs/repo/path"
  },
  "query": {
    "text": "Bug in compact_repr repr for vector-valued estimator params",
    "terms": [
      "compact_repr",
      "repr"
    ],
    "hints": {
      "paths": ["samplepkg/utils/pretty.py"],
      "path_terms": ["utils"],
      "symbols": ["_changed_params"],
      "imports": ["numpy"],
      "errors": []
    },
    "failing_tests": [
      "samplepkg/utils/tests/test_pretty.py::test_changed_only"
    ]
  },
  "edit_candidates": [],
  "behavior_references": [],
  "verification": [],
  "warnings": [],
  "summary_text": "Top edit candidate: ..."
}
```

顶层字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `schema_version` | string | JSON 契约版本。当前固定为 `code-locate/v1`，用于插件或 harness 做兼容判断。 |
| `source` | object | 实际使用的代码来源摘要。 |
| `source.type` | `"local"` / `"viking"` | 实际代码来源类型。 |
| `source.root` | string / null | 扫描根路径或资源根。local 通常是本地 checkout 根目录；viking 通常是资源 URI 根。 |
| `query` | object | 本次定位输入的查询摘要。 |
| `query.text` | string | 原始 `query` 文本。 |
| `query.terms` | list[string] | 规范化后的结构化关键词，最多保留 30 个非空去重项。 |
| `query.hints` | object | 规范化后的结构化定位提示。即使调用方未传 hints，也会以空列表对象回显，便于 harness 稳定解析。 |
| `query.hints.paths` | list[string] | 明确路径或文件名提示，最多保留 10 个非空去重项。 |
| `query.hints.path_terms` | list[string] | 弱路径概念提示，最多保留 20 个非空去重项。 |
| `query.hints.symbols` | list[string] | 符号名提示，最多保留 20 个非空去重项。 |
| `query.hints.imports` | list[string] | 包或模块名提示，最多保留 10 个非空去重项。 |
| `query.hints.errors` | list[string] | 诊断文本提示，最多保留 5 个非空去重项。 |
| `query.failing_tests` | list[string] | 原始 `failing_tests` 列表，用于解释 ranking 和验证建议来源。 |
| `edit_candidates` | list[object] | 建议优先检查或修改的生产代码候选。测试文件一般不会进入这里。 |
| `behavior_references` | list[object] | 用作行为证据的参考文件，通常是测试、断言或复现相关文件。 |
| `verification` | list[object] | 面向 agent 的最小验证建议，尽量先做静态检查或窄测试。 |
| `warnings` | list[object] | 结构化告警，例如扫描被 cap 截断、本地路径未开启、无可读源码等。 |
| `summary_text` | string / null | 文本摘要。`output_format=both` 时用于保留人类可读摘要；纯 JSON 场景可为空或省略。 |
| `debug` | object | 调试信息。仅 `debug=true` 返回。 |

候选项 schema：

```json
{
  "rank": 1,
  "location": {
    "type": "local",
    "path": "/abs/repo/path/samplepkg/utils/pretty.py",
    "relative_path": "samplepkg/utils/pretty.py"
  },
  "score": 142,
  "imports": ["numpy"],
  "focus_symbols": [
    {
      "name": "_changed_params",
      "kind": "symbol",
      "range": {
        "start_line": 42,
        "end_line": 80
      }
    }
  ],
  "symbols": [],
  "reasons": [
    "exact identifiers: compact_repr",
    "content matches: repr, vector"
  ],
  "snippets": [
    {
      "line": 57,
      "text": "..."
    }
  ],
  "next_action": "inspect current checkout lines; no web/upstream/git history"
}
```

候选项字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `rank` | int | 候选在当前列表中的排序，从 1 开始。 |
| `location` | object | 候选文件位置。输入 source 类型决定输出 location 类型。 |
| `location.type` | `"local"` / `"viking"` | 候选位置类型。 |
| `location.path` | string | local source 下的本地绝对路径。viking source 不返回该字段。 |
| `location.uri` | string | viking source 下的资源 URI。local source 不返回该字段。 |
| `location.relative_path` | string | 相对 `source.root` 的路径，用于显示、排序解释和验证 target。 |
| `score` | int | 启发式 ranking 分数。只用于同一结果内相对比较，不承诺跨请求可比。 |
| `imports` | list[string] | 从候选文件中轻量抽取的 import 依赖摘要，帮助 agent 判断上下文和相关模块；它不是修改目标列表。 |
| `focus_symbols` | list[object] | ranking 认为最值得优先查看的类、函数或方法。 |
| `symbols` | list[object] | 文件中抽取到的其他符号摘要。用于补充上下文，优先级低于 `focus_symbols`。 |
| `focus_symbols[].name` / `symbols[].name` | string | 符号名称。 |
| `focus_symbols[].kind` / `symbols[].kind` | string | 符号类型，例如 `class`、`function`、`method`、`symbol`。 |
| `focus_symbols[].range` / `symbols[].range` | object | 符号所在行号范围。 |
| `focus_symbols[].range.start_line` / `symbols[].range.start_line` | int | 起始行号，1-based。 |
| `focus_symbols[].range.end_line` / `symbols[].range.end_line` | int | 结束行号，1-based。 |
| `reasons` | list[string] | 候选入选和得分的主要原因，例如精确标识符命中、路径命中、内容命中、诊断信号命中。 |
| `snippets` | list[object] | 少量命中代码行片段，帮助 agent 判断是否需要打开文件。 |
| `snippets.line` | int | 片段所在行号，1-based。 |
| `snippets.text` | string | 片段文本，通常会做长度裁剪。 |
| `next_action` | string | 建议 agent 下一步执行的动作，例如优先查看当前 checkout 行、先做最小 patch、避免查外部历史等。 |

`debug=false` 时不返回 `debug` 字段。`debug=true` 时返回：

- scan 统计：source type、root、candidate/read/failed counts、skipped dirs、capped。
- query terms。
- structured `terms` 和 `hints`。
- exact identifiers。
- ranking signals。

Debug 用于调试和评估排序效果，不作为 opencode 默认输出。

## 验证建议

`verification` 是 agent-facing 的最小验证建议。

Local source 可以生成命令：

```json
{
  "kind": "static",
  "command": "python3 -m py_compile samplepkg/utils/pretty.py",
  "cwd": "/abs/repo/path",
  "targets": [
    {
      "type": "local",
      "path": "/abs/repo/path/samplepkg/utils/pretty.py",
      "relative_path": "samplepkg/utils/pretty.py"
    }
  ],
  "reason": "top Python edit candidate"
}
```

如果存在测试 reference 且不是诊断 wording delta staged action，会补充窄测试建议：

```json
{
  "kind": "narrow_tests",
  "command": "python3 -m pytest samplepkg/utils/tests/test_pretty.py",
  "cwd": "/abs/repo/path",
  "targets": [],
  "reason": "top related behavior reference"
}
```

Viking source 默认不生成本地命令；有测试 reference 时可以先给出不可直接执行的 target 记录：

```json
{
  "kind": "narrow_tests",
  "command": null,
  "cwd": null,
  "targets": [
    {
      "type": "viking",
      "uri": "viking://resources/owner/repo/samplepkg/utils/tests/test_pretty.py",
      "relative_path": "samplepkg/utils/tests/test_pretty.py"
    }
  ],
  "reason": "top related behavior reference"
}
```

并补充 setup note：

```json
{
  "kind": "setup_note",
  "command": null,
  "cwd": null,
  "targets": [],
  "reason": "viking source has no local checkout mapping"
}
```

Python 命令统一使用 `python3 -m ...`，比 `python -m ...` 更适合常见 Linux agent harness；命令中的路径会按 shell 参数规则转义，避免空格或特殊字符破坏命令。

`verification` 字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `kind` | string | 验证建议类型。常见值包括 `static`、`narrow_tests`、`setup_note`。 |
| `command` | string / null | 建议执行的命令。viking source 没有本地 checkout 映射时可以为 null。 |
| `cwd` | string / null | 建议执行命令的工作目录。local source 通常是扫描根目录。 |
| `targets` | list[object] | 该验证建议覆盖的候选文件或参考文件，结构与 `location` 相同。 |
| `reason` | string | 生成该验证建议的原因，例如 top edit candidate 或 top related behavior reference。 |

## Warning code

当前 warning 使用结构化对象：

```json
{
  "code": "scan_capped",
  "message": "Scanning stopped at 1000-file cap; narrow source path to search more."
}
```

已使用的 code：

- `empty_query`
- `invalid_source`
- `local_source_disabled`
- `no_supported_source_files`
- `path_not_found`
- `path_not_file_or_directory`
- `scan_capped`
- `skipped_unreadable_files`

`warnings` 字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `code` | string | 稳定的机器可读 warning code，便于插件分支处理或 UI 展示。 |
| `message` | string | 面向 agent 和人的可读说明，描述风险、失败原因或下一步收敛方式。 |

## Debug 字段

`debug=true` 时，输出会附加 `debug` 对象：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `scan` | object | I/O 层扫描统计，用于判断候选是否被 cap、读文件是否失败、跳过了哪些目录。 |
| `scan.source_type` | `"local"` / `"viking"` | 扫描来源类型。 |
| `scan.root` | string / null | 扫描根路径或 URI。 |
| `scan.candidate_files` | int | 进入源码类型过滤前后的候选文件数量，具体语义由 source reader 记录。 |
| `scan.scanned_files` | int | 参与 ranking 的文件数量。 |
| `scan.read_files` | int | 成功读取内容的文件数量。 |
| `scan.failed_reads` | int | 读取失败的文件数量。 |
| `scan.capped` | bool | 是否因为 1000 文件上限发生裁剪。 |
| `scan.skipped_dirs` | list[string] | local source 扫描时跳过的重型或生成目录。 |
| `query_terms` | list[string] | 从 `query` 和 `failing_tests` 中抽取的普通检索词。 |
| `exact_query_identifiers` | list[string] | 从查询中抽取的精确标识符，例如函数名、配置名、错误码。 |
| `ranking_signals` | list[object] | 每个候选的主要 ranking 信号快照，用于调试排序，不建议作为稳定业务契约依赖。 |

## opencode 插件集成

opencode 插件默认使用 local source，并要求调用方提供 `projectDirectory`：

```js
source: {
  type: "local",
  path: projectDirectory,
}
```

插件请求：

- `query`: agent 传入的问题描述。
- `failing_tests`: 可选失败测试列表。
- `output_format`: 固定为 `json`。
- `debug`: 默认 `false`。

插件把 JSON 压缩为 agent-facing 文本：

- 普通定位输出展示 Top edit candidates、Useful behavior references、Suggested verification。
- staged action 输出以 `OpenViking staged action:` 开头，突出 PATCH FIRST、edit line、message shape line 和 immediate verification。
- 如果输出包含 local location，agent 直接读写本地 path。
- `codesearch` 仍用于 follow-up narrow terms，不替代 `codelocate` 的初始定位。

## 相关实现文件

- `openviking/parse/parsers/code/ast/code_tools.py`
  - 纯 ranking、schema dataclass、JSON/text formatter、verification 建议、diagnostic staged action。
- `openviking/server/routers/code.py`
  - HTTP code API，处理 local/viking source I/O、output format、debug scan 信息和本地路径开关。
- `openviking/server/mcp_endpoint.py`
  - MCP tool 实现，处理 direct tool 调用和本地路径开关。
- `openviking/server/config.py`
  - `allow_local_code_source_paths` 显式配置。
- `examples/opencode-plugin/lib/code-tools.mjs`
  - opencode tool schema、请求体、JSON 格式化、staged action 文本。
- `examples/opencode-plugin/index.mjs`、`examples/opencode-plugin/lib/utils.mjs`
  - 插件注册、配置与请求辅助逻辑。
- `tests/parse/test_code_tools.py`
  - 纯 ranking、JSON/text formatter、diagnostic staged action、cap 选择测试。
- `tests/server/test_api_code.py`
  - HTTP code API local/viking source、开关、cap、输出 schema 测试。
- `tests/server/test_mcp_endpoint_code.py`
  - MCP code tool local/viking source、开关、cap、输出 schema 测试。
- `examples/opencode-plugin/tests/*.test.mjs`
  - opencode 插件请求体和格式化输出测试。

## 测试与验证范围

实现覆盖以下行为：

- `query` 替代 `issue`，旧 shape 不被接受。
- local source 默认被显式开关拒绝。
- 开启开关后 local directory/source file 读取当前 checkout。
- local 输出只包含 `path`，viking 输出只包含 `uri`。
- `output_format=json` 返回 `schema_version=code-locate/v1`。
- `output_format=both` 返回 JSON，并用文本 formatter 填充 `summary_text`。
- `debug=false` 不输出 debug，`debug=true` 输出 scan/ranking 信息。
- `code_search` 和 `code_locate` 使用 1000 文件 cap。
- local/viking cap warning 文案与常量一致。
- diagnostic wording delta 能收敛为 PATCH FIRST staged action。
- staged action 不把测试断言作为第一修改目标。
- suggested verification 使用 `python3 -m ...`。
- opencode 插件发送 local `source.path=projectDirectory` 和 `query`。

## 非目标

- 不调用模型做 ranking。
- 不构建完整 call graph、type graph 或 dependency graph。
- 不兼容旧 `issue` 参数。
- 不在同一候选项中同时输出 local path 和 viking URI。
- 不让远程 HTTP 默认读取服务端本地路径。
