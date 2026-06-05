# Issue 2383 洞察记录

这份文档记录围绕 `#2383` 重复 `add_resource` 仍产生较多 embedding 工作量时讨论出的系统洞察。它不是最终修复方案，而是用于沉淀判断依据。

## 1. `.overview.md` 同时承担了人读文档和机器缓存的角色

当前增量导入时，如果文件内容没有变化，系统会尝试复用旧的文件 summary。

具体做法是：

- 读取父目录下已有的 `.overview.md`
- 用 `_parse_overview_md()` 从 Markdown 文本里反解析出 `filename -> summary`
- 如果能解析出当前文件名对应的 summary，就复用旧 summary，并跳过文件级 vectorization
- 如果解析不出来，就重新生成 summary，并重新 vectorize

这说明 `.overview.md` 在当前设计里有双重角色：

- 对用户/模型可读的目录概览
- 对系统可反解析的 summary cache

这个设计有风险，因为 `.overview.md` 是 Markdown 文本，不是严格机器结构。

## 2. overview 生成不是完全无格式，但也不是强结构化数据

overview 生成 prompt 确实要求模型输出固定 Markdown 结构：

- H1 标题
- 简短描述
- Quick Navigation
- Detailed Description
- 每个文件或子目录一个 H3 subsection

所以问题不是“没有指定格式”。

更准确的问题是：

`系统要求 LLM 输出强 Markdown 格式，但后续代码又把这份 Markdown 当作稳定可解析的结构化缓存。`

Markdown 结构对人类阅读友好，但对机器反解析不够稳：

- LLM 可能不严格输出 H3
- H3 标题可能不是纯文件名
- 标题可能包含编号、解释、路径或重复词
- parser 只识别有限格式，比如 `### filename` 或 `[1] filename: summary`
- 一旦格式偏离，旧 summary 就无法复用

## 3. 这个点对 `#2383` 的意义

`#2383` 的核心问题是重复导入没有达到预期的增量效果。

其中一个关键原因可能是：

`文件内容虽然没变，但旧 summary 不能稳定从 .overview.md 中恢复，于是系统重新 summary + 重新 embedding。`

这不是单纯返回值显示错误，而是可能真实触发额外模型调用。

## 4. 初步产品/系统判断

如果希望持续导入真的低成本，文件级 summary 应该有更稳定的缓存来源。

更合理的方向可能是：

- 不把 `.overview.md` 同时当做人读文档和机器缓存
- 单独维护结构化 summary cache
- 或在派生语义文件中加入可验证的结构化 metadata block
- 或让目录 overview 继续面向阅读，而把增量判断依赖放到更稳定的内部状态上

当前最重要的判断是：

`overview 可以继续是知识页，但不应该是唯一的机器可恢复 summary source。`

## 5. 文件变化检测不应该依赖 summary 恢复结果

进一步讨论后，一个更底层的判断是：

`文件是否变化，应该只由文件本身决定，而不是由 summary 是否能恢复决定。`

当前代码里已经有文件层面的内容对比：

- 先比较源文件和目标文件的 size
- size 相同后再读取完整 content 比较

这说明系统本来就具备文件级变化检测。

但当前增量链路里，文件未变化后还会继续尝试从 `.overview.md` 中恢复旧 summary。如果旧 summary 恢复失败，代码会重新生成 summary，并把该文件重新标记为 changed。

这里的问题是：

`summary cache miss` 不等于 `file content changed`。

更合理的状态应该拆开：

- `file_content_changed`
- `summary_cache_miss`
- `summary_regenerated`
- `embedding_needs_rebuild`
- `directory_semantics_changed`

文件没有变化时，一般不应该因为 summary 取不回来就把文件当成内容变化。summary 丢失或恢复失败，最多说明需要修复或补齐 summary 缓存，不应该直接触发文件级 changed 状态向上游目录传播。

## 6. 目录语义更新应该由真实子内容变化驱动

目录的 summary / overview / abstract 自底向上生成是合理的，因为父目录需要依赖当前目录文件 summary 和子目录 abstract。

但触发条件应该是：

`当前目录下的文件或子目录语义真的发生变化。`

不是：

`因为旧 summary 没恢复出来，所以把未变化文件当成变化文件。`

也就是说，理想链路应该是：

- 文件内容没变：文件 summary 和 embedding 默认不需要重建
- 文件新增、删除、修改：当前目录 summary / overview / abstract 需要更新
- 子目录 abstract 真的变化：父目录 summary / overview / abstract 才需要更新

这个洞察可以概括为：

`增量导入应该由真实内容 diff 驱动，而不是由派生语义缓存恢复是否成功来驱动。`
