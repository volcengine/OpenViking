# Tool Stub 设计文档

**范围**: OpenViking 当前 `tool stub` 能力的实现说明，覆盖类型识别、规则化摘要、原文外置、回溯读取与测试边界。
**状态**: 已实现，本文档描述当前代码行为，不额外引入新需求。

---

## 概述

OpenViking 原生已经支持 tool result preview。原有链路能够在 session 写入阶段把过大的 tool output externalize，并在 `ToolPart` 中留下一个 preview stub，同时保留 ref 供后续回溯。

这次工作的重点不是新建 externalize 机制，而是在现有能力上优化 preview 的生成方式：从偏 `head + tail` 的直接截断，升级为按内容类型输出更稳定、更可读的规则化摘要。

换句话说，本次改动保持下面这些基础能力不变：

1. 哪些 tool output 需要 externalize，仍由 session 写入阶段决定。
2. 原始内容仍写入 `ToolResultStore`。
3. `ToolPart` 仍保留 stub 和 `tool_output_ref`。
4. 原文回溯方式仍是 `read/search/list`。

这次变化主要集中在 preview 生成层：

1. 在 session 写入阶段识别哪些 tool output 需要 externalize。
2. 原始输出写入 session 下的 tool result store。
3. preview 从简单截断优化为基于内容和 MIME 的 deterministic synopsis。
4. 把原始 `ToolPart.tool_output` 替换成 stub 文本，并保留 `tool_output_ref`。
5. 后续通过 `read/search/list` 工具按 ref 回溯原文。

`text` 类型只做规则化摘要，不接 LLM。

---

## 设计目标

1. 在保留 OV 原生 externalize 和 ref 回溯链路的前提下，优化 preview 的可读性。
2. 减少大 tool output 对上下文窗口的占用。
3. 把原有偏 `head + tail` 的截断 preview，升级为按类型输出的规则化摘要。
4. 对常见文本型输出给出稳定、可读的 deterministic synopsis。
5. 保留原始输出，支持按 ref 精确回溯。
6. 当前版本不做 LLM 摘要，只做 deterministic 的规则化摘要。

---

## 端到端流程

### 1. 选择哪些输出需要 externalize

入口在 [session.py](/F:/code/OpenViking/openviking/session/session.py:685) 的 `_externalize_large_tool_output_group()`。

当前按两类条件触发：

1. 单个 tool output 超过 `threshold_chars`。
2. 同一个 assistant turn 中多个 tool output 的总 inline 体积超过 `assistant_turn_inline_budget_chars`。

命中后会进入 externalization 流程。触发阈值仍沿用 OV 原有配置；规则化摘要本身不改变“什么时候 externalize”。

### 2. 外置原始结果并替换 ToolPart

入口在 [session.py](/F:/code/OpenViking/openviking/session/session.py:606) 的 `_externalize_tool_part()`。

这一步会：

1. 把原始 `tool_output` 写入 `ToolResultStore`。
2. 调用 preview 生成逻辑产出 stub 文本。
3. 用 stub 替换 `ToolPart.tool_output`。
4. 把原始结果的 ref 写入 `ToolPart.tool_output_ref`。

因此被 stub 后，消息里保留的是 preview，不再是完整原始 tool result；原始结果仍在外置存储里。

### 3. 生成 synopsis 和 stub

入口在 [tool_result_store.py](/F:/code/OpenViking/openviking/session/tool_result_store.py:38) 的 `make_preview()`：

1. 原生 preview/stub 能力保留，但内容生成逻辑从简单截断演进为 typed synopsis。
2. `generate_tool_result_synopsis()` 负责类型识别和摘要生成。
3. `render_tool_result_stub()` 负责把摘要渲染成最终 stub 文本。

核心实现位于 [tool_result_synopsis.py](/F:/code/OpenViking/openviking/session/tool_result_synopsis.py:363)。

当前原则：

1. externalize 触发阈值沿用 OV 原有配置。
2. 常见类型使用固定规则上限生成 synopsis，不依赖 `preview_chars` 控制摘要长度。
3. `preview_chars` 只作为无法规则化时的 fallback head/tail 采样预算，并作为兼容字段保留在 stub header / metadata 中。

### 4. 原文回溯

当前回溯能力由 session 暴露三类工具：

1. [session.py](/F:/code/OpenViking/openviking/session/session.py:932) `read_tool_result()`：按 `offset/limit` 读取原始内容片段。
2. [session.py](/F:/code/OpenViking/openviking/session/session.py:952) `search_tool_result()`：在原始内容中做关键字搜索。
3. [session.py](/F:/code/OpenViking/openviking/session/session.py:972) `list_tool_results()`：列出当前 session 已 externalize 的结果。

对应存储层实现位于 [tool_result_store.py](/F:/code/OpenViking/openviking/session/tool_result_store.py:77)。

