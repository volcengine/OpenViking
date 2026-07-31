# `add_resource` 不拆分模式设计

## 背景

当前分支把 `parse_mode=no_parse` 实现为跳过格式解析器并原样保存源文件。该行为不符合实际需求：PDF、Word、PowerPoint、HTML 等文档仍应转换成 Markdown，只是不再按照标题、章节、段落或长度拆成多个 Markdown 文件。

本设计取代 `docs/superpowers/plans/2026-07-16-add-resource-no-parse.md` 中的原样直存方案。旧计划和相关实现将在新实施计划中删除或改写。

## 目标

为所有 `add_resource` 入口提供：

```text
parse_mode = default | no_split
```

- `default`：保持现有解析与拆分行为。
- `no_split`：保持现有访问、格式识别和格式转换行为，但每个文档正文只生成一个完整的 Markdown 文件。

例如：

```text
输入：社交网络中英文剧本.pdf
目标：viking://resources/shendiao_0731_02

最终资源：
shendiao_0731_02/
├── 社交网络中英文剧本.md
├── .abstract.md
└── .overview.md
```

如解析器从文档中提取了被 Markdown 引用的图片，图片资源和必要的内部 sidecar 可以继续存在；“不拆分”约束的是每个文档正文只有一个 Markdown 文件。

## 非目标

- 不保留 PDF、DOCX 等源文件的原始字节。
- 不把一个目录中的多个源文档合并成一个 Markdown。
- 不改变 `.abstract.md`、`.overview.md`、语义理解、向量化或内部向量分块行为。
- 不为尚未合入的 `no_parse` 值提供兼容别名。
- 不改变 `default` 模式的布局、URI 或解析后端选择。

## 方案选择

采用“在 Markdown 布局层控制拆分”的方案：

1. 资源仍通过现有 Accessor 获取本地资源。
2. 资源仍通过现有 ParserRouter 和格式 Parser 解析。
3. PDF、Word、PowerPoint、HTML、文本等解析器仍将内容转换为 Markdown。
4. `MarkdownParser` 在布局阶段识别不拆分选项，直接写入 `<文档名>.md`，不执行标题、段落或长度拆分。

未采用以下方案：

- 解析完成后拼接多个 Markdown：难以可靠恢复顺序、标题层级和图片引用。
- 在每种格式 Parser 中分别实现单文件布局：重复逻辑多，容易产生格式间行为差异。

## 对外接口

REST API、MCP、Python 本地客户端、Python SDK、Go SDK、TypeScript SDK 和 Rust CLI 统一使用 `default | no_split`。

CLI 示例：

```bash
./target/debug/ov add-resource \
  /path/to/社交网络中英文剧本.pdf \
  --to viking://resources/shendiao_0731_02 \
  --parse-mode no_split
```

默认模式继续省略请求字段，以维持对旧服务端的兼容。显式 `no_split` 时发送：

```json
{
  "parse_mode": "no_split"
}
```

非法值在入口处返回参数错误。原分支中的 `no_parse` 不再接受。

## 数据流

### 单文件

```text
add_resource
  -> Accessor
  -> ParserRouter
  -> 格式 Parser（PDF/DOCX/PPTX/HTML/...）
  -> Markdown 内容
  -> MarkdownParser(no_split=True)
  -> <原文件名>.md
  -> TreeBuilder
  -> 语义处理与向量化
```

文件名沿用现有 `resource_name`、`source_name` 和上传原始文件名的解析规则，只把输出扩展名统一为 `.md`。

### 目录

目录仍由 `DirectoryParser` 扫描。每个有格式 Parser 的文档接收不拆分选项，并分别生成一个 Markdown；相对目录结构按现有 `preserve_structure` 行为保留或展平。没有专用 Parser 的文本或代码文件继续沿用目录解析器的现有直接写入行为。

### 解析后端

内置格式 Parser 的不拆分行为由 `MarkdownParser` 保证。`no_split` 不应绕过 ParserRouter，也不应自动改变用户配置的解析后端。

如果某个外部解析后端只返回已拆分的产物且没有单文件输出能力，不能通过不可靠的事后拼接假装满足语义；应返回明确的不支持错误。当前改动至少保证内置文档解析链路和所有已覆盖的入口行为一致。

## 实现边界

- 将 `ParseMode.NO_PARSE` 重命名为 `ParseMode.NO_SPLIT`，值改为 `no_split`。
- 删除 `DirectResourceStager` 及为原样直存新增的目录扫描放宽逻辑。
- 在统一资源处理器中继续走正常解析链路，并把内部不拆分选项传给 Parser。
- `MarkdownParser` 在已完成命名和 frontmatter 处理后，直接规划根目录和唯一 Markdown 写操作。
- `DirectoryParser` 把不拆分选项传递给每个子文档 Parser。
- 服务层的监听任务、异步队列和上传令牌继续持久化 `parse_mode`，使后台重放保持相同行为。
- 连接器仍按现有能力校验模式处理非默认参数，不静默忽略。

## 错误处理

- `parse_mode` 不是 `default` 或 `no_split`：入口直接报错。
- 外部解析后端无法保证单正文 Markdown：返回清晰的不支持错误，不回退为原文件直存，也不静默拆分。
- 格式转换失败：沿用对应 Parser 的现有失败处理。
- `no_split` 与目录的 `preserve_structure=false` 不冲突；它只控制文档内部拆分，目录是否展平继续由 `preserve_structure` 决定。

## 测试策略

按 TDD 增加或改写以下覆盖：

1. 超过默认拆分阈值、没有标题的 Markdown 在 `no_split` 下只生成 `<文档名>.md`，且内容完整。
2. 有多级标题的长 Markdown 在 `no_split` 下仍只生成一个文件。
3. PDF Parser 转换出的长 Markdown 在 `no_split` 下只生成一个 `.md`，不保留 `.pdf`。
4. `default` 模式仍按现有规则拆分，防止行为回归。
5. 目录输入中的每个可解析文档各生成一个 Markdown，并遵守现有相对目录策略。
6. REST、MCP、上传令牌、监听任务、异步队列及各 SDK 正确传递 `no_split`。
7. Rust CLI 接受 `--parse-mode no_split`，拒绝 `no_parse` 和未知值。
8. 文档示例和字段说明统一描述“正常解析但不拆分”。

## 交付与集成

实现完成后运行 Python 相关测试、Rust CLI 测试与格式化、Go SDK 测试、TypeScript SDK 测试及差异检查。随后获取最新 `upstream/main`，rebase 当前分支，解决冲突并重新运行受影响回归测试。最后强制安全推送分支并用中文更新现有 PR 的标题与描述。
