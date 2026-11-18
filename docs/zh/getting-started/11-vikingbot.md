# VikingBot

VikingBot 是 OpenViking 内置的 AI Agent，可以接收任务、调用模型和工具，并结合 OpenViking 中的资料、记忆和技能生成回答。你可以通过命令行与它对话，整理任务、查询项目资料，或按照技能完成重复工作。

本节采用本机运行方式，带你启动 VikingBot、整理一份任务清单，再在同一会话中继续追问。完成后，你可以确认服务连接正常、模型能够回复，以及后续对话能沿用已有上下文。

## 准备环境

开始前，请准备以下环境：

| 准备项 | 用途与准备方式 |
| --- | --- |
| uv 和 Python | 安装并运行 OpenViking 与 VikingBot。以下命令使用 Python 3.13，安装方法见[准备本地运行环境](02-quickstart.md#准备本地运行环境) |
| Node.js/npm | 安装 `ov` CLI，安装方法见[安装 Node.js](02-quickstart.md#安装-node-js) |
| 模型访问凭据 | 准备 Embedding 模型和支持工具调用的 VLM 的模型名、端点及 API Key，开通方法见[模型开通指南](../guides/02-volcengine-purchase-guide.md) |

VikingBot 默认使用 OpenViking 配置中的 VLM，Embedding 模型用于后续资料检索。使用 Agent Plan 时，选择套餐支持的模型与端点；模型调用按所选服务计费。

如果已经按快速开始启动了带 VikingBot 的本机服务，可以直接进入[连接 CLI](#连接-cli)。

<a id="启动与连接"></a>

## 启动服务

安装 OpenViking 和 VikingBot，让本机具备资料管理和 Agent 对话能力：

```bash
uv tool install --python 3.13 "openviking[bot]" --upgrade
```

首次使用时，运行初始化向导，选择本机访问并填写准备好的模型信息。已有配置时，可以直接进行配置检查。

```bash
openviking-server init
```

检查模型和服务配置。如果报告错误，按提示修正对应项目后再次检查：

```bash
openviking-server doctor
```

已有服务启用了 API Key 认证时，可先按[身份认证](../guides/04-authentication.md)为 Bot 配置连接身份，并为 CLI 准备相应的 User/Admin Key。

检查通过后，一起启动 OpenViking 和 VikingBot。`--with-bot` 会启用 Agent 对话入口；保持这个终端运行，后续命令在另一个终端中执行。如果本机服务已在运行但尚未启用 Bot，可先在原终端停止服务，再使用下面的命令启动。

```bash
openviking-server --with-bot
```

本节后续命令使用默认地址 `http://127.0.0.1:1933`。

## 连接 CLI

在新终端中安装 CLI。已有可用的 `ov` 时，可以直接配置连接。

```bash
npm install -g @openviking/cli
```

将界面语言设为中文，再打开连接向导：

```bash
ov language zh-CN
ov config
```

选择自定义（Custom）连接，URL 填写 `http://127.0.0.1:1933`，默认本机模式的密钥留空；已启用认证时，填写准备好的 User/Admin Key。保存并设为当前配置；已有对应连接时，直接选择它即可。更多连接方式见 [CLI 入门](05-cli-setup.md)。

先确认 CLI 能连接资料服务：

```bash
ov health
```

结果中应包含 `healthy: true`。接着检查 Bot 对话入口：

```bash
curl -f http://127.0.0.1:1933/bot/v1/health
```

返回 `status: healthy` 后，可以开始对话。接下来的任务会进一步确认模型能否正常生成回复。

<a id="完成任务并追问"></a>

## 完成一次任务

将一份虚构的任务清单交给 VikingBot，让它按完成状态整理。`--session` 指定本次会话，后续使用同一名称可以继续讨论这份清单。重复体验时，可将示例中的会话名换成新的名称。

```bash
ov chat --session bot-review-v1 -m "以下是虚构任务清单：资料导入已完成，来源核对待完成，演示排练待完成。请整理为简短的已完成和待完成清单。"
```

回复应将“资料导入”列为已完成，将“来源核对”和“演示排练”列为待完成。你可以对照输入检查状态是否保留准确；具体措辞和排版可以不同。

## 继续追问

保持同一个 `--session`，请 VikingBot 根据已有清单建议下一步：

```bash
ov chat --session bot-review-v1 -m "沿用刚才的清单，建议先完成哪一步，并用一句话说明理由。"
```

这次无需重新提供任务清单，回复应围绕“来源核对”和“演示排练”提出建议。例如，可以建议先核对来源，以便在演示前确认内容准确。建议顺序可以不同，依据应来自前一轮的待办事项。

同一会话中的追问会使用已有对话上下文。使用新的会话名称可以开始另一段对话；希望在不同会话中复用项目背景或偏好时，可继续[记忆使用](09-memory.md)，了解记忆的保存与召回。

## 检查结果

| 检查项 | 预期结果 |
| --- | --- |
| 服务连接 | `ov health` 和 Bot 健康检查均成功 |
| 单次任务 | 回复准确保留三项任务及其完成状态 |
| 连续对话 | 追问回复使用前一轮的待办信息，并说明建议理由 |

如果回复遗漏了某项任务或改变了完成状态，可以指出差异，请 VikingBot 根据原始清单修订。

## 常见问题

| 现象 | 处理方法 |
| --- | --- |
| 安装后找不到 `openviking-server` 或 `ov` | 重新打开终端；仍不可用时，按 uv 或 npm 的安装说明检查命令路径 |
| 提示端口已被占用 | 确认是否已有本机服务；需要启用 Bot 时，在原服务终端停止后再带 `--with-bot` 启动 |
| `ov health` 连接失败 | 在 `ov config` 中确认当前连接地址，并检查服务终端是否仍在运行 |
| Bot 健康检查返回 `503` | 确认启动时启用了 `--with-bot`，并查看服务日志中的 Bot 启动信息 |
| 健康检查成功，但模型调用失败 | 根据错误信息检查模型名、端点、凭据和套餐支持情况 |
| 追问没有沿用清单 | 确认两次命令使用相同的 `--session` 和 CLI 连接身份，并确认第一轮已返回完整回复 |

## 继续使用

连接和对话正常后，可以让 VikingBot 使用 OpenViking 中的上下文完成任务：

| 目标 | 下一步 |
| --- | --- |
| 根据项目资料回答，并核对来源 | [资料检索](08-resources.md) |
| 在新会话中复用背景和偏好 | [记忆使用](09-memory.md) |
| 按固定工作方法处理不同输入 | [技能使用](10-skills.md) |

接入飞书等聊天平台、独立调试或配置模型时，可查看 [VikingBot 指南](../guides/17-vikingbot.md)；在程序中发送消息可查看 [Bot API](../api/24-vikingbot.md)。VikingBot Gateway 承接 Bot 消息和聊天渠道；为兼容客户端提供模型请求入口的能力见 [OpenViking 网关](12-gateway.md)。

## 结束体验

保留会话名称，便于之后继续讨论。本机服务可在运行终端按 Ctrl+C 停止，停止后 CLI 对话入口也会随之关闭。需要清理测试会话时，可在 Studio 中确认对应会话后删除。
