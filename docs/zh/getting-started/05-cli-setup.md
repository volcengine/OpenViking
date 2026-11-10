# CLI 入门

`ov` 是 OpenViking 的命令行客户端。你可以在终端中用它导入资料、检索内容和读取原文，也可以让能够执行终端命令的 Agent 调用它，为回答提供依据。

本页从安装和连接开始，带你浏览一份资料、按问题检索并读取结果。完成后，你就可以在自己的终端或 Agent 中使用同一个 OpenViking 连接。

## 准备工作

准备以下工具和连接信息：

| 准备项 | 获取方式 |
| --- | --- |
| Node.js 和 npm | 从 [Node.js 官网](https://nodejs.org/en/download)安装 LTS 版本，npm 随安装包提供；已有可用的 `ov` 时可直接进入[连接](#连接) |
| OpenViking 服务 | 使用已开通的火山引擎云服务或正在运行的自建服务；还没有服务时，先完成[快速开始](02-quickstart.md#开通或启动服务)中的开通或启动步骤 |
| 访问凭据 | 云服务使用控制台提供的 API Key；开启认证的自建服务使用管理员提供的 User/Admin Key；默认无鉴权的本机服务可留空 |
| 一份可访问的资料 | 使用服务中已有的资料；首次体验可在连接成功后[导入灯塔样例](02-quickstart.md#导入样例) |

安装 Node.js 后，重新打开终端，确认 Node.js 和 npm 可用：

```bash
node --version
npm --version
```

CLI 通过 HTTP 连接服务。仅安装 CLI 时，无需在本机安装 Python 或配置模型；资料处理使用服务端配置的模型。

## 安装

使用 npm 安装 `ov`：

```bash
npm install -g @openviking/cli
```

安装后设置显示语言，便于后续交互和 Agent 调用。中文使用 `zh-CN`，英文使用 `en`：

```bash
ov language zh-CN
```

确认终端可以找到 CLI 并输出版本号：

```bash
ov --version
```

如果安装服务端时已提供 `ov`，可以直接使用已有命令。

## 连接

打开连接向导，为要访问的服务添加一个命名连接：

```bash
ov config
```

按所选服务填写连接信息，保存后设为当前配置。已有对应连接时，直接选中它即可。

| 服务 | 连接类型 | 填写内容 |
| --- | --- | --- |
| 本机默认服务 | 自定义（Custom） | URL 为 `http://127.0.0.1:1933`；默认无鉴权模式下密钥留空 |
| 远程自建服务 | 自定义（Custom） | 使用管理员提供的服务 URL 和 User/Admin Key |
| 火山引擎云服务 | OpenViking Service | 使用向导提供的服务地址，输入控制台取得的 API Key |

可以将连接命名为 `local`、`cloud` 等便于识别的名称。服务 URL 指向 OpenViking 数据服务；模型的 API 地址配置在服务端。

通过向导输入密钥，可以避免把密钥明文写入命令历史。在 API Key 认证模式下，普通数据访问使用 User/Admin Key，Root Key 用于服务管理。更多身份配置见[身份认证](../guides/04-authentication.md)。

## 检查连接

先查看当前连接，核对服务地址。该命令会隐藏密钥：

```bash
ov config show
```

再验证配置、连接和认证：

```bash
ov config validate
```

检查通过后，查看服务健康状态：

```bash
ov health
```

结果中的 `healthy: true` 表示服务健康。接下来读取一份资料，确认当前身份可以访问所需内容。资料导入所用的模型，以及 VikingBot 的问答能力，在各自的使用步骤中验证。

## 浏览与读取

列出当前服务中可访问的共享资料目录：

```bash
ov ls viking://resources
```

如果还没有资料，先[导入灯塔样例](02-quickstart.md#导入样例)，等待处理完成后回到这里。以下使用样例目录 `viking://resources/lighthouse-demo`；使用自己的资料时，将命令中的目录 URI 换成实际路径。

展开目录，找到资料正文对应的文件 URI：

```bash
ov tree viking://resources/lighthouse-demo
```

复制输出中的文件 URI，替换下面的占位值，读取正文：

```bash
ov read "<文件 URI>"
```

样例正文包含“试点环境”和“反馈收集”等内容。能够读到正文，说明连接和文件访问已完成。

## 检索资料

当你有具体问题时，可以先检索相关内容，再读取命中的原文。检索使用服务端的模型配置，可能产生模型调用费用。

```bash
ov find "灯塔项目第一版怎样收集用户反馈？" --uri viking://resources/lighthouse-demo
```

结果会列出匹配内容及其 URI。选择与问题相关的结果，用 `ov read` 读取正文，核对反馈由谁收集、如何交付。CLI 返回的检索结果可以作为 Agent 生成回答的依据。

## 交给 Agent 使用

在能够执行终端命令的 Agent 中发送下面的任务，让它使用已配置的 CLI 完成检索、阅读和回答：

```text
请使用已配置的 ov CLI，查询 viking://resources/lighthouse-demo 中的资料。
先按“灯塔项目第一版怎样收集用户反馈，需要提前准备什么？”进行 ov find 检索，
再用 ov read 读取相关结果的原文，然后回答这个问题。
为关键结论列出来源文件的完整 viking:// URI；资料未说明的内容请明确指出。
```

Agent 需要能访问 `ov` 命令及其连接配置。如果 Agent 运行在另一台电脑、容器或不同系统用户下，在该环境中完成安装和连接，再执行任务。

`ov chat` 则将问题发送给服务端的 **VikingBot**。使用它需要目标服务已启用 VikingBot；本机可按 [VikingBot](11-vikingbot.md) 章节使用 `openviking-server --with-bot` 启动。火山引擎云服务路径使用上面的 Agent 调用方式完成问答。`ov health` 检查的是数据服务，Bot 的可用状态需另外检查。

## 切换连接

同时使用云端和本地服务时，先列出已保存的连接：

```bash
ov config list
```

打开选择器，切换到目标连接：

```bash
ov config switch
```

切换后查看当前地址，并重新检查连接：

```bash
ov config show
ov health
```

切换连接后，命令访问的是目标服务及当前身份下的内容。云端导入的资料会保留在云端；在本地体验同一样例时，需要向本地服务导入。迁移已有资料可参考[导入导出](15-transfer.md#数据服务间迁移)。

两类配置文件分别负责连接和服务运行：

| 配置文件 | 用途 |
| --- | --- |
| `~/.openviking/ovcli.conf` | CLI 当前连接、访问凭据和命令默认行为 |
| `~/.openviking/ov.conf` | 自建服务的模型、存储和服务参数 |

需要指定其他配置路径或在脚本中管理命名连接时，查看[客户端配置](../configuration/02-client.md)和 `ov config --help`。

## 常用命令

| 目的 | 命令 |
| --- | --- |
| 查看目录 | `ov ls "<目录 URI>"`、`ov tree "<目录 URI>"` |
| 按问题检索 | `ov find "问题" --uri "<目录 URI>"` |
| 读取原文 | `ov read "<文件 URI>"` |
| 查看已生成的目录概览 | `ov overview "<目录 URI>"` |
| 查看后台任务 | `ov task status <task_id>` |
| 查询参数 | `ov --help`、`ov <命令> --help` |

## 常见问题

| 现象 | 下一步 |
| --- | --- |
| 找不到 `npm` 或 `ov` | 安装 Node.js 后重新打开终端；若 npm 已可用而 `ov` 不可用，检查 npm 全局可执行文件目录是否在 `PATH` 中 |
| npm 安装权限错误 | 按 [npm 官方指南](https://docs.npmjs.com/resolving-eacces-permissions-errors-when-installing-packages-globally)调整全局安装权限 |
| 命令提示设置语言 | 运行 `ov language zh-CN` 或 `ov language en`，保存显示语言 |
| 连接失败 | 用 `ov config show` 核对当前 URL；本机服务需保持运行，再用 `ov config validate` 检查 |
| 认证失败或读取被拒绝 | 核对 Key 类型和资料访问权限，见[身份认证](../guides/04-authentication.md) |
| 切换连接后找不到原资料 | 核对目标服务和身份；向该服务导入样例，或迁移原资料 |
| `ov chat` 返回 `404 page not found` | 核对当前地址是否提供 VikingBot 接口；云服务可由已有 Agent 调用 CLI 完成问答，本地服务按 [VikingBot](11-vikingbot.md) 章节启动并检查 Bot |
| 导入时模型返回 `UnsupportedModel` | 核对服务端 `ov.conf` 中模型名称、端点和套餐是否匹配，参见[模型开通指南](../guides/02-volcengine-purchase-guide.md) |
| 找不到目录概览 | 查看资料处理状态；概览是否生成取决于处理方式与配置，见[上下文层级](../concepts/03-context-layers.md) |

## 让 Agent 配置

也可以让 Agent 帮你完成安装和连接。复制下面的提示词，并补充要连接的服务类型、连接名称和自建服务地址。API Key 通过交互向导输入，或由已有的可信环境变量提供。

<AgentPrompt>

```text
请帮我安装并配置 OpenViking CLI（ov），连接我指定的 OpenViking 服务。

先检查 ov 是否已安装；需要安装时使用 npm install -g @openviking/cli。
如果缺少 Node.js/npm，请说明安装方式。将 CLI 显示语言设为 zh-CN。

根据我提供的目标，查看 ov config list -o json，复用匹配的命名连接；
新建连接前查看 ov config add ov-service --help 或 ov config add custom --help。
使用明确的 --name 保存连接，并将已确认的目标设为当前配置。
如果服务类型、URL 或凭据来源缺失，请向我询问缺失项。
遇到同名但内容不同的配置，先说明差异，再询问是否替换。

API Key 使用 --api-key-env 从已有环境变量读取，或通过 --api-key-stdin 传入。
如果没有可用的安全输入方式，引导我在终端运行 ov config 输入密钥。
展示配置时使用 ov config show，避免输出凭据。

完成后运行 ov config validate 和 ov health，检查实际返回结果。
告诉我当前连接的服务地址、检查结果，以及仍需处理的问题。
本次任务完成安装和连接，资料导入由后续任务指定。
```

</AgentPrompt>

## 下一步

继续[快速开始](02-quickstart.md#提问并核对)，完成带来源的资料问答；准备导入自己的项目资料时，查看[资料检索](08-resources.md)。

<!-- Preserve incoming links to previous section titles. -->
<a id="_1-安装"></a>
<a id="_2-连接"></a>
<a id="_4-浏览与读取"></a>
<a id="安装与使用-cli"></a>
<a id="准备"></a>
<a id="_1-安装-ov"></a>
<a id="_2-添加连接"></a>
<a id="_3-检查连接"></a>
<a id="ov-能做什么"></a>
<a id="管理多个连接"></a>
<a id="api-key"></a>
<a id="找不到-ov"></a>
<a id="npm-报权限错误"></a>
<a id="命令提示需要显示语言"></a>
<a id="本机服务没有响应"></a>
<a id="api-key-校验失败"></a>
<a id="当前配置不对"></a>
