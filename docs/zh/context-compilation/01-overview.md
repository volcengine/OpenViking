# 上下文编译概览

`ov compile` 读取 OpenViking 中的文档、笔记、网页或会话记录，按指定 Skill 整理成 Wiki、知识图谱、日报等内容，并写回 OpenViking。

## 它是怎么工作的

每次编译需要指定：

- `--from`：一个或多个来源目录或文件。
- `--to`：输出目录。
- `--skill`：已安装的 Skill URI，定义输出内容和结构。

可选的 `--instruction` 用来补充本次任务的范围、受众、语言、侧重点或日期。

编译由服务端配置的 [Agent Runtime](../api/23-agent-runtime.md) 执行，本地部署可使用内置 [VikingBot](../concepts/15-vikingbot.md)。它以请求用户的身份读取来源和 Skill，在独立的 Agent Loop 中整理并写入内容。任务异步运行，返回 `task_id` 后可查询进度和结果。

## 一条命令跑起来

先按 [LLM Wiki 示例](02-llm-wiki.md) 导入来源、安装 Skill，再执行：

```bash
ov compile \
  --from viking://resources/research \
  --to viking://resources/research-wiki \
  --skill viking://agent/skills/llm-wiki \
  --instruction "把研究资料整理成便于团队检索的知识库"
```

命令会立即返回一个 `cmp_...` 任务 ID，之后用 `ov task status <id>` 查看进度、用 `ov task cancel <id>` 取消。完整的字段说明、任务生命周期和 HTTP 接口见 [Agent Runtime API](../api/23-agent-runtime.md)。

## 换个 Skill，就换一种产物

Skill 决定输出内容和结构。仓库提供以下示例，其中 LLM Wiki 和 Knowledge Graph 还包含可视化脚本：

| Skill | 产物形态 | 适合 | 示例 |
|-------|---------|------|------|
| **LLM Wiki** | 一套互相链接的 Markdown 页面（实体页、概念页、方法页……）加一个导航 `index.md` | 需要人和 Agent 都能快速检索、导航、复用的知识库 | [LLM Wiki 示例](./02-llm-wiki.md) |
| **Knowledge Graph** | `entities/*.md` 节点 + 一个 `relations.jsonl` 关系表 | 需要按实体、类型、关系去遍历的结构化知识图谱 | [Knowledge Graph 示例](./03-knowledge-graph.md) |
| **日报** | 每个日期一页 `<YYYY-MM-DD>.md` | 从对话、会话、消息、任务记录里还原「每天真正做了什么」 | [日报示例](./04-daily-report.md) |
| **知识蒸馏** | 按主题组织的高层次结论页 | 从一个或多个知识库里提炼跨来源的发现、趋势、变化 | [知识蒸馏示例](./05-knowledge-distillation.md) |

前两个示例还给出了从**导入来源 → 添加 Skill → 执行编译 → 可视化产物**的完整 `ov` 命令，照着做就能得到一张可交互的 HTML 图。

## 前置条件

- 一个已配置 Compile Runtime 的 OpenViking 服务；本地示例可通过 `--with-bot` 启用内置 VikingBot。默认端点是 `http://localhost:1933`；远程使用需要 API Key，参见 [鉴权](../guides/04-authentication.md)。没有服务先看 [快速开始](../getting-started/02-quickstart.md)。
- `ov` CLI 已配置好连接（`~/.openviking/ovcli.conf`，或由 `OPENVIKING_CLI_CONFIG_FILE` 指定的文件）。
- 示例中的 `examples/...` 是仓库相对路径。先下载 [OpenViking 仓库](https://github.com/volcengine/OpenViking)，在仓库根目录运行命令。
- 可视化脚本需要 Python 3。LLM Wiki 的 Python 依赖见[脚本说明](https://github.com/volcengine/OpenViking/tree/main/examples/compile/graph-show/llm-wiki)。

## 相关文档

- [VikingBot 概念](../concepts/15-vikingbot.md) — 内置 Compile 执行端
- [Agent Runtime API](../api/23-agent-runtime.md) — 创建、查询和取消 Compile 任务的完整参考
- [Skills API](../api/04-skills.md) — 如何管理和自定义 Skill
