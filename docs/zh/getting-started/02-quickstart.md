<a id="导入并检索第一份文档"></a>

# 快速开始

当你希望 AI Agent 根据项目资料回答问题时，需要先让它找到相关内容，再读取原文。OpenViking 是面向 Agent 的上下文数据库，负责保存和检索这些资料，让 Agent 能够根据来源生成回答。

接下来，你会导入一份虚构的“灯塔项目”说明，让 Agent 回答反馈收集的问题，并打开它引用的原文核对结果。你可以使用火山引擎云服务，也可以在本机运行 OpenViking：两条路径都会完成 **导入资料 → 检索与读取 → 生成回答 → 核对来源**。

## 准备工作

先选择运行方式，再安装对应的工具：

| 运行方式 | 适合的情况 | 需要准备 |
| --- | --- | --- |
| **火山引擎云服务** | 希望使用托管服务，并在已有 Agent 中提问 | Node.js/npm、火山引擎账号，以及能在本机执行终端命令的 Agent |
| **本地服务** | 希望在自己的电脑上运行，并直接使用内置 Agent VikingBot | Node.js/npm、uv、Python，以及模型访问凭据 |

### 安装 Node.js

两条路径都通过 `ov` CLI 操作资料。打开 [Node.js 下载页](https://nodejs.org/en/download)，选择与你的系统匹配的 **LTS** 版本，按页面提供的安装程序或命令完成安装；npm 会随 Node.js 一同安装。

安装后重新打开终端，确认两个命令都能输出版本号：

```bash
node --version
npm --version
```

使用云服务时，可以直接进入[开通或启动服务](#开通或启动服务)。

### 准备本地运行环境

本地服务使用 uv 安装 OpenViking，并使用 Python 运行。下面以 macOS / Linux 终端为例；其他系统的安装方式可查看 [uv 安装指南](https://docs.astral.sh/uv/getting-started/installation/)和[服务端部署](../guides/03-deployment.md)。

先安装 uv：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

重新打开终端，确认 uv 可用：

```bash
uv --version
```

再用 uv 安装 Python 3.13，供后续服务使用：

```bash
uv python install 3.13
```

已有 Python 3.13 时可直接使用。其他安装方式见 [Python 下载页](https://www.python.org/downloads/)和 [uv 的 Python 管理指南](https://docs.astral.sh/uv/guides/install-python/)。

最后，准备两个模型的调用信息：**Embedding 模型**用于资料向量化和检索，**支持工具调用的 VLM**用于内容理解和 VikingBot 回答。每个模型需要模型名、调用端点及 API Key；可按[火山引擎模型开通指南](../guides/02-volcengine-purchase-guide.md)取得这些信息。使用 Agent Plan 时，选择套餐支持的模型与端点。导入资料和生成回答会产生模型调用，按所选服务计费。

<a id="_1-启动服务"></a>

<a id="_1-选择服务"></a>

<a id="_1-开通或启动服务"></a>

## 开通或启动服务

选择下面的一条路径，完成后继续[连接 CLI](#连接-cli)。

<a id="火山引擎托管服务"></a>

### 火山引擎云服务

云服务负责运行 OpenViking 和处理资料，你的电脑只需安装客户端。

1. 打开 [OpenViking 控制台](https://console.volcengine.com/vikingdb/openviking/region:openviking+cn-beijing)，登录火山引擎账号，并按控制台提示开通服务。套餐与额度可在[服务说明](https://docs.volcengine.com/docs/84313/2374478)中查看。
2. 在控制台取得用于访问 OpenViking 的 **API Key**，供下一步配置 CLI 使用。
3. 准备一个能在本机执行终端命令的 Agent。在“提问并核对”中，通过它调用 `ov` 检索云端资料并生成回答；接入方式可查看 [CLI 入门](05-cli-setup.md)。

取得 API Key 后，继续[连接 CLI](#连接-cli)。

<a id="自建服务"></a>

### 本地服务

先安装 OpenViking 和 VikingBot，让本机同时具备资料管理和问答能力：

```bash
uv tool install --python 3.13 "openviking[bot]" --upgrade
```

接着运行初始化向导。选择本机访问，并填写准备好的 Embedding 和 VLM 模型信息；VikingBot 默认使用这里配置的 VLM。

```bash
openviking-server init
```

配置完成后，检查模型和服务配置。若检查报告错误，按提示修正对应项目后再次检查：

```bash
openviking-server doctor
```

检查通过后，启动服务和 VikingBot。保持这个终端运行，后续操作在另一个终端中进行。

```bash
openviking-server --with-bot
```

在新终端中确认资料服务和 Agent 都已就绪：

```bash
curl -f http://127.0.0.1:1933/health
curl -f http://127.0.0.1:1933/bot/v1/health
```

分别看到 `healthy: true` 和 Bot 的 `status: healthy` 后，继续连接 CLI。

<a id="_2-安装并连接-cli"></a>

<a id="_2-连接-cli"></a>

## 连接 CLI

安装 CLI，以便在终端中导入、检索和读取资料。已有 `ov` 时可以跳过安装命令。

```bash
npm install -g @openviking/cli
```

将界面语言设为中文，再打开连接向导：

```bash
ov language zh-CN
ov config
```

在向导中添加连接，按所选路径填写，然后保存并设为当前配置：

| 运行方式 | 连接类型 | 填写内容 |
| --- | --- | --- |
| 火山引擎云服务 | **OpenViking Service** | 按向导使用服务地址，输入控制台取得的 API Key |
| 本地服务 | **自定义（Custom）** | URL 填写 `http://127.0.0.1:1933`；默认本机模式的密钥留空 |

如果本地服务已有认证配置，使用对应的 User/Admin Key；配置方式见[身份认证](../guides/04-authentication.md)。

检查当前连接，成功后即可开始导入：

```bash
ov health
```

<a id="_3-导入文档"></a>

<a id="_3-导入样例"></a>

## 导入样例

<a href="/quickstart/lighthouse.zh.txt" download="lighthouse.zh.md">下载灯塔项目资料</a>，保存为 `lighthouse.zh.md`，然后在终端中进入该文件所在目录。资料包含试点的网络条件、反馈收集方式和未确定事项。

将它导入独立的演示目录，并通过 `--wait` 等待处理完成。再次体验时，可将 `lighthouse-demo` 换成一个新的目录名，并在后续命令中使用相同名称。

```bash
ov add-resource ./lighthouse.zh.md --to viking://resources/lighthouse-demo --wait
```

处理成功后，查看导入的文件：

```bash
ov tree viking://resources/lighthouse-demo
```

如果等待超时，使用返回的任务 ID 执行 `ov task status <task_id>` 查看进度。任务完成后继续；任务失败时，先根据错误信息修正模型或连接配置，再重试。任务状态说明见[任务管理](../api/17-tasks.md)。

<a id="_4-浏览与检索"></a>

<a id="_4-提问并核对"></a>

## 提问并核对

### 检索并读取资料

先查找与反馈收集相关的内容：

```bash
ov find "灯塔项目第一版怎样收集用户反馈？" --uri viking://resources/lighthouse-demo
```

结果中会包含匹配内容的 URI。复制其中的来源文件 URI，替换下面的占位值，打开原文：

```bash
ov read "<来源文件 URI>"
```

能读到“试点环境”和“反馈收集”等内容后，就可以让 Agent 使用这些资料回答问题。

### 让 Agent 生成回答

**使用云服务：** 在能够执行本机终端命令的 Agent 中发送下面的任务。让它使用刚才配置好的 CLI 连接；如果 Agent 运行在另一台机器或独立容器中，先在那里完成[CLI 连接配置](#连接-cli)。

```text
请使用本机已配置的 ov CLI，根据 OpenViking 中的项目资料回答问题。
先执行 ov health 确认连接，再执行：
ov find "灯塔项目第一版怎样收集用户反馈？" --uri viking://resources/lighthouse-demo
用 ov read 读取检索结果中的相关原文，然后回答：
灯塔项目第一版应怎样收集用户反馈，需要提前准备什么？
请只依据该目录中的资料，为关键结论列出来源文件的完整 viking:// URI，
并明确指出资料未说明的内容。
```

**使用本地服务：** 通过 `ov chat` 把同一个问题交给已启动的 VikingBot，它会调用检索和读取工具：

```bash
ov chat -m "请只使用 viking://resources/lighthouse-demo 中的资料，先按问题进行语义检索，再读取相关原文并回答：灯塔项目第一版应怎样收集用户反馈，需要提前准备什么？为关键结论列出来源文件的完整 viking:// URI；资料没有说明的部分请明确指出。"
```

### 核对回答的依据

打开回答引用的来源文件，逐项比较下面的事实。回答可以采用不同措辞，关键是结论与原文一致。

| 应包含的结论 | 原文依据 |
| --- | --- |
| 反馈保存在本地，由试点管理员每周导出并交给项目负责人 | “反馈收集” |
| 提前准备离线安装包和本地存储目录 | “试点环境” |
| 第一版没有云端同步，后续是否支持尚未决定 | “反馈收集” |

如果某项结论缺少来源，可以请 Agent 补充引用并重新核对。再问一次“试点何时结束？”，查看它是否根据资料说明该日期尚未确定。

至此，你已经完成了资料导入、检索、原文读取和带来源的问答。接下来可以换成自己的资料，重复这个流程。

## 常见问题

| 现象 | 下一步 |
| --- | --- |
| 安装后找不到 `uv`、`node` 或 `ov` | 重新打开终端；仍不可用时，按对应安装指南检查命令路径 |
| `ov health` 连接失败 | 在 `ov config` 中检查当前连接、服务地址和 API Key；本地服务同时检查运行终端 |
| Bot 健康检查返回 `503` | 确认已使用 `--with-bot` 启动，查看服务终端及 [VikingBot 日志](../guides/17-vikingbot.md) |
| 导入失败或检索无结果 | 查看返回的任务状态，确认模型可用、处理完成且查询 URI 与导入目标一致 |
| 模型返回 `UnsupportedModel` | 核对模型名、端点和套餐是否匹配，参见[模型开通指南](../guides/02-volcengine-purchase-guide.md) |
| Agent 无法调用 `ov` | 在 Agent 使用的终端中检查 `ov --version` 和 `ov health`，完成安装及连接配置 |

<a id="使用-sdk"></a>

## 下一步

继续[资料检索](08-resources.md)，用多份资料体验需要综合来源和资料未覆盖的问题，再用[效果评估](06-evaluate.md)记录自己的任务结果。
