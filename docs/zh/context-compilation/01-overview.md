# 上下文编译概览

`ov compile` 读取 OpenViking 中的文档、笔记、网页或会话记录，按指定 Skill 整理成 Wiki、知识图谱、日报等内容，并写回 OpenViking。

## 它是怎么工作的

使用已安装 Skill 编译时，需要指定：

- `--from`：一个或多个来源目录或文件。
- `--to`：输出目录。
- `--skill`：已安装的 Skill URI，定义输出内容和结构。

可选的 `--instruction` 用来补充本次任务的范围、受众、语言、侧重点或日期。

编译由服务端配置的 [Agent Runtime](../api/23-agent-runtime.md) 执行，本地部署可使用内置 [VikingBot](../concepts/15-vikingbot.md)。它以请求用户的身份读取来源和 Skill，在独立的 Agent Loop 中整理并写入内容。任务异步运行，返回 `task_id` 后可查询进度和结果。

### 图片来源与模型

来源目录包含 PNG、JPEG、GIF 或 WebP 时，内置 VikingBot 会保留原图、来源 URI 和内容哈希，按需将图片证据交给模型；不会把图片二进制当文本编译。需要选择支持图片输入的模型，不能仅凭 provider 支持工具调用判断模型能看图。

图片传输方式由 VLM 后端声明，不按 provider 名称或模型名称放行。Anthropic 协议和 OpenAI Codex 使用原生多模态工具结果；OpenAI Chat Completions 后端及其方舟、Azure、Kimi、GLM 子类将工具结果保留为文本，并在同一请求中附带图片消息，通过工具调用 ID 和图片序号关联来源。

失败策略分层处理：**协议明确、模型视觉能力未知时允许正常带图请求**，不要求新模型先进入名单，也不额外发探测请求；服务端明确拒绝图片时，编译报错。**协议或路由未明确、图片未完整传递时停止编译**，不会跳过图片后报告成功。允许尝试可能带来失败请求的延迟和费用；请求成功也不等于已验证模型正确理解了图片。

LiteLLM 的显式 `provider` 优先；使用 `provider: "litellm"` 时，图片输入需要已识别的 `provider/model` 前缀，例如 `openai/future-vision-model`、`anthropic/claude-sonnet-4-5`。仅有裸模型名或 `api_base` 不足以声明图片协议，不能靠 `claude`、`qwen` 等名字片段授予传图能力。文本请求继续保留原有自动推断。已有依赖裸模型名自动推断的 LiteLLM 图片编译配置需要补上协议前缀。

LiteLLM 根据本地实际路由的协议适配器判断能力：OpenAI 兼容协议（如通义、Moonshot、智谱 `zai`、OpenRouter、NVIDIA NIM、vLLM）、Gemini/Vertex Gemini、Ollama 和 Bedrock Converse 使用图片消息；Anthropic 协议保留原生工具结果。智谱旧 `zhipu/` 前缀会规范为当前 LiteLLM 使用的 `zai/`。能力检查不调用凭据解析或登录流程。纯文本 Completion API 和尚未验证的协议继续拒绝图片证据，不能静默退回二进制文本。

图片消息会排在同一轮所有工具结果之后；转换仅发生在 provider 请求边界，不改写任务证据或会话历史。主备或多凭据链上的每个后端都必须支持图片证据传递；包含图片消息传输方式的混合链统一使用图片消息。

此适配不增加模型请求、下载或依赖，只增加本地协议元数据检查和少量关联文字；模型仍会按图片输入计费。现有图片格式、数量、字节预算和来源哈希校验继续生效。传输协议支持图片不代表具体模型支持图片，尤其不能据此给纯文本模型宣称视觉能力。

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

## 使用 Web Studio

Studio 提供 `/compile` 列表、`/compile/new` 新建表单和 `/compile/tasks/<task_id>` 任务详情页。可以选择并预览 Skill、浏览来源目录、提交多个来源、查看或取消任务，以及打开产物目录。文件系统终端也支持 `compile` 和 `task`，并可将参数带入表单。

