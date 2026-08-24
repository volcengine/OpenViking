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
| `summary` | 单一来源的忠实数字化摘要（仅当 `--reason` 明确要求时才生成） |

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
  --reason "面向团队检索整理成 Wiki，保留每条结论的出处" \
  --wait
```

- `--from` 可以重复或用逗号分隔，一次传多个来源。
- `--to` 目录不存在时会自动创建。
- 想要机器可读结果加 `-o json`；不想阻塞终端就去掉 `--wait`，用返回的 `task_id` 轮询：

```bash
ov task status cmp_01abc      # 查看进度与最终结果
ov task cancel cmp_01abc      # 协作式取消
```

## 第四步：看看产物

编译完成后目标目录里就是一套 Markdown 知识库。先看导航页，再按需钻进去：

```bash
ov tree viking://resources/research-wiki
ov read viking://resources/research-wiki/index.md
```

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
- [VikingBot API → compile()](../api/24-vikingbot.md#compile)
