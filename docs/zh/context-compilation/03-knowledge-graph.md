# 示例：Knowledge Graph

把一批来源编译成一个**证据可溯、可直接可视化**的知识图谱：语义分类的实体节点、语句级出处、带类型的有向关系边。产物是这样一棵工件树：

```text
entities/
  <entity-id>.md      # 每个节点一个文件，frontmatter 里有 type/id/title/entity_type/description/sources
relations.jsonl       # 每行一条有向边
```

每条边是一行紧凑 JSON，可读成 `<from> <relation> <to>` 这样一句话：

```json
{"from":"孙悟空","relation":"member_of","label":"属于","to":"取经队伍","evidence":["viking://resources/source.md"]}
```

`relation` 是稳定、语言无关的机器谓词（`member_of`、`leads`、`located_in`……），`label` 是对应的本地化显示名（`属于`、`率领`、`位于`……），`entity_type` 用于可视化时的节点颜色、形状和过滤。图谱可以增量刷新：已有节点和边会被保留、合并证据，新知识追加进来。

Skill 源码：[examples/compile/ov-compile-skills/knowledge-graph](https://github.com/volcengine/OpenViking/tree/main/examples/compile/ov-compile-skills/knowledge-graph) · 可视化脚本：[examples/compile/graph-show/knowledge-graph](https://github.com/volcengine/OpenViking/tree/main/examples/compile/graph-show/knowledge-graph)

## 第一步：准备来源

```bash
ov add-resource ./journal-to-the-west --to viking://resources/journal --wait
ov ls -r viking://resources/journal
```

## 第二步：添加 Skill

```bash
ov add-skill examples/compile/ov-compile-skills/knowledge-graph --wait
ov skills list
# → viking://agent/skills/knowledge-graph
```

## 第三步：执行编译

```bash
ov compile \
  --from viking://resources/journal \
  --to viking://resources/journal-kg \
  --skill viking://agent/skills/knowledge-graph \
  --reason "抽取人物、地点、法宝及其关系，构建可遍历的知识图谱" \
  --wait
```

去掉 `--wait` 会立刻返回 `task_id`，之后：

```bash
ov task status cmp_01abc      # 查看进度与最终结果
ov task cancel cmp_01abc      # 协作式取消
```

## 第四步：看看产物

```bash
ov tree viking://resources/journal-kg
ov read viking://resources/journal-kg/relations.jsonl
ov read viking://resources/journal-kg/entities/孙悟空.md
```

## 第五步：可视化成交互式图谱

与 LLM Wiki 的脚本不同，`knowledge_graph.py` 读取的是**本地目录**（需要 `entities/` 和 `relations.jsonl` 都在本地）。所以先把产物拉到本地，再生成 HTML。

先把整棵工件树下载下来。`ov get` 一次下载一个文件，配合 `ov ls -r -s` 列出全部路径即可批量拉取：

```bash
SRC="viking://resources/journal-kg"
DST="./journal-kg"
mkdir -p "$DST"
ov ls -r -s "$SRC" | while read -r uri; do
  # 只下载文件（entities/*.md 和 relations.jsonl），跳过目录
  case "$uri" in
    */entities|"$SRC") continue ;;
  esac
  rel="${uri#$SRC/}"
  mkdir -p "$DST/$(dirname "$rel")"
  ov get "$uri" "$DST/$rel"
done
```

> `ov get` 要求本地目标路径尚不存在，所以重新下载前先清掉旧目录（`rm -rf ./journal-kg`）。

确认本地目录结构正确：

```bash
find ./journal-kg          # 应能看到 entities/*.md 和 relations.jsonl
```

生成交互式 HTML：

```bash
python examples/compile/graph-show/knowledge-graph/knowledge_graph.py \
  ./journal-kg \
  -o journal-kg.html \
  --title "西游知识图谱"
```

用浏览器打开 `journal-kg.html`。脚本会先做校验——`relations.jsonl` 必须是合法 JSON、每个实体文件都要有稳定的 `id` 和 `title`、每条边的两端都要能对上某个实体节点——校验不通过会直接报错并指出问题行，所以它同时也是产物质量的检查器。节点按 `entity_type` 分色分形，边显示本地化 `label`，点节点能看到该实体的正文、别名和出处。

## 相关文档

- [上下文编译概览](./01-overview.md)
- [LLM Wiki 示例](./02-llm-wiki.md)
- [VikingBot API → compile()](../api/24-vikingbot.md#compile)
