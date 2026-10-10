# 安装与使用 CLI

`ov` 是 OpenViking 的命令行客户端。OpenViking 把 Agent 的上下文存成一个文件系统：资源、记忆和技能都是 `viking://` 下的目录和文件。用 `ov` 可以浏览、读取、检索和写入这些内容。Agent 也用同样的命令操作这些内容。

`ov` 连接一个已有的 OpenViking 服务。还没有服务时，先完成[快速开始](02-quickstart.md)的第 1 步。

## 让 Agent 配置

点击下方的**复制**，把提示词粘贴给你使用的编程 Agent，例如 Claude Code、Codex 或 Cursor。Agent 会安装 `ov`，向你确认要连接的服务，然后保存配置并检查连接。

<AgentPrompt>

````markdown
# openviking-cli

> `ov` 是 OpenViking 的命令行客户端。OpenViking 是面向 AI Agent 的上下文数据库。`ov` 连接已有的 OpenViking 服务端，或连接火山引擎上的 OpenViking 服务。

我希望你为我安装并配置 OpenViking CLI（`ov`）。自主执行下面的全部步骤。只在步骤标注 ASK 的地方停下来问我。

OBJECTIVE：安装 `ov`，为我的 OpenViking 服务保存一个命名配置，并把它设为当前配置。

DONE WHEN：`ov config validate` 的检查项全部通过（配置文件有效、服务器可连接、认证已通过、健康），并且 `ov health -o json` 返回 `"healthy": true`。

## TODO

- [ ] 安装 `ov` 并设置显示语言
- [ ] 确认要连接的服务
- [ ] 保存并激活命名配置
- [ ] 验证连接

## 规则

- 你不能猜测连接目标。已有配置、本地文件、开放端口和正在运行的服务都不代表我的同意。
- 切换、替换或删除配置，探测或启动本地服务，或写入数据之前，你必须先 ASK 我。
- API Key 不能出现在命令文本、shell 历史、日志、记忆或打印出的配置文件中。只能通过 stdin 或已经存在的环境变量传入 key。
- 如果你无法用这两种方式传入 key，ASK 我自己运行 `ov config` 并输入 key。
- 运行 `ov config add` 时必须传 `--name`，这样重试会更新同一个配置。
- `ov config add|edit|list|switch|delete` 必须加 `-o json`。根据退出码和 `error.code` 判断结果，不要解析说明文字。
- 如果本机 `ov --help` 与本文不一致，以本机帮助为准，并告诉我差异。

## 第 1 步：安装 ov

需要 Node.js 和 npm。

```bash
command -v ov || npm i -g @openviking/cli
ov language zh-CN
ov --version
```

如果我用英文和你交流，改用 `ov language en`。未保存显示语言时，大多数 `ov` 命令在非交互式 shell 中以退出码 2 退出。

安装后仍找不到 `ov` 时，把 `$(npm prefix -g)/bin` 加入 `PATH`。不要使用 `sudo npm`。没有 npm 时，先 ASK 我，再用 `cargo install --git https://github.com/volcengine/OpenViking ov_cli` 从源码构建。

查看要用到的命令帮助：

```bash
ov config add ov-service --help
ov config add custom --help
```

## 第 2 步：确认连接目标

运行 `ov config list -o json`。如果已有配置与目标一致，先 ASK 我，再用 `ov config switch <NAME> -o json` 激活它。

否则，除非我已经说明，ASK 我要连接哪种目标：

