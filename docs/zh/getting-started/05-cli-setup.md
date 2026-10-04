# 安装与使用 CLI

`ov` 是 OpenViking 的命令行客户端。它连接已有的 OpenViking 服务端，不负责安装服务端。还没有服务时，先完成[快速开始](02-quickstart.md)的第 1 步。

## 让 Agent 配置

复制下面的提示词，发给你使用的编程 Agent，例如 Claude Code、Codex 或 Cursor。Agent 会安装 `ov`，向你确认要连接的服务，然后完成配置和检查。

::: details 展开 Agent 提示词

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

EXECUTE NOW：完成上面的 TODO 列表，达到：`ov config validate` 的检查项全部通过，并且 `ov health -o json` 返回 `"healthy": true`。

需要更多上下文时，阅读 https://docs.openviking.ai/llms.txt 。
````

:::

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

第二条命令设置 CLI 的显示语言。英文界面使用 `en`。使用大多数命令前，必须先设置语言。

运行 OpenViking 服务端的机器已经有 `ov`。服务端安装包（`uv tool install openviking`）会一并安装它。

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
ov health
```

`ov config validate` 检查当前配置。检查项全部通过时，连接可用：配置文件有效、服务器可连接、认证已通过、健康。`ov health` 显示服务状态为 **Connected (Healthy)**。

下一步，[导入并检索第一份文档](02-quickstart.md#_3-导入文档)。

## 管理多个连接

```bash
ov config list     # 列出已保存的配置
ov config switch   # 选择当前配置
ov config show     # 查看当前配置，密钥会被隐藏
```

编辑或删除配置时，运行 `ov config` 并选择对应操作。在脚本中配置时，使用 `ov config add`，参数见 `ov config add --help`。

当前配置是 `~/.openviking/ovcli.conf`。每个已保存的配置是 `~/.openviking/ovcli.conf.<名称>`。切换时，`ov` 把选中的配置复制到当前配置文件。

设置 `OPENVIKING_CLI_CONFIG_FILE` 后，`ov` 把该文件作为当前配置，已保存的配置也位于该文件所在目录。全部字段见[客户端配置](../configuration/02-client.md)。

## API Key 类型

- **User key**：用于数据命令，例如 `ov add-resource` 和 `ov find`。大多数用户只需要这种 key。
- **Root key**：用于管理命令和带 `--sudo` 的命令。

一个配置可以同时保存两种 key。普通命令使用 user key，带 `--sudo` 的命令使用 root key。详见[认证](../guides/04-authentication.md)。

## 保护 API Key

- 在 `ov config` 的输入框中输入 API Key。不要把 key 写进命令，shell 历史会保存命令。
- 用 `ov config show` 查看配置。它会隐藏密钥。
- 不要分享 `~/.openviking/ovcli.conf` 的内容或截图。
- 演示和试用时，使用可以撤销的临时 key。
- 让 Agent 配置 `ov` 时，只通过你信任的渠道把 key 交给 Agent。

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

运行 `ov config`，选择**编辑配置**，重新输入 key。OpenViking 服务的 key 从控制台复制。自建服务的 key 和 key 类型向管理员确认。

### 当前配置不对

运行 `ov config list` 查看当前配置。运行 `ov config switch` 选择其他配置。

### `ov config setup-cli` 不可用

该命令已移除。使用 `ov config`。

## 重建索引

`ov reindex <uri>` 检查并修复已导入内容的索引：

```bash
ov reindex viking://resources/my-project --mode vectors_only
ov reindex viking://resources/my-project --mode semantic_and_vectors
```

- `vectors_only`（默认）：只重建向量。
- `semantic_and_vectors`：先重新生成摘要和概览（`.abstract.md`、`.overview.md`），再重建向量。

命令默认处理整个子树，并等待完成。MD5 指纹未变化的 resource 和 skill 会被跳过。加 `--force` 重建范围内的全部数据。加 `--recursive false` 只处理目标本身。全部参数见 `ov reindex --help`。

## 下一步

- 导入并检索第一份文档：[快速开始](02-quickstart.md)。
- 把 OpenViking 接入你日常使用的 Agent：[Agent 接入方式](../agent-integrations/01-overview.md)。
- 用 `ov --help` 查看全部命令，用 `ov <命令> --help` 查看单个命令的参数。