任务列表游标使用进程内密钥签名，并绑定调用者和筛选条件。服务重启后旧游标失效，刷新列表即可重新开始。一个副本签发的游标不能直接用于另一个副本。分页扫描已存储的任务记录，不依赖索引，读取成本仍随保留的历史数量增长。

重试 HTTP 提交时，复用相同的 `Idempotency-Key` 和请求参数；同一 Key 对应的参数变化时返回 `409`。提交恢复只在任务记录保留期间有效：完成或取消的记录在最后一次更新后保留 24 小时，失败记录保留 7 天。这不是永久提交历史，也不保证跨实例 exactly-once。提交恢复见 [Agent Runtime](../api/23-agent-runtime.md)，任务操作见[任务管理](../api/17-tasks.md)。

## 换个 Skill，就换一种产物

Skill 决定输出内容和结构。仓库提供以下示例，其中 LLM Wiki 和 Knowledge Graph 还包含可视化脚本：

| Skill | 产物形态 | 适合 | 示例 |
|-------|---------|------|------|
| **LLM Wiki** | 一套互相链接的 Markdown 页面（实体页、概念页、方法页……）加一个导航 `index.md` | 需要人和 Agent 都能快速检索、导航、复用的知识库 | [LLM Wiki 示例](./02-llm-wiki.md) |
| **Knowledge Graph** | `entities/*.md` 节点 + 一个 `relations.jsonl` 关系表 | 需要按实体、类型、关系去遍历的结构化知识图谱 | [Knowledge Graph 示例](./03-knowledge-graph.md) |
| **日报** | 每个日期一页 `<YYYY-MM-DD>.md` | 从对话、会话、消息、任务记录里还原「每天真正做了什么」 | [日报示例](./04-daily-report.md) |
| **知识蒸馏** | 按主题组织的高层次结论页 | 从一个或多个知识库里提炼跨来源的发现、趋势、变化 | [知识蒸馏示例](./05-knowledge-distillation.md) |

前两个示例还给出了从**导入来源 → 添加 Skill → 执行编译 → 可视化产物**的完整 `ov` 命令，照着做就能得到一张可交互的 HTML 图。

## 不止生成新产物：整理已有记忆

除了用 Skill 把来源材料编译成新的知识产物，`ov compile` 还有一种特殊模式——把 `--skill` 设为 `memory`，即可对已有的**记忆**目录做就地整理（去重、合并、拆分、精简），且不经过 VikingBot。详见 [记忆整理](./06-memory-consolidation.md)。

## 前置条件

- 使用 Skill 的示例需要一个已配置 Compile Runtime 的 OpenViking 服务；本地使用需安装 `openviking[bot]` 依赖，并通过 `--with-bot` 启用内置 VikingBot。`--skill memory` 模式在进程内运行，不需要 Compile Runtime，也不传 `--from`。默认端点是 `http://localhost:1933`；远程使用需要 API Key，参见 [鉴权](../guides/04-authentication.md)。没有服务先看 [快速开始](../getting-started/02-quickstart.md)。
- `ov` CLI 已配置好连接（`~/.openviking/ovcli.conf`，或由 `OPENVIKING_CLI_CONFIG_FILE` 指定的文件）。
- 示例中的 `examples/...` 是仓库相对路径。先下载 [OpenViking 仓库](https://github.com/volcengine/OpenViking)，在仓库根目录运行命令。
- 可视化脚本需要 Python 3。[LLM Wiki 脚本](https://github.com/volcengine/OpenViking/blob/main/examples/compile/graph-show/llm-wiki/wiki_graph.py)还需要 `openviking` Python 包。

## 相关文档

- [VikingBot 概念](../concepts/15-vikingbot.md) — 内置 Compile 执行端
- [Agent Runtime API](../api/23-agent-runtime.md) — 创建、查询和取消 Compile 任务的完整参考
- [Skills API](../api/04-skills.md) — 如何管理和自定义 Skill