| 目标 | 服务地址 | API Key |
|---|---|---|
| OpenViking 服务（火山引擎云） | 固定地址，不要传 `--url`。 | 必填。我在[控制台](https://console.volcengine.com/vikingdb/openviking/region:openviking+cn-beijing)的“用户管理 → API Key”中获取。 |
| 远程自建服务 | ASK 我。 | ASK 我。 |
| 本机自建服务 | `http://127.0.0.1:1933` | 通常不需要。 |

只有目标是本机自建服务时，才检查服务是否运行：`curl -fsS http://127.0.0.1:1933/health`。检查失败时，ASK 我启动服务端。参见 https://docs.openviking.ai/zh/guides/03-deployment 。

除非我的管理员提供了 `--account` 和 `--user` 的值，否则不要询问这两项。

## 第 3 步：保存并激活配置

把 `<NAME>`、`<URL>` 和 `<ENV_VAR>` 替换为确认过的值，不保留尖括号。`$OV_API_KEY` 表示可信的运行时密钥来源，不是字面量 key。

OpenViking 服务：

```bash
printf '%s' "$OV_API_KEY" | ov config add ov-service --name <NAME> --api-key-stdin --activate -o json
```

使用 API Key 的远程自建服务：

```bash
printf '%s' "$OV_API_KEY" | ov config add custom --name <NAME> --url <URL> --api-key-stdin --activate -o json
```

无鉴权的本机自建服务：

```bash
ov config add custom --name <NAME> --url http://127.0.0.1:1933 --activate -o json
```

特殊密钥情况：

- key 已经在环境变量中时，用 `--api-key-env <ENV_VAR>` 代替 `--api-key-stdin`。
- 只有 root key，且服务端为 `trusted` 模式：使用 `--root-api-key-stdin --account <ACCOUNT> --user <USER>`。服务端为 `api_key` 模式时，root key 不能读取数据，ASK 我提供 user 或 admin key。
- 同时有 user key 和 root key：使用 `--api-key-stdin --root-api-key-env <ENV_VAR>`。一条命令只有一个 stdin，所以第二个 key 必须来自已存在的环境变量。

`ov config` 子命令的退出码：

| 退出码 | 含义 | 你的操作 |
|---|---|---|
| `0` | 成功，或已经处于目标状态 | 继续。 |
| `2` | 输入错误、缺少参数，或未设置显示语言 | 修正输入，或运行 `ov language <code>`。 |
| `3` | 同名配置已存在且内容不同 | 先 ASK 我，再加 `--force`。 |
| `4` | 服务端不可达，或配置校验失败 | ASK 我确认 URL，以及服务是否运行。 |
| `5` | 鉴权失败，或 key 角色不匹配 | ASK 我确认 key 和 key 类型。 |
| `6` | 操作被拒绝，例如删除当前配置 | ASK 我如何继续。 |

不要用猜测的值重试。

## 第 4 步：验证

```bash
ov config validate
ov health -o json
```

读取输出内容。退出码为 0 不代表服务健康。查看配置时使用 `ov config show`，它会隐藏密钥。不要打印 `~/.openviking/ovcli.conf`。

除非我要求，不要导入数据做演示。

配置完成后，我可能会让你用 `ov` 浏览、检索、添加或整理内容。OpenViking 的内容是 `viking://` 下的目录树。读取时，先用 `ov abstract` 或 `ov overview` 读目录摘要，再用 `ov read` 读需要的文件。运行 `ov --help` 查看命令分组。使用某条命令前，先运行 `ov <命令> --help`。

EXECUTE NOW：完成上面的 TODO 列表，达到：`ov config validate` 的检查项全部通过，并且 `ov health -o json` 返回 `"healthy": true`。

需要更多上下文时，阅读 https://docs.openviking.ai/llms.txt 。
````

</AgentPrompt>

下文是手动配置步骤。

## 准备

需要 Node.js 和 npm。

还需要连接信息。连接信息取决于服务类型：

| 服务类型 | 服务地址 | API Key |
|---|---|---|
| OpenViking 服务（火山引擎云） | 固定地址，无需填写。 | 必填。在 [OpenViking 控制台](https://console.volcengine.com/vikingdb/openviking/region:openviking+cn-beijing)的**用户管理 → API Key** 中获取。 |
| 远程自建服务 | 向管理员获取。 | 向管理员获取。 |
| 本机自建服务 | `http://127.0.0.1:1933` | 默认配置不需要。 |

## 1. 安装 `ov`

```bash
npm i -g @openviking/cli
ov language zh-CN
ov --version
```

`ov language` 设置显示语言。英文界面使用 `en`。未设置语言时，大多数命令不能运行。

在运行 OpenViking 服务端的机器上，`ov` 已随服务端安装。跳过 `npm i`，只设置语言。

## 2. 添加连接

```bash
ov config
```

按提示操作：

1. 选择**添加配置**。
2. 选择服务类型。OpenViking 服务选择 **OpenViking 服务（火山引擎云）**；自建服务选择**自定义**。
3. 输入配置名称。留空时，`ov` 自动生成名称。
4. 按提示输入服务地址和 API Key。
5. 校验通过后，选择**保存并设为当前配置**。

## 3. 检查连接

```bash
ov config validate
```

输出的**检查项**全部通过时，连接可用：配置文件“有效”，服务器“可连接”，认证“已通过”，健康状态“健康”。

配置到此完成。接下来可以[导入并检索第一份文档](02-quickstart.md#_3-导入文档)。想了解更多，可以继续往下看。

## `ov` 能做什么

OpenViking 的内容是一棵目录树。运行 `ov ls` 查看根目录 `viking://`：

- `viking://resources/`：导入的文档、代码仓库和网页。同一账号内共享。
- `viking://user/<用户 ID>/`：你的记忆、私有资源、技能和会话。`viking://~/` 指向这个目录。
- `viking://agent/`：同一账号内共享的技能和 Agent 配置。

每个目录有 L0 摘要和 L1 概览。文件全文是 L2。先读目录的 L0 和 L1，判断内容是否相关，再读需要的 L2 文件。详见 [Viking URI](../concepts/04-viking-uri.md) 和[上下文层级](../concepts/03-context-layers.md)。

| 任务 | 命令 |
|---|---|
| 浏览目录 | `ov ls`、`ov tree`、`ov stat`。`ov tui` 打开交互式浏览界面。 |
| 按层读取 | `ov abstract`（L0）、`ov overview`（L1）、`ov read`（L2）。`ov get` 把文件下载到本地。 |
| 检索 | `ov find`（语义检索）、`ov grep`（匹配内容）、`ov glob`（匹配路径） |
| 导入资料和技能 | `ov add-resource`、`ov add-skill`、`ov skills` |
| 写入和整理 | `ov write`、`ov mkdir`、`ov mv`、`ov cp`、`ov rm` |
| 从对话中提取记忆 | `ov session new`、`ov session add-message`、`ov session commit`。`ov add-memory` 一步完成这三步。 |
| 等待后台处理 | `ov task list`、`ov task status`、`ov wait` |
| 保存和回滚版本 | `ov snapshot` |
| 备份与迁移 | `ov export`、`ov import`、`ov backup`、`ov restore` |
| 重建索引（`viking://resources` 需要 admin key） | `ov reindex` |
| 管理用户（需要 admin 或 root key） | `ov admin list-users`、`ov admin register-user`、`ov admin regenerate-key` |
| 检查服务 | `ov health`、`ov status` |

导入资料和提交会话后，服务端在后台处理：解析内容，提取记忆，生成 L0 和 L1，建立索引。处理完成前，`ov find` 检索不到新内容。

用 `ov <命令> --help` 查看命令的参数。这些操作也可以直接交给 Agent 完成。

::: warning 注意
`ov rm -r` 会删除目录及其中的全部内容。删除在服务端执行，使用同一服务的其他 Agent 也会失去这些内容。删除前，先用 `ov ls` 确认 URI。
:::

## 管理多个连接

```bash
ov config list     # 列出已保存的配置
ov config switch   # 选择当前配置
ov config show     # 查看当前配置，密钥会被隐藏
```

编辑或删除配置时，运行 `ov config` 并选择对应操作。在脚本中添加配置时，使用 `ov config add`，参数见 `ov config add --help`。

当前配置是 `~/.openviking/ovcli.conf`。每个已保存的配置是 `~/.openviking/ovcli.conf.<名称>`。设置 `OPENVIKING_CLI_CONFIG_FILE` 后，`ov` 改用该文件作为当前配置，已保存的配置位于该文件所在目录。全部字段见[客户端配置](../configuration/02-client.md)。

## API Key

服务使用 API Key 认证时，key 有三种角色：

- **User key**：用于数据命令，例如 `ov add-resource` 和 `ov find`。大多数用户只需要这种 key。
- **Admin key**：可以执行数据命令，也可以管理本账号的用户。
- **Root key**：管理整个服务，例如创建账号。在 `api_key` 模式下，root key 不能读写账号数据。

一个配置可以同时保存 user key 和 root key。普通命令使用 user key。`ov admin`、`ov system`、`ov reindex`、`ov task status` 和 `ov task list` 加 `--sudo` 时使用 root key。详见[认证](../guides/04-authentication.md)。

保护 API Key：

- 在 `ov config` 的输入框中输入 key。不要把 key 写进命令，shell 历史会保存命令。
- 用 `ov config show` 查看配置，它会隐藏密钥。不要分享 `~/.openviking/ovcli.conf` 的内容或截图。
- 演示和试用时，使用可以撤销的临时 key。
- 让 Agent 配置时，不要把 key 粘贴到对话中。把 key 放进环境变量，或在 Agent 请求时自己运行 `ov config` 输入 key。

## 常见问题

### 找不到 `ov`

打开一个新终端。仍然找不到时，把 npm 全局 binary 目录加入 `PATH`。在 macOS 和 Linux 上，该目录通常是 `$(npm prefix -g)/bin`。

### npm 报权限错误

按你平时管理 Node.js 的方式修复权限，例如使用 nvm。除非你一直用 sudo 管理全局包，否则不要运行 `sudo npm i -g`。

### 命令提示需要显示语言

运行 `ov language zh-CN` 或 `ov language en`，然后重新运行命令。

### 本机服务没有响应

检查服务：

```bash
curl http://127.0.0.1:1933/health
```

检查失败时，先启动服务端。参见[部署](../guides/03-deployment.md)。

### API Key 校验失败

运行 `ov config`，选择**编辑配置**，重新输入 key。OpenViking 服务的 key 从控制台复制。自建服务的 key 和 key 类型向管理员确认。在 `api_key` 模式下，数据命令需要 user key 或 admin key。

### 当前配置不对

运行 `ov config list` 查看当前配置。运行 `ov config switch` 选择其他配置。

## 下一步

- 导入并检索第一份文档：[快速开始](02-quickstart.md)。
- 把 OpenViking 接入你日常使用的 Agent：[Agent 接入方式](../agent-integrations/01-overview.md)。