---

## 支持的数据类型

当前支持的 synopsis kind 定义在 [tool_result_synopsis.py](/F:/code/OpenViking/openviking/session/tool_result_synopsis.py:18)：

`json` / `csv` / `tsv` / `yaml` / `xml` / `code` / `text` / `unknown`

### 类型对照表

| 类型 | 识别方式 | 处理办法 | stub 中保留内容 |
|---|---|---|---|
| `json` | MIME 含 `json`，或内容以 `{` / `[` 开头且能被 JSON decoder 解析 | 解析 top-level shape，提取 keys、array length、标量示例 | `summary + structure + notable_items` |
| `csv` | 含逗号，且能按 CSV 读成规则表格 | 统计行列数、列名，保留首条数据样例 | `summary + structure` |
| `tsv` | 含制表符，且能按 TSV 读成规则表格 | 统计行列数、列名，保留首条数据样例 | `summary + structure` |
| `yaml` | 满足 YAML 启发式并能 `yaml.safe_load()` 成 dict/list | 提取 top-level keys 和 child type | `summary + structure` |
| `xml` | MIME 含 `xml`，或内容以 `<` 开头且可解析 | 提取 root tag、属性数、子标签计数 | `summary + structure` |
| `code` | 命中代码模式正则 | 提取 imports、symbols、line_count | `summary + structure + notable_items` |
| `text` | 作为最终 fallback | 规则化文本摘要，不保留全文 sample | `summary` |
| `unknown` | 空内容、binary-like 内容，或带明确 MIME 但解析失败的结构化内容 | 无法规则化时使用 fallback head/tail sample | `summary + sample` |

---

## 类型识别顺序

识别顺序定义在 [tool_result_synopsis.py](/F:/code/OpenViking/openviking/session/tool_result_synopsis.py:363)。

当前顺序如下：

1. 空内容：直接标为 `unknown`。
2. binary-like 内容：如果出现 NUL 或控制字符比例过高，标为 `unknown`。
3. `json`
4. `xml`
5. `tsv`
6. `csv`
7. `yaml`
8. `code`
9. 最终 fallback 为 `text`

这个顺序的目的是优先识别结构化格式，再识别代码，最后才把剩余内容视作普通文本。日志样式输出不再作为独立类型处理，会走 `text`，与 lossless-claw 的 large-file exploration 行为保持一致。

---

## 各类型处理策略

### JSON

实现位于 [tool_result_synopsis.py](/F:/code/OpenViking/openviking/session/tool_result_synopsis.py:150)。

输出重点：

1. 顶层类型是 object 还是 array。
2. top-level keys，最多 10 个。
3. 子字段是 object / array / scalar。
4. 最多若干条标量示例。
5. 若第一个 JSON value 后还有额外字符，会记入 `trailing_chars_after_first_json_value`。
6. 不额外保留原始 JSON sample。

### CSV / TSV

实现位于 [tool_result_synopsis.py](/F:/code/OpenViking/openviking/session/tool_result_synopsis.py:218)。

输出重点：

1. 列数与数据行数。
2. 首行列名。
3. 首条数据样例，最多 180 字符。

只接受“列数基本一致”的表格；不规则分隔文本不会被误判成表格。

### YAML

实现位于 [tool_result_synopsis.py](/F:/code/OpenViking/openviking/session/tool_result_synopsis.py:184) 与 [tool_result_synopsis.py](/F:/code/OpenViking/openviking/session/tool_result_synopsis.py:268)。

输出重点：

1. 顶层是 object 还是 array。
2. top-level keys，最多 30 个。
3. 每个 key 对应的 child type。
4. 不额外保留 YAML sample。

### XML

实现位于 [tool_result_synopsis.py](/F:/code/OpenViking/openviking/session/tool_result_synopsis.py:204)。

输出重点：

1. 根标签名。
2. 根节点属性数量。
3. 一级子标签频次，最多 30 个。
4. 不额外保留 XML sample。

### Code

实现位于 [tool_result_synopsis.py](/F:/code/OpenViking/openviking/session/tool_result_synopsis.py:280)。

输出重点：

1. 总行数。
2. import 语句，最多 12 条，单条最多 180 字符。
3. 顶层 symbol，如 `class Foo`、`def bar`、`fn baz`，最多 24 条，单条最多 200 字符。
4. 不额外保留 head/tail sample。

当前是轻量规则识别，不做 AST 级代码摘要。

### Text

实现位于 [tool_result_synopsis.py](/F:/code/OpenViking/openviking/session/tool_result_synopsis.py:319)。

`text` 类型明确不接 LLM，只做 deterministic fallback。摘录采用固定上限，不受 `preview_chars` 影响。

