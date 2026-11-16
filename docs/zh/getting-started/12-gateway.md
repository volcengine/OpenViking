# 网关接入

OpenViking 网关连接客户端与模型服务，在转发请求时补充相关记忆，并将对话保存到 OpenViking，供后续提取和复用。对于支持自定义 Base URL 和 API Key 的兼容客户端，可以通过修改连接配置接入记忆能力。

本节使用一个 Python 样例完成 **连接网关 → 发送对话 → 查看记忆 → 在新会话中使用**：先告诉模型虚构“山岚项目”的评审约定，再开启另一段对话，查看回答是否沿用这些约定。

::: info Beta
OpenViking 网关目前处于 Beta 阶段，后续可能调整接口和配置方式。升级时，请结合更新说明核对现有接入配置。
:::

## 准备环境

本节采用本机服务，开始前请准备：

- **OpenViking 服务**：按[快速开始](02-quickstart.md#本地服务)完成安装和模型配置，确保 Embedding 和 VLM 可用。网关包含在 OpenViking 中，启用后与资料服务共用端口。
- **Python 3**：用于运行样例，安装方法见[准备本地运行环境](02-quickstart.md#准备本地运行环境)。样例只使用 Python 标准库。
- **CLI 连接**：按 [CLI 入门](05-cli-setup.md)连接本机服务，供后续查看会话和记忆。CLI 与网关密钥应对应同一 OpenViking 用户；默认本机模式使用 `default` 用户。
- **上游模型**：准备支持 **OpenAI Chat Completions** 协议的模型服务地址、模型名和 API Key，供本节样例发送请求。

以下操作会调用上游模型，并把虚构项目约定写入当前用户的记忆；记忆提取也会调用服务端配置的模型，按所选服务计费。远程服务的身份与权限配置见[网关快速上手](../gateway/02-quickstart.md#remote-server-api-keys)。

## 开启本机网关

运行初始化向导，在已有配置的菜单中选择 **Update gateway**，启用网关：

```bash
openviking-server init
```

保存配置后，在原服务终端停止服务，再使用原来的启动命令启动。例如：

```bash
openviking-server
```

如果原服务还启用了 VikingBot，启动时保留 `--with-bot` 等已有参数。

打开 [Studio](http://127.0.0.1:1933/studio/)，在侧边栏选择 **OpenViking 网关**。本机网关地址为 `http://127.0.0.1:1933/gateway`，后续样例会使用这个地址。

以 Studio 显示的网关地址为准。下方截图使用本机端口 `1934`；如果你的服务也使用其他端口，请同步替换后续命令中的地址。点击截图可查看原图。

## 配置模型与记忆

在 Studio 中依次准备上游、上下文配置和网关密钥：

### 配置上游

上游是网关转发请求时使用的模型服务。在“上游”标签页，选择或添加支持 **Chat Completions** 的上游，填写模型服务地址和凭据，并通过页面的测试功能确认连接或模型可用。

网关可能已从服务端 VLM 配置导入上游。可以直接检查并使用匹配的配置；导入的 Coding Plan 上游按页面提示完成确认。网关按请求协议转发，本节样例使用 Chat Completions，所选上游也需要支持该协议。

在上游编辑页选择 **Chat Completions**，根据模型服务提供的信息填写 **Base URL**，并核对下方显示的完整请求地址。

[![Studio 上游编辑页：选择 Chat Completions 并核对 Base URL](/quickstart/gateway/upstream-zh.jpg)](/quickstart/gateway/upstream-zh.jpg)

### 设置上下文

上下文配置决定如何召回记忆和保存对话。在“上下文配置”标签页新建一份配置，例如命名为“山岚项目体验”，使用以下设置：

| 设置 | 本节取值 | 作用 |
| --- | --- | --- |
| 召回记忆 | 开启 | 为新消息补充相关记忆 |
| 检索范围 | 记忆 | 使用已保存的项目约定 |
| 保存对话 | 开启 | 将对话写入 OpenViking，供后续提取记忆 |
| 最新回复等待时长 | 30 秒 | 对话空闲后保存最后一轮并提交会话 |
| 保留最近消息 | 0 | 让这段短对话的消息参与本次归档与记忆提取 |
| 显示召回摘要 | 开启 | 在回复中查看补充记忆的摘要 |
| OpenViking 工具 | 关闭 | 通过自动召回和保存完成本次流程 |

在配置编辑页选择“召回记忆”，开启“显示召回摘要”，并在“检索范围”中勾选“记忆”。

[![Studio 召回记忆设置：开启召回摘要，检索范围选择记忆](/quickstart/gateway/recall-zh.jpg)](/quickstart/gateway/recall-zh.jpg)

接着选择“保存对话”，将“最新回复等待时长”设为 **30 秒**，“保留最近消息”设为 **0**。完成后，在“OpenViking 工具”分组关闭工具并保存配置。

[![Studio 保存对话设置：等待时长为 30 秒，保留最近消息为 0](/quickstart/gateway/capture-zh.jpg)](/quickstart/gateway/capture-zh.jpg)

保存配置后，用它签发下面的密钥并开始新对话。配置修改对之后开始的对话生效；日常使用时，可以按任务需要调整等待时间和消息保留策略，详见[上下文配置](../gateway/08-context-profiles.md)。

### 签发密钥

在“密钥”标签页签发一把用于本次体验的网关密钥，选择刚才的上下文配置和上游。复制弹窗显示的完整 `ovgw_…` 值，供样例连接使用；完整密钥仅在签发时显示一次。

在签发表单中填写名称，核对“给谁用”，并选择已准备的上下文配置与上游。如果上游显示“已拦截”，先返回上游页按提示核对凭据和套餐适用条件，确认可用后再发送请求。

[![Studio 签发网关密钥表单：填写名称，选择上下文配置和上游](/quickstart/gateway/key-zh.jpg)](/quickstart/gateway/key-zh.jpg)

客户端使用这把网关密钥访问模型，模型服务商的 API Key 保存在上游配置中。查看会话和记忆的 `ov` CLI 继续使用原有的 OpenViking 连接配置。

## 发送两轮对话

下载 [Python 样例](/quickstart/gateway-demo-v1.zip)，在终端中进入下载目录并解压：

```bash
unzip gateway-demo-v1.zip
```

解压后得到 `gateway-demo.py`。下面的命令适用于 macOS / Linux 终端，先设置网关地址和模型名。将 `<上游模型 ID>` 替换为刚才上游中可用的模型名：

```bash
export GATEWAY_URL=http://127.0.0.1:1933/gateway
export GATEWAY_MODEL='<上游模型 ID>'
```

运行下面的命令后输入网关密钥并回车，输入过程不会显示密钥字符：

```bash
read -r -s GATEWAY_KEY
export GATEWAY_KEY
```

发送两轮对话，向模型提供山岚项目的约定：评审固定检查四项风险、报告使用 Markdown、文件名为 `mist-review.md`。

```bash
python3 gateway-demo.py capture --language zh --run v1
```

样例先发送约定，再携带完整的第一轮消息发送“谢谢”。终端应依次显示两次回复。第二次请求中的历史用于保存第一轮；最后一轮会在对话空闲达到配置的等待时长后保存并提交。

在 Studio 的“请求日志”中确认两次请求成功，再到“会话”页面找到包含山岚项目约定的 `gateway-…` 会话。以服务实际生成的会话 ID 为准，后续 CLI 检查使用这个 ID。

<a id="等待记忆并开启新会话"></a>

## 查看生成的记忆

停止发送消息后，网关会按配置等待 30 秒再提交最后一轮，后台记忆提取还需要额外时间。可以每隔 10 秒查看一次，先等待最多 5 分钟。仍在处理时保留会话和任务 ID，稍后继续检查；出现失败或取消状态时，查看对应错误或原因。

先列出会话，并将 `<gateway-session-id>` 替换为 Studio 中找到的会话 ID，查看提交情况：

```bash
ov session list
ov session get <gateway-session-id>
```

再查看记忆提取任务。找到 `resource_id` 与该会话 ID 对应的任务，将其 `task_id` 填入下一条命令：

```bash
ov task list --task-type session_commit
ov task status <task_id>
```

任务状态为 `completed` 后，从任务结果中复制 `archive_uri`，查看记忆变更：

```bash
ov read "<archive_uri>/memory_diff.json"
```

从 `operations.adds` 或 `operations.updates` 中复制记忆的 `uri`，读取正文：

```bash
ov read "<记忆 URI>"
```

确认正文包含四项风险、Markdown 格式和 `mist-review.md` 三项约定后，再开启新会话。记忆的名称和分类以实际生成结果为准。

## 在新会话中使用

在同一终端中运行召回阶段，沿用网关地址、密钥和模型设置：

```bash
python3 gateway-demo.py recall --language zh --run v1
```

这条命令启动新的 Python 进程，并使用不同的会话标识。它只发送“按已保存的山岚项目约定准备评审清单，并给出交付文件名”的任务，具体约定由网关从记忆中补充。

查看回复是否包含四项风险的检查位置、使用 Markdown，并给出文件名 `mist-review.md`。样例没有提供具体风险内容，回答可以列为待补充，或明确标注为建议。

<a id="检查与收尾"></a>

## 检查结果

结合记忆正文、回复中的召回摘要和 Studio 请求日志，分别核对以下结果：

| 环节 | 预期结果 |
| --- | --- |
| 请求 | 两次模型请求成功，终端收到回复 |
| 保存 | 对应会话及其归档中包含项目约定 |
| 提取 | 任务完成，记忆正文保留三项约定 |
| 召回与使用 | 新会话有召回记录，回答遵循约定 |

回复措辞可以不同。核对时既要看记忆是否被召回，也要看回答是否使用了其中的信息。

## 常见问题

| 现象 | 处理方法 |
| --- | --- |
| 请求失败或模型不可用 | 检查网关地址、密钥绑定的上游、模型名和 Chat Completions 协议，并查看请求日志 |
| Studio 中找不到会话 | 检查保存对话开关，确认当前用户与网关密钥绑定用户一致，并查看请求的保存状态 |
| 有会话但没有生成记忆 | 检查空闲等待时间、保留最近消息数和 `session_commit` 任务状态 |
| 已生成记忆但没有召回 | 核对密钥绑定的用户、上下文配置和检索范围；修改配置后用新会话重试 |
| 有召回记录但回答遗漏约定 | 对照记忆正文和回复，请模型根据已保存约定修订 |

更多问题见[网关排障](../gateway/12-troubleshooting.md)。

## 继续使用

完成后，可以按[客户端接入](../gateway/03-connect-clients.md)将自己的应用连接到网关。远程服务和团队配置见[网关快速上手](../gateway/02-quickstart.md)。

再次体验时，为两条样例命令使用相同的新 `--run` 值，以创建新的一组会话。相同用户的已有记忆仍可被召回；如需从空白记忆开始，可使用单独的演示用户。

体验结束后，可在 Studio 中撤销本次网关密钥，并根据会话和 `memory_diff.json` 按需清理演示数据。若本次更新了已有记忆，先核对修改内容，保留仍需使用的信息。
