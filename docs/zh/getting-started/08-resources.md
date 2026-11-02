<a id="资源检索"></a>

# 资料检索

项目说明、会议记录和反馈流程往往分散在多份文档中。将这些资料导入 OpenViking 后，可以按问题的含义检索相关内容，再让 Agent 读取原文，综合回答并标注来源。

本节使用四份虚构的“灯塔项目”资料，带你完成 **导入资料 → 检索 → 读取原文 → 生成回答 → 核对来源**。你会分别查看只需一份资料就能回答、需要综合多份资料，以及资料尚未给出答案的三类问题。

## 准备环境

开始前，请准备以下环境：

- **OpenViking 服务和 CLI**：按照[快速开始](02-quickstart.md)完成连接配置，当前账号需要具备目标目录的导入和读取权限。
- **VikingBot**：本节通过 `ov chat` 生成回答。请确认服务已启用 VikingBot，并完成模型配置，具体方法见 [VikingBot](11-vikingbot.md)。CLI 和 VikingBot 应连接同一服务，并能访问同一份资料。
- **模型配置**：资料处理、语义检索和生成回答会使用已配置的模型，并产生相应的模型调用费用。

如果使用自己的 Agent，可以按[快速开始中的接入方式](02-quickstart.md#让-agent-生成回答)连接资料服务，再使用下文的问题和核对标准。

## 准备资料

下载[灯塔项目资料包](/quickstart/lighthouse-zh-v1.zip)，在终端中进入下载文件所在的目录，再解压：

```bash
unzip lighthouse-zh-v1.zip
```

解压后得到 `lighthouse-zh/` 目录，其中包含以下文件：

| 文件 | 内容 |
| --- | --- |
| `project.md` | 试点的网络条件、离线安装包和本地存储准备 |
| `feedback.md` | 反馈导出时间、责任人和交付方式 |
| `meeting.md` | 反馈登记模板、导出演练和待确认事项 |
| `limits.md` | 资料覆盖范围及尚未提供的信息 |

## 导入资料

将整个目录导入独立的演示路径。`--wait` 会等待资料处理完成，便于后续检索。再次体验时，可将 `onboarding-zh-v1` 换成新的目录名，并同步修改后续命令中的 URI。

```bash
ov add-resource ./lighthouse-zh --to viking://resources/onboarding-zh-v1 --wait
```

处理成功后，查看导入结果：

```bash
ov tree viking://resources/onboarding-zh-v1
```

这组 Markdown 文件处理后会分别放入同名目录。确认目录中包含以下文件，再继续检索：

```text
viking://resources/onboarding-zh-v1/
├── project/project.md
├── feedback/feedback.md
├── meeting/meeting.md
└── limits/limits.md
```

如果等待超时，可用返回的任务 ID 执行 `ov task status <task_id>` 查看进度。任务完成后继续；任务失败时，根据错误信息检查模型或连接配置。任务状态说明见[任务管理](../api/17-tasks.md)。

<a id="检索与回答"></a>

## 检索资料

先提出一个需要综合项目说明和会议记录的问题，将检索范围限定在刚才导入的目录：

```bash
ov find "灯塔项目怎样收集反馈，开始试点前要准备什么？" --uri viking://resources/onboarding-zh-v1
```

检索结果会列出匹配内容及其 URI。查看命中内容是否涉及反馈收集和试点准备，再读取相关原文。`ov find` 用于找到相关资料，下一步的原文读取用于确认具体依据。

## 读取原文

根据检索结果中的 URI 打开来源文件。这组样例中，与问题相关的文件包括项目说明、会议记录和反馈流程：

```bash
ov read viking://resources/onboarding-zh-v1/project/project.md
ov read viking://resources/onboarding-zh-v1/meeting/meeting.md
ov read viking://resources/onboarding-zh-v1/feedback/feedback.md
```

阅读时可以留意：安装包和本地目录写在项目说明中，登记模板和导出演练写在会议记录中。要回答完整的试点准备要求，需要结合这两份资料。

## 生成回答

把检索、读取和回答交给 VikingBot。在新会话中提出下面三个问题，并要求它为结论提供来源、明确标注未知信息。重复体验时，可以将会话名换成新的名称。

```bash
ov chat --session lighthouse-resources-v1 -m "请只使用 viking://resources/onboarding-zh-v1 的资料，先进行语义检索，再读取相关原文，依次回答：反馈由谁在什么时候导出？试点开始前要准备什么？试点何时结束、预算多少？每项结论列出完整来源 URI，资料未说明的内容请标为尚未确定。"
```

回答应包含反馈导出安排、试点准备事项和待确认信息，并为相关结论附上完整的 `viking://` 来源 URI。

## 核对来源

使用 `ov read` 打开回答引用的文件，逐项核对下表。回答措辞可以不同，事实和来源应与原文一致。

| 问题类型 | 问题 | 预期事实 | 来源 |
| --- | --- | --- | --- |
| 单份资料 | 谁在什么时候导出反馈？ | 试点管理员每周五导出，并交给项目负责人 | `feedback/feedback.md` |
| 多份资料 | 试点开始前要准备什么？ | 离线安装包、本地存储目录、反馈登记模板、一次离线导出演练 | `project/project.md`、`meeting/meeting.md` |
| 尚无答案 | 试点何时结束、预算多少？ | 两项均尚未确定 | `meeting/meeting.md` |

结合 `ov chat` 的工具调用输出或 Bot 日志，确认 Agent 检索了相关资料、读取了来源文件，并在回答中使用了这些内容。对于多份资料的问题，检查回答是否遗漏其中一份的要求；对于尚未确定的事项，检查是否保留了原文中的未知状态。

## 常见问题

| 现象 | 处理方法 |
| --- | --- |
| 导入目录中缺少文件 | 检查解压目录是否包含四份资料，并查看导入结果和任务错误 |
| 检索没有相关结果 | 确认处理已完成、查询 URI 与导入目录一致，再尝试用更具体的问题检索 |
| CLI 可以读取，Agent 无法访问 | 核对两者连接的服务、账号和目录访问权限 |
| 检索已命中，但回答遗漏内容 | 查看 Agent 实际读取的文件，请它补读相关来源并完善回答 |
| 回答给出具体结束日期或预算 | 对照会议记录，请 Agent 将缺少依据的结论修正为“尚未确定” |

## 换成自己的资料

选择一组你熟悉的项目资料，分别准备单份资料、多份资料和资料未覆盖的问题，按同样的流程检索与核对。可以使用[效果评估](06-evaluate.md)中的记录表，比较检索结果、原文读取和回答质量。支持的格式、导入选项和增量处理方式见[资料管理](../api/02-resources.md)。

样例目录还可用于后续的[上下文编译](13-compile.md)和[导入导出](15-transfer.md)。如需清理，可在 Studio 中确认目录 URI 后删除该演示目录。
