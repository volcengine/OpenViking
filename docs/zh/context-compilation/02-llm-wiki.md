# 示例：LLM Wiki

把一批异构来源编译成一套 Karpathy 风格、有出处、互相链接的 **LLM Wiki**：每一页有明确的检索目的，开头一句话直给结论，术语统一，关系显式，证据紧贴结论，并由一个 `index.md` 做导航入口。

这套 Skill 会按页面的检索目的挑选最合适的页面类型：

| 页面类型 | 用于 |
|---------|------|
| `entity` | 有稳定身份的具名事物（人、组织、产品、项目、系统、数据集、标准、事件……） |
| `concept` | 可复用的思想、机制、模式、协议、心智模型 |
| `method` | 有前置条件、有序步骤、可验证结果的可复用流程 |
| `comparison` | 在明确维度上对两个及以上对象做并排评估 |
| `analysis` | 围绕一个问题的跨来源结论 |
| `summary` | 单一来源的忠实数字化摘要（仅当 `--instruction` 明确要求时才生成） |

默认以 `entity` 和 `concept` 为主，其余类型只在满足各自的严格判定时才提升。产物是一个**知识库**，不是逐文档的摘要拼盘。

Skill 源码：[examples/compile/ov-compile-skills/llm-wiki](https://github.com/volcengine/OpenViking/tree/main/examples/compile/ov-compile-skills/llm-wiki) · 可视化脚本：[examples/compile/graph-show/llm-wiki](https://github.com/volcengine/OpenViking/tree/main/examples/compile/graph-show/llm-wiki)

## 第一步：准备来源

如果材料还没进 OpenViking，先导入。目录型来源用 `ov add-resource`，单文件可以用 `ov write`：

```bash
# 导入一个目录作为来源
ov add-resource ./my-research --to viking://resources/research --wait

# 或者写入单个文件
ov mkdir viking://resources/research
ov write viking://resources/research/notes.md \
  --from-file ./notes.md --mode create --wait
```

确认来源已就位：

```bash
ov ls -r viking://resources/research
```

## 第二步：添加 Skill

把 LLM Wiki 的 Skill 装进服务。默认落到你的用户私有 skills 命名空间；想让团队共用就用 `-p viking://agent/skills`：

```bash
ov add-skill examples/compile/ov-compile-skills/llm-wiki --wait
```

查看装好的 Skill URI：

```bash
ov skills list
# → viking://agent/skills/llm-wiki  （或 viking://user/<你>/skills/llm-wiki）
```

## 第三步：执行编译

```bash
ov compile \
  --from viking://resources/research \
  --to viking://resources/research-wiki \
  --skill viking://agent/skills/llm-wiki \
  --instruction "面向团队检索整理成 Wiki，保留每条结论的出处"
```

- `--from` 可以重复或用逗号分隔，一次传多个来源。
- `--to` 目录不存在时会自动创建。
- 想要机器可读结果加 `-o json`；命令会立即返回 `task_id`，用它查询或取消任务：

```bash
ov task status cmp_01abc      # 查看进度与最终结果
ov task cancel cmp_01abc      # 协作式取消
```

启用子代理时，Compile 会读取每个来源一次，在任务内存中按每批最多 **80,000 个字符**组织输入批次。小文件可以合批，长文件可以跨批；优先在章节、段落或完整 Markdown 表格行之间划分，并为后续段补充所在章节标题、文档 frontmatter 和表头。重复上下文也计入字符预算，另有每批最多 10 个不同来源的限制。

每个子代理直接收到分配范围的完整原文、原 URI、字符偏移和行号，生成知识草稿；已有 Wiki 的查找、去重和更新在后续主题合并阶段处理。超长段落可以按完整行划分；单行超过预算时使用带续段标记的字符范围。源文件和目录保持不变，不创建分片资源，来源链接仍指向原文件。范围覆盖表示原文已经交付给子代理，不等于知识提取完整性的保证。

同一个 compile 任务内，失败来源批次的重试沿用 `__compile_staging__/drafts/source-<批次号>` 目录。子代理会收到已有文件清单和主代理的重试要求，修复或补齐已有页面后统一提交；文件存在本身不代表覆盖完整。剩余 15、8、3、1 轮时会提醒收尾提交。保留草稿不等于支持 bot 重启后的任务恢复。

主代理在收集子任务结果时获得草稿目录，保存合并计划时只收到统计、本次成功保存的分配变更和问题预览。完整分配、任务说明和检查点保存在 `__compile_staging__/merge-state.json`。内部工具 `merge_compile_drafts` 支持只读 `view` 查询：`drafts`、`unassigned`、`groups`、`failures`、`conflicts`，以及配合准确 `group_name` 的 `group` 查询。列表通过 `offset`、`limit` 分页（默认 20 条，最多 50 条），`next_offset` 表示后续页面。主代理压缩上下文后可通过这些查询恢复细节，最终覆盖校验始终使用完整状态。

## 第四步：看看产物

最终校验错误会连同最多 3 轮的修复倒计时返回主代理。修复仍失败时，Compile 将最终输出目录中所有已生成文件原样写入目标，包括未通过内容或合并校验的文件。写入成功后任务完成，不报告未完成部分；本次输出未涉及的已有目标文件保持不变。

编译完成后目标目录里就是一套 Markdown 知识库。先看导航页，再按需钻进去：

```bash
ov tree viking://resources/research-wiki
ov read viking://resources/research-wiki/index.md
```

Resource Wiki 提交时，代码会根据 YAML `sources` 补充正文来源链接，兼容 `resource` 和 `path` 字段，并保留已有引用。导航根据本次提交和目标目录中保留的有效 Wiki 页面补齐，保留模型写的介绍，并创建缺失的祖先目录 `index.md`。索引在标题后提供描述段落，再以 `## 分类导航` 引出普通 Markdown 链接，不使用 HTML 注释边界标记。每个索引只列当前目录的直接知识页及直接子目录的 `index.md`，更深层内容通过子目录索引逐层访问。这些处理不改变已有文件名。

相对链接只有在目标文件名唯一、链接文字匹配页面标题、文件名或显式别名时才会自动修正。有歧义或找不到目标的链接保持原样，记录在任务结果 `link_report.unresolved`，不会导致提交失败或增加模型修复轮次。达到轮次上限后的兜底保存也会对格式有效的 Wiki 页面执行同样处理。

典型结构（页面类型对应目录）：

```text
research-wiki/
├── index.md            # 导航入口，类型 index
├── entity/
│   └── <标题>.md
├── concept/
│   └── <标题>.md
├── method/…  comparison/…  analysis/…
```

## 第五步：可视化成交互式图谱

`wiki_graph.py` 会**直接连接 OpenViking 服务**读取 Wiki 页面（不需要先下载到本地），把页面按类型着色、按链接连边，生成一个独立的交互式 HTML：

```bash
python examples/compile/graph-show/llm-wiki/wiki_graph.py \
  viking://resources/research-wiki \
  -o research-wiki-graph.html \
  --title "研究知识库"
```

用浏览器打开 `research-wiki-graph.html` 即可。节点是页面（按 `entity`/`concept`/`method`… 分色），边是页面之间的链接，点节点能看正文。

连接配置的解析顺序和 `ov` 一致：命令行参数 → `OPENVIKING_*` 环境变量 → `~/.openviking/ovcli.conf`。远程服务显式传参：

```bash
python examples/compile/graph-show/llm-wiki/wiki_graph.py \
  viking://resources/research-wiki \
  --url https://openviking.example.com \
  --api-key "$OPENVIKING_API_KEY" \
  -o research-wiki-graph.html --title "研究知识库"
```

一次传多个 Wiki，可以把它们画在同一张图里对比：

```bash
python examples/compile/graph-show/llm-wiki/wiki_graph.py \
  viking://resources/wiki-a viking://resources/wiki-b \
  -o combined.html --title "两个知识库对照"
```

## 相关文档

- [上下文编译概览](./01-overview.md)
- [Knowledge Graph 示例](./03-knowledge-graph.md)
- [Agent Runtime API](../api/23-agent-runtime.md)
