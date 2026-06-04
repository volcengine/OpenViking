# OpenViking CLI 配置指南

本文介绍如何安装 OpenViking CLI、完成配置，并验证它可以连接到 OpenViking。

`ov` 是客户端 CLI。它连接到已经存在的 OpenViking 服务端，或连接到 Volcengine Cloud。它不是服务端安装命令。如果你还没有安装或启动自托管服务端，请先阅读[快速开始](02-quickstart.md)或[服务端模式](03-quickstart-server.md)。

你可以用两种方式阅读本文：

- 如果你自己手动配置 `ov`，请阅读[手动配置](#手动配置)。
- 如果你让 Agent 帮你配置，请把本文发给 Agent，并让它阅读 [Agent 辅助配置](#agent-辅助配置)。

两种方式都应该先查看当前安装版本的 CLI 帮助：

```bash
ov --help
ov config --help
```

CLI 会持续演进。请把 `ov --help` 和 `ov <command> --help` 作为当前安装版本的命令准确信息来源。

## 本文配置什么

CLI 使用 `~/.openviking/ovcli.conf` 作为 active 客户端连接配置。

创建命名配置时，`ov` 会把配置保存为 `~/.openviking/ovcli.conf.<name>`。切换配置时，`ov` 会把选中的已保存配置复制到 `~/.openviking/ovcli.conf`。

`ov config` 是面向人的交互式配置管理器，可以新增、编辑、删除、校验和切换配置。

`ov config add`、`ov config edit`、`ov config list`、`ov config switch <name>` 和 `ov config delete` 是面向脚本和 Agent 的确定性命令。

## 最短路径

```bash
npm i -g @openviking/cli
ov --help
ov config
ov config validate
ov health
ov status
```

预期结果：

- `ov --help` 输出 OpenViking 命令列表。
- `ov config` 打开交互式配置管理器。
- `ov config validate` 确认 active 配置可以连接服务端并通过鉴权。
- `ov health` 确认服务端基础可达。
- `ov status` 展示 active 配置和服务端诊断信息。

## 开始前

你需要准备：

- Node.js 和 npm。
- 一个可访问的 OpenViking 目标：
  - Volcengine Cloud，或
  - 自托管 OpenViking 服务端。
- 如果目标需要鉴权，需要准备 API Key。

API Key 是敏感凭证。优先通过 `ov config` 的交互式输入框、环境变量或 stdin 传入。避免把 API Key 直接写进聊天消息或 shell history。

## 安装 `ov`

先检查是否已经安装：

```bash
command -v ov
ov --version
```

安装或升级 npm 包：

```bash
npm i -g @openviking/cli
```

验证：

```bash
ov --help
```

如果仍然找不到 `ov`，关闭并重新打开 shell，或检查 npm 全局 bin 目录：

```bash
npm bin -g
```

## 选择连接目标

### Volcengine Cloud

如果你希望使用火山引擎托管的 OpenViking，选择此项。

- 服务端 URL 固定为：`https://api.vikingdb.cn-beijing.volces.com/openviking`
- API Key 必填。
- API Key 获取地址：https://console.volcengine.com/vikingdb/openviking/region:openviking+cn-beijing

### Self-Managed

如果你运行或维护自己的 OpenViking 服务端，选择此项。

- 本地默认 URL：`http://127.0.0.1:1933`
- 本地无鉴权服务通常不需要 API Key。
- 远程或开启鉴权的自托管服务端可能需要 API Key。

## 手动配置

如果你正在阅读本文，并准备自己配置 `ov`，使用这个路径。

运行：

```bash
ov config
```

然后选择：

1. `Add config`
2. `Volcengine Cloud` 或 `Self-Managed`
3. 配置名称，或留空自动生成
4. 所需的 URL 和 API Key
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

1. 确认用户要连接 Volcengine Cloud 还是自托管服务端。
2. 在选择命令前，运行 `ov --help`、`ov config --help` 和相关 config 子命令的帮助。
3. 如果你具备长期记忆能力，并且用户允许，可以记录当前 `ov --help` 命令面的简要摘要。不要记录 API Key 或其他密钥。
4. 当必需信息明确时，使用非交互式 `ov config` 命令。
5. 优先从环境变量或 stdin 读取 API Key。除非没有更安全的方式，否则不要让用户把密钥粘贴到聊天里。
6. 使用 `ov config validate` 校验 active 配置，然后运行 `ov health` 和 `ov status`。
7. 如果非交互式配置因为信息缺失、鉴权不明确或终端输入更安全而失败，请引导用户使用 `ov config` 交互式向导。

### 查看当前安装的 CLI

运行：

```bash
ov --help
ov config --help
ov config add --help
ov config add cloud --help
ov config add self-managed --help
ov config edit --help
```

以当前安装版本的 CLI 帮助为准。如果本文与本地帮助不一致，请遵循本地帮助，并告诉用户差异是什么。

### 列出已有配置

```bash
ov config list -o json
```

如果已经存在合适的 saved config，可以按名称激活：

```bash
ov config switch prod -o json
```

然后运行验证命令。

### 添加 Volcengine Cloud

请让用户通过环境变量或其他安全方式把 API Key 提供给 shell，然后运行：

```bash
ov config add cloud --name prod --api-key-env OV_API_KEY --activate -o json
```

如果必须从 stdin 读取：

```bash
printf '%s' "$OV_API_KEY" | ov config add cloud --name prod --api-key-stdin --activate -o json
```

只有当用户或 OpenViking 管理员提供了身份信息时，才使用 `--account` 和 `--user`。

### 添加本地自托管服务

对于本地无鉴权服务：

```bash
ov config add self-managed --name local --url http://127.0.0.1:1933 --activate -o json
```

如果本地服务没有运行，请先引导用户启动服务端。参见[服务端模式](03-quickstart-server.md)。

### 添加远程自托管服务

对于使用普通 API Key 的远程自托管服务：

```bash
ov config add self-managed --name hosted --url https://ov.example.com --api-key-env OV_API_KEY --activate -o json
```

如果用户提供的是 root API key，需要同时提供目标 account 和 user：

```bash
ov config add self-managed --name hosted --url https://ov.example.com --root-api-key-env OV_ROOT_API_KEY --account "$OV_ACCOUNT" --user "$OV_USER" --activate -o json
```

Root key 需要显式 `--account` 和 `--user`，这样普通 CLI 命令才知道以哪个身份执行。

### 编辑或替换配置

先列出配置：

```bash
ov config list -o json
```

重命名并激活 saved config：

```bash
ov config edit prod --new-name production --activate -o json
```

替换 API Key：

```bash
ov config edit production --api-key-env OV_API_KEY --activate -o json
```

替换自托管 URL：

```bash
ov config edit local --url http://127.0.0.1:1933 --activate -o json
```

只有在你明确要覆盖已有 saved config 名称时，才使用 `--force`。

### 删除 saved config

只删除非 active 的 saved config：

```bash
ov config delete old-local -o json
```

如果该配置正处于 active 状态，先切换到另一个配置：

```bash
ov config switch prod -o json
ov config delete old-local -o json
```

## 验证配置

运行：

```bash
ov config show
ov config validate
ov health
ov status
```

检查配置时优先使用 `ov config show`，因为它会隐藏密钥。

除非你理解配置文件可能包含密钥，否则不要打印原始配置文件。

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
- Agent 辅助配置时，优先使用环境变量或 stdin。
- 不要把 API Key 直接写进可能被 shell history 保存的命令。
- 除非你明确信任当前聊天渠道，否则不要把 API Key 粘贴进聊天。
- 不要打印原始 `~/.openviking/ovcli.conf`。
- 不要分享包含 API Key 的截图。
- 演示和试用建议使用临时或可撤销的 key。

## 常见问题

### 找不到 `ov`

运行：

```bash
npm i -g @openviking/cli
npm bin -g
```

然后重新打开 shell，或把 npm 全局 bin 目录加入 `PATH`。

### npm 全局安装失败

如果 npm 报权限错误，请按你平时管理 Node.js 的方式处理。除非你本来就用 sudo 管理全局 npm 包，否则不要直接运行 `sudo npm i -g`。

### 本地服务端没有运行

对于本地 Self-Managed 配置，先验证服务端：

```bash
curl http://127.0.0.1:1933/health
```

如果失败，先启动服务端再配置 `ov`。参见[服务端模式](03-quickstart-server.md)。

### API Key 校验失败

重新运行 `ov config` 并编辑配置。对于 Volcengine Cloud，确认 API Key 来自上面的 OpenViking 控制台地址。对于自托管服务，确认服务端是否要求鉴权。

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
ov config switch prod -o json
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

CLI 配置完成后，可以尝试：

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