日志样式输出也归入 `text`。如果需要定位错误/警告行，优先通过 stub 中的 ref 使用 `openviking_tool_result_search` 搜索原始 payload，避免仅靠关键字把普通文档误判成日志。

输出重点：

1. `Characters`
2. `Words`
3. `Lines`
4. `Detected section headers`
5. `Opening excerpt`
6. `Closing excerpt`

标题提取规则：

1. Markdown 标题，如 `# Heading`
2. 全大写风格标题行，如 `SYSTEM STATUS`

摘录规则：

1. opening excerpt 固定最多取前 500 字符。
2. closing excerpt 固定最多取后 500 字符。
3. 先压缩空白，再写入摘要。
4. 不额外保留 `sample` 字段，避免把完整原文重新带回上下文。

### Unknown

`unknown` 是当前实现里的保守分类，不是富类型支持。

当前会落到 `unknown` 的场景包括：

1. 输出为空。
2. 文本中存在明显二进制控制字符。
3. MIME 明确标成 JSON/XML，但内容解析失败。

对这些内容，stub 保留基础说明；非空内容会使用 `preview_chars` 生成 head/tail fallback sample。原始 payload 仍通过 ref 回溯。

---

## Stub 文本结构

渲染逻辑位于 [tool_result_synopsis.py](/F:/code/OpenViking/openviking/session/tool_result_synopsis.py:436)。

当前 stub 由两部分组成：

### Header

包含：

1. `tool_name`
2. `kind`
3. `original_chars`
4. `preview_chars`
5. `ref`
6. `sha256`
7. `reason`

### Body

按 synopsis 内容选择性渲染：

1. `Synopsis`
2. `Structure`
3. `Notable items`
4. `Sample`
5. `Explore`

其中 `Explore` 会提示模型使用：

1. `openviking_tool_result_search`
2. `openviking_tool_result_read`
3. `openviking_tool_result_list`

---

## 原始内容存储与回溯

实现位于 [tool_result_store.py](/F:/code/OpenViking/openviking/session/tool_result_store.py:101)。

每个 externalized tool result 会写入两类文件：

1. `output.txt`：原始输出内容。
2. `metadata.json`：元数据和 synopsis。

元数据包括：

1. `tool_result_id`
2. `session_id`
3. `message_id`
4. `tool_id`
5. `tool_name`
6. `created_at`
7. `original_chars`
8. `preview_chars`
9. `sha256`
10. `mime_type`
11. `synopsis_kind`
12. `synopsis`
13. `storage_uri`
14. `output_uri`
16. `offset_unit=unicode_code_point`

### 读取方式

1. `read()`：按 `offset/limit` 读取原始文本片段，适合长文本逐段展开。
2. `search()`：在原始文本中查关键词，并返回带 offset 的 snippet。
3. `list()`：按 session 列出已有外置结果，便于发现 ref。

当前读取模型是“面向长文本”的；它适合回看日志、代码、表格文本、普通文本。

---

## 当前边界与取舍

1. `text` 不接 LLM，原因是我们当前只需要稳定、低成本、可测试的规则化 stub。
2. 当前回溯接口是 `read/search/list`，更适合大文本原文回看。
3. `offset/limit` 对“顺序文本展开”很合适，但对图片、二进制、复杂多模态结果并不理想。

---

## 测试覆盖

当前相关测试包括：

1. [test_tool_result_synopsis.py](/F:/code/OpenViking/tests/session/test_tool_result_synopsis.py:1)：覆盖类型识别和 synopsis 生成，包括固定 caps、text 的 500/500 deterministic fallback，以及 unknown 的 head/tail fallback。
2. [test_tool_result_externalization.py](/F:/code/OpenViking/tests/session/test_tool_result_externalization.py:1)：覆盖 externalization、stub 替换、阈值边界、aggregate budget、ref 回溯等端到端流程。
3. [test_api_sessions.py](/F:/code/OpenViking/tests/server/test_api_sessions.py:190)：覆盖 HTTP API 层的 tool result externalization、stub 文案、`read/list/search` 回溯，以及 `synopsis_kind` / `synopsis.kind` 元数据透出。

当前相关测试共 29 个用例通过，可作为后续继续补齐真实输出回归用例的基础。

---

## 结论

OpenViking 当前的 `tool stub` 已经具备一版完整闭环：

1. OV 原生的 externalize、preview stub、ref 回溯链路继续保留。
2. preview 生成方式已从偏 `head + tail` 的截断，升级为按类型的规则化摘要。
3. `text` 类型采用 deterministic 规则摘要，不接 LLM。
4. 能通过 `read/search/list` 对外置原文进行回溯。

后续优先级应放在更深的回归测试、更多真实输出样本，以及是否需要继续优化 `read/search/list` 的原文回溯体验，而不是先扩展 LLM 摘要或媒体解析。
