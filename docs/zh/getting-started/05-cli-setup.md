# OpenViking CLI 配置指南

本文介绍如何安装 OpenViking CLI、完成配置，并验证它可以连接到 OpenViking。

`ov` 是客户端 CLI。它连接到已经存在的 OpenViking 服务端，或连接到 OpenViking Service（火山引擎云）。它不是服务端安装命令。如果你还没有安装或启动自定义 OpenViking 服务端，请先阅读[快速开始](02-quickstart.md)或[服务端模式](03-quickstart-server.md)。

你可以用两种方式阅读本文：

- 如果你自己手动配置 `ov`，请阅读[手动配置](#手动配置)。
- 如果你让 Agent 帮你配置，请把本文发给 Agent，并让它阅读 [Agent 辅助配置](#agent-辅助配置)。

CLI 会持续演进。请把 `ov --help` 和 `ov <command> --help` 作为当前安装版本的命令准确信息来源。

## 本文配置什么

CLI 使用 `~/.openviking/ovcli.conf` 作为 active 客户端连接配置。

创建命名配置时，`ov` 会把配置保存为 `~/.openviking/ovcli.conf.<name>`。切换配置时，`ov` 会把选中的已保存配置复制到 `~/.openviking/ovcli.conf`。

`ov config` 是面向人的交互式配置管理器，可以新增、编辑、删除、校验和切换配置。

`ov config add`、`ov config edit`、`ov config list`、`ov config switch <name>` 和 `ov config delete` 是面向脚本和 Agent 的确定性命令。

## 选择连接目标

运行配置命令前，先选择要连接的 OpenViking 目标。

除非用户已经明确说明，Agent 应先询问用户要连接哪种目标。已有配置、active 配置、本地文件、默认端口和正在运行的服务可以帮助 Agent 追问细节，但不代表用户同意 Agent 选择目标、切换或替换配置、探测本地服务、启动服务端，或写入数据。

### OpenViking Service（火山引擎云）

如果你希望使用火山引擎云上的 OpenViking 托管服务，选择此项。

- `ov` 使用的服务端端点：`https://api.vikingdb.cn-beijing.volces.com/openviking`
- 管理 API Key 的控制台页面：https://console.volcengine.com/vikingdb/openviking/region:openviking+cn-beijing
- 在控制台进入 User Management → API Key，查看并复制你的 key。
- API Key 必填。
- 标准配置只需要 API Key。除非用户的管理员明确提供身份覆盖值，否则不要询问 `--account` 或 `--user`。

### 远程自定义

如果你要连接不在当前机器上的自定义 OpenViking 服务端，选择此项。

- 服务端 URL 由用户或服务端管理员提供。
- 可能需要 API Key。
- 只有 root key 的配置需要 `--account` 和 `--user`。

### 本地自定义

只有当用户要连接当前机器上的自定义 OpenViking 服务端时，才选择此项。

- 本地默认 URL：`http://127.0.0.1:1933`
- 本地无鉴权服务通常不需要 API Key。
- 除非用户选择本地自定义配置，否则 Agent 不应探测本地端口、curl 本地 health endpoint，或运行启动服务端的命令。

> **注意：** 最近的 CLI 版本（v0.3.23+）要求在运行大多数命令前先保存一个显示语言。在交互式终端中，CLI 会在首次使用时提示你选择；在非交互式 shell（Agent 或 CI）中，任何非豁免命令都会以 `2` 退出，直到你运行 `ov language en` 或 `ov language zh-CN`。只有 `ov language`/`ov lang`、`ov config add|edit|delete|list` 和 `ov config switch <name>` 是豁免的，因此请在上面的 `ov config validate`、`ov health` 和 `ov status` 之前先运行 `ov language <code>`。

## 开始前

你需要准备：

- 一种安装 CLI 的方式：
  - 使用 Node.js 和 npm 安装独立的 `@openviking/cli` 包，或
  - 使用 Python 工具安装完整的 `openviking` 包。
- 一个可访问的 OpenViking 目标：
  - OpenViking Service（火山引擎云），或
  - 自定义 OpenViking 服务端。
- 如果目标需要鉴权，需要准备 API Key。

API Key 是敏感凭证。手动配置时优先通过 `ov config` 的交互式输入框输入。只有当你明确相信当前渠道时，才把 API Key 提供给 Agent。Agent 应通过 stdin 传入 key，不能把 key 写进 shell 命令、日志、长期记忆或原始配置输出。只有当 key 已经存在于当前 shell 环境变量中时，才使用环境变量。

## 安装 `ov`

先检查是否已经安装：

```bash
command -v ov
ov --version
```

如果 `ov --version` 或任何其他 `ov` 命令提示 OpenViking 需要显示语言，请先选择语言再重试：

```bash
ov language en
# 或
ov language zh-CN
```

安装或升级 npm 包：

```bash
npm i -g @openviking/cli
```

npm 包是最轻量的独立 CLI 安装方式。如果你同时需要 Python SDK 或服务端包，Python 包也会提供 `ov`：

```bash
uv tool install openviking --upgrade
# 或
pip install openviking --upgrade --force-reinstall
```

验证：

```bash
ov --help
```

如果仍然找不到 `ov`，关闭并重新打开 shell，或检查 npm 全局 prefix：

```bash
npm prefix -g
```

在 macOS 和 Linux 上，全局 npm binary 目录通常是 `$(npm prefix -g)/bin`。确认该目录已经加入 `PATH`。

## 密钥类型

OpenViking CLI 配置可以包含 user key、root key，或同时包含两者。

- User key：用于普通数据命令，例如 `ov add-resource`、`ov find` 和 `ov tree`。服务端会从 key 推导身份，所以通常不需要传 `--account` 或 `--user`。这是大多数用户需要的方式。
- Root key：用于管理操作和需要 `--sudo` 的命令。Root key 自身不包含租户身份。如果一个配置只有 root key，就必须同时包含 `--account` 和 `--user`；这个 root key 会同时服务于该身份下的普通命令和 `--sudo` 命令。
- User key + root key：适合一个配置同时支持日常数据操作和偶尔的管理操作。普通命令使用 user key，`--sudo` 命令使用 root key，并带上配置中的 account 和 user。

## 手动配置

如果你正在阅读本文，并准备自己配置 `ov`，使用这个路径。

运行：

```bash
ov config
```

然后选择：

1. `Add config`
2. `OpenViking Service（火山引擎云）` 或 `自定义`
3. 配置名称，或留空自动生成
4. 上面选择的目标所需的 URL 和 API Key
5. 校验成功后保存配置

如果你维护多个 OpenViking 目标，之后可以使用：

```bash
ov config switch
```

切换 active 配置。

配置完成后，继续阅读[验证配置](#验证配置)。

## Agent 辅助配置

如果 Agent 正在替用户配置 `ov`，使用这个路径。Agent 应该阅读整篇文档。当确定性命令不适合用户环境时，上面的手动配置流程就是回退路径。

### Agent 检查清单

1. 除非用户已经明确说明，先询问用户要连接哪种目标：OpenViking Service（火山引擎云）、远程自定义，还是本地自定义。
2. 不要根据已有配置、active 配置、本地文件、默认端口或正在运行的服务推断用户想要的 setup。
3. 切换配置、替换配置、探测本地服务、启动服务端或写入数据前，都要先询问用户。
4. 在选择命令前，运行 `ov --help`、`ov config --help` 和相关 config 子命令的帮助。
5. 如果你具备长期记忆能力，并且用户允许，可以记录当前 `ov --help` 命令面的简要摘要。不要记录 API Key 或其他密钥。
6. 当必需信息明确时，使用非交互式 `ov config` 命令。
7. Agent 配置时始终传 `--name`，这样重试会命中同一个 saved config。
8. 如果 Agent 已经通过可信渠道拿到 API Key，使用 `--api-key-stdin` 或 `--root-api-key-stdin`，并且只把 key 内容写入 stdin。只有当环境变量已经存在时，才使用 `--api-key-env` 或 `--root-api-key-env`。不要要求用户额外打开一个 shell 只为了给 Agent export 一个 key。
9. 使用 `-o json`，并根据 JSON 结果和进程退出码分支处理。
10. 使用 `ov config validate` 校验 active 配置，然后运行 `ov health` 和 `ov status`。
11. 如果非交互式配置因为信息缺失、鉴权不明确或终端输入更安全而失败，请引导用户使用 `ov config` 交互式向导。

### 查看当前安装的 CLI

运行：

```bash
ov --help
ov config --help
ov config add --help
ov config add ov-service --help
ov config add custom --help
ov config edit --help
```

以当前安装版本的 CLI 帮助为准。如果本文与本地帮助不一致，请遵循本地帮助，并告诉用户差异是什么。

如果 help 命令提示 OpenViking 需要显示语言，请运行 `ov language en`；如果用户希望使用中文，则运行 `ov language zh-CN`，然后重试。`ov config add`、`ov config list`、`ov config edit`、`ov config switch <name>` 和 `ov config delete` 等非交互式 config 子命令可以在设置显示语言前运行。

### 使用稳定名称便于重试

Agent 创建配置时始终传 `--name`。如果省略名称，`ov` 会随机生成名称；重试时可能创建第二个 saved config，而不是更新预期的配置。

当传入相同 `--name` 且配置内容完全一致时，`ov config add` 可以安全重复运行。它会以 `0` 退出，`--activate` 也会再次把该 saved config 设为 active。如果同名配置已经存在但内容不同，命令会以 `3` 退出，并要求只有在确认替换时才使用 `--force`。

下面示例中的 `<CONFIG-NAME>` 和 `<REMOTE-OPENVIKING-URL>` 等占位符需要替换成用户确认过的值再运行。运行时不要保留尖括号。

### 读取结果

对非交互式 config 命令使用 `-o json` 时，成功结果会输出到 stdout：

```json
{"status":"ok","result":{"action":"add","name":"<CONFIG-NAME>"}}
```

`result` 对象会随子命令变化。`add` 和 `edit` 还会包含 `kind`、`url`、`saved_path`、`active_path`、`activated` 和 `validation` 等字段，因此 Agent 不应该假设结果里只有 `action` 和 `name`。

错误结果会输出到 stderr：

```json
{"status":"error","error":{"code":"bad_input","message":"..."}}
```

Agent 应该根据进程退出码和 JSON 中的 `error.code` 分支处理，不要解析面向人的说明文字。

| 退出码 | 含义 |
|--------|------|
| `0` | 成功，或已经处于目标状态 |
| `2` | 输入错误、缺少参数、名称非法、无法读取密钥来源，或在非交互式 shell 中尚未选择显示语言（请先运行 `ov language <code>`） |
| `3` | 同名配置已经存在但内容不同；只有确认要替换时才传 `--force` |
| `4` | 服务端不可达，或配置校验失败 |
| `5` | 鉴权或 key 角色不匹配，例如把 root key 传到了需要 user key 的位置 |
| `6` | 操作被拒绝，例如删除 active 配置 |

### 列出已有配置

```bash
ov config list -o json
```

列表输出形状如下：

```json
{"status":"ok","result":[{"name":"<CONFIG-NAME>","kind":"OpenViking Service","url":"https://api.vikingdb.cn-beijing.volces.com/openviking","active":true}]}
```

做存在性检查时，读取 `result[].name`。判断是否还需要切换 active config 时，读取匹配项的 `active` 标记。

如果已经存在合适的 saved config，可以按名称激活：

```bash
ov config switch <CONFIG-NAME> -o json
```

然后运行验证命令。

### 添加 OpenViking Service

如果 Agent 已经通过可信渠道拿到 API Key，运行：

```bash
ov config add ov-service --name <CONFIG-NAME> --api-key-stdin --activate -o json
```

shell pipe 形式如下：

```bash
printf '%s' "$API_KEY" | ov config add ov-service --name <CONFIG-NAME> --api-key-stdin --activate -o json
```

`$API_KEY` 表示可信的运行时密钥来源，不是字面量 key。Agent 能在不把 key 写进命令文本、shell history、日志或长期 export 的环境变量时提供 key，就应使用 stdin。

只把 API Key 内容写入 stdin，不要把 key 放进 shell 命令本身。这会写入一个 OpenViking Service 配置，并使用固定端点：`https://api.vikingdb.cn-beijing.volces.com/openviking`。`ov-service` 目标不接受自定义服务端 URL。

只有当环境变量已经存在时，才使用环境变量：

```bash
ov config add ov-service --name <CONFIG-NAME> --api-key-env <API-KEY-ENV-VAR> --activate -o json
```

标准 OpenViking Service 配置不要传 `--account` 或 `--user`。只有当用户或 OpenViking 管理员提供身份覆盖值时，才使用它们。

### 添加本地自定义服务

只有当用户选择本地自定义时，才使用这个路径。

对于本地无鉴权服务：

```bash
ov config add custom --name <CONFIG-NAME> --url http://127.0.0.1:1933 --activate -o json
```

如果本地服务没有运行，请先引导用户启动服务端。参见[服务端模式](03-quickstart-server.md)。

### 添加远程自定义服务

对于使用普通 API Key 的远程自定义服务：

```bash
ov config add custom --name <CONFIG-NAME> --url <REMOTE-OPENVIKING-URL> --api-key-stdin --activate -o json
```

stdin pipe 形式如下：

```bash
printf '%s' "$API_KEY" | ov config add custom --name <CONFIG-NAME> --url <REMOTE-OPENVIKING-URL> --api-key-stdin --activate -o json
```

把 API Key 写入 stdin。如果 key 已经存在于当前 shell 环境变量中，可以改用 `--api-key-env <API-KEY-ENV-VAR>`。

如果用户只提供 root API key，需要同时提供目标 account 和 user：

```bash
ov config add custom --name <CONFIG-NAME> --url <REMOTE-OPENVIKING-URL> --root-api-key-stdin --account <ACCOUNT-ID> --user <USER-ID> --activate -o json
```

把 root API key 写入 stdin。Root key 需要显式 `--account` 和 `--user`，这样普通 CLI 命令才知道以哪个身份执行。

如果用户同时拥有 user key 和 root key，可以把两者放在同一个配置里：

```bash
ov config add custom --name <CONFIG-NAME> --url <REMOTE-OPENVIKING-URL> --api-key-stdin --root-api-key-env <ROOT-API-KEY-ENV-VAR> --account <ACCOUNT-ID> --user <USER-ID> --activate -o json
```

这样普通命令使用 user key，需要 `--sudo` 的命令使用 root key。因为一个命令只有一个 stdin 流，第二个 key 必须来自已经存在的环境变量。如果两个 key 都不在环境变量中，请使用 `ov config` 并引导用户完成交互式流程。

### 编辑或替换配置

先列出配置：

```bash
ov config list -o json
```

重命名并激活 saved config：

```bash
ov config edit <CONFIG-NAME> --new-name <NEW-CONFIG-NAME> --activate -o json
```

替换 API Key：

```bash
ov config edit <CONFIG-NAME> --api-key-stdin --activate -o json
```

把新的 API Key 写入 stdin。

替换自定义服务 URL：

```bash
ov config edit <CONFIG-NAME> --url <CUSTOM-OPENVIKING-URL> --activate -o json
```

只有在你明确要覆盖已有 saved config 名称时，才使用 `--force`。

### 删除 saved config

只删除非 active 的 saved config：

```bash
ov config delete <OLD-CONFIG-NAME> -o json
```

如果该配置正处于 active 状态，先切换到另一个配置：

```bash
ov config switch <CONFIG-NAME> -o json
ov config delete <OLD-CONFIG-NAME> -o json
```

## 验证配置

运行：

```bash
ov config show
ov config list -o json
ov config validate
ov health
ov status
```

检查配置时优先使用 `ov config show`，因为它会隐藏密钥。

除非你理解配置文件可能包含密钥，否则不要打印原始配置文件。

如果验证命令提示 OpenViking 需要显示语言，请运行 `ov language en`；如果用户希望使用中文，则运行 `ov language zh-CN`，然后重新验证。

`ov status` 包含更宽泛的服务端和数据诊断。如果 `ov config validate` 和 `ov health` 通过，`ov status` 中的 warning 不一定代表 CLI 配置失败。

## 学习其他 CLI 命令

配置成功后，用内置帮助继续了解 `ov` 的其他能力：

```bash
ov --help
ov config --help
ov add-resource --help
```

Agent 在运行不熟悉的命令前，应该重新查看帮助。如果 Agent 为用户维护长期记忆，并且用户允许，可以记录当前命令面的简要摘要，方便之后继续工作。不要记录密钥、原始配置文件或私有服务详情，除非用户明确要求。

## 凭证安全

- API Key 可能允许访问你的 OpenViking 数据。
- 手动配置时，优先使用 `ov config` 的交互式输入框。
- Agent 辅助配置时，只有通过你明确相信的渠道提供 API Key。
- Agent 应通过 stdin 传入 key。只有当环境变量已经存在于当前 shell 中时，才使用环境变量。
- 不要把 API Key 直接写进可能被 shell history 保存的命令。
- 不要打印原始 `~/.openviking/ovcli.conf`。
- 不要分享包含 API Key 的截图。
- 演示和试用建议使用临时或可撤销的 key。

## 常见问题

### 找不到 `ov`

运行：

```bash
npm i -g @openviking/cli
npm prefix -g
```

然后重新打开 shell，或把 npm 全局 binary 目录加入 `PATH`。在 macOS 和 Linux 上，该目录通常是 `$(npm prefix -g)/bin`。

### npm 全局安装失败

如果 npm 报权限错误，请按你平时管理 Node.js 的方式处理。除非你本来就用 sudo 管理全局 npm 包，否则不要直接运行 `sudo npm i -g`。

### 本地服务端没有运行

只有当用户选择本地自定义时才使用此项。先验证服务端：

```bash
curl http://127.0.0.1:1933/health
```

如果失败，先启动服务端再配置 `ov`。参见[服务端模式](03-quickstart-server.md)。

### API Key 校验失败

重新运行 `ov config` 并编辑配置。对于 OpenViking Service，确认 API Key 来自上面的 OpenViking 控制台地址。对于自定义服务，确认服务端是否要求鉴权。

Agent 不应该反复重试未知 key。请让用户确认目标类型、服务端 URL、key 类型、account 和 user。

### active 配置不对

检查并切换：

```bash
ov config show
ov config list
ov config switch
ov config validate
```

Agent 可以按名称切换：

```bash
ov config list -o json
ov config switch <CONFIG-NAME> -o json
```

### 非交互式配置不适合当前情况

使用交互式向导：

```bash
ov config
```

当密钥应由用户直接在终端输入、连接目标不明确，或校验结果需要人工判断时，这是合适的回退路径。

### 旧配置命令

使用 `ov config`。不要使用旧的或已移除的配置命令，例如 `ov config setup-cli`。

## 下一步

CLI 配置完成后，使用 `ov --help` 和 `ov <command> --help` 继续了解其他命令。

添加资源会把数据写入 active OpenViking 服务端。如果你想做一个小演示，请选择你愿意存入服务端的资源。Agent 运行这类演示命令前，必须先征得用户同意。

```bash
ov add-resource https://github.com/volcengine/OpenViking --wait
ov find "what is OpenViking"
ov tree viking://resources/ -L 2
```

查看全部命令：

```bash
ov --help
ov config --help
ov add-resource --help
```
