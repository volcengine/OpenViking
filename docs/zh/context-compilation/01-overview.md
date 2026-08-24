# 上下文编译概览

`ov compile` 把散落在 OpenViking 里的原始材料——文档、笔记、网页、访谈记录、研究资料、代码仓库——**编译**成结构化、可检索、方便人和 Agent 反复使用的知识产物。

## 它是怎么工作的

你只需要提供三样东西：

- **从哪里来（`--from`）**：一个或多个来源目录/文件；
- **到哪里去（`--to`）**：产物写入的目标目录；
- **用哪个 Skill（`--skill`）**：一份描述「要编译成什么样」的说明书。

再加上一个可选的 **`--reason`**：给这次编译的补充指令，比如范围、受众、语言、侧重点。Skill 定义了「编译成什么形态」，`--reason` 则在此之上告诉 Agent「这一次具体要什么」。

剩下的交给 OpenViking。Compile 依赖 [VikingBot](../concepts/15-vikingbot.md)：任务被接受后，VikingBot 会加载你指定的 Skill，以你的身份读取来源，在一个独立的 **Agent Loop** 里自主地阅读、归纳、组织、写页面——就像你雇了一个人，把一堆资料整理成一份干净的知识库，然后把成品交回给你。整个过程是异步的，你可以等它跑完，也可以拿到 `task_id` 之后去做别的事。

换句话说：**你负责给材料和目标，Agent 负责真正把知识整理出来。** 

## 一条命令跑起来

```bash
ov compile \
  --from viking://resources/research \
  --to viking://resources/research-wiki \
  --skill viking://agent/skills/llm-wiki \
  --reason "把研究资料整理成便于团队检索的知识库" \
  --wait
```

`--wait` 会一直轮询到任务结束；去掉它就立刻返回一个 `cmp_...` 任务 ID，之后用 `ov task status <id>` 查看进度、用 `ov task cancel <id>` 取消。完整的字段说明、任务生命周期和 HTTP 接口见 [VikingBot API → compile()](../api/24-vikingbot.md#compile)。

## 换个 Skill，就换一种产物

Compile 本身不规定「编译成什么」——那由 Skill 决定。同一批来源，配不同的 Skill，就能得到形态完全不同的知识产物。下面是我们提供的示例 Skill，前两个还各自配了一个可视化脚本，可以直接照着跑：

| Skill | 产物形态 | 适合 | 示例 |
|-------|---------|------|------|
| **LLM Wiki** | 一套互相链接的 Markdown 页面（实体页、概念页、方法页……）加一个导航 `index.md` | 需要人和 Agent 都能快速检索、导航、复用的知识库 | [LLM Wiki 示例](./02-llm-wiki.md) |
| **Knowledge Graph** | `entities/*.md` 节点 + 一个 `relations.jsonl` 关系表 | 需要按实体、类型、关系去遍历的结构化知识图谱 | [Knowledge Graph 示例](./03-knowledge-graph.md) |
| **日报** | 每个日期一页 `<YYYY-MM-DD>.md` | 从对话、会话、消息、任务记录里还原「每天真正做了什么」 | [日报示例](./04-daily-report.md) |
| **知识蒸馏** | 按主题组织的高层次结论页 | 从一个或多个知识库里提炼跨来源的发现、趋势、变化 | [知识蒸馏示例](./05-knowledge-distillation.md) |

前两个示例还给出了从**导入来源 → 添加 Skill → 执行编译 → 可视化产物**的完整 `ov` 命令，照着做就能得到一张可交互的 HTML 图。

## 前置条件

- 一个正在运行、且启用了 Bot（`--with-bot`）的 OpenViking 服务。默认端点是 `http://localhost:1933`；远程使用需要 API Key，参见 [鉴权](../guides/04-authentication.md)。没有服务先看 [快速开始](../getting-started/02-quickstart.md)。
- `ov` CLI 已配置好连接（`~/.openviking/ovcli.conf` 或 `OPENVIKING_*` 环境变量）。
- 可视化脚本需要 Python 3；LLM Wiki 的脚本还会用到 `openviking` Python 包来直接读取服务里的 Wiki 页面。

## 相关文档

- [VikingBot 概念](../concepts/15-vikingbot.md) — Compile 背后的执行体
- [VikingBot API](../api/24-vikingbot.md) — `compile()` / `compile_status()` / `compile_cancel()` 的完整参考
- [Skills API](../api/04-skills.md) — 如何管理和自定义 Skill
