# ovcli 配置

`ovcli.conf` 是 `ov` CLI 的客户端配置文件，用于保存服务端连接、鉴权身份和命令默认行为。

Codex、Claude Code、OpenCode 等 Agent 插件还会读取各自的 `OPENVIKING_*` 环境变量，用于控制 Recall、Capture、调试等行为；这些不属于 `ovcli.conf`，请在对应的 [Agent 集成](../agent-integrations/01-overview.md)文档中配置。

建议使用 `ov config` 创建和维护配置；使用 `ov config show` 查看脱敏后的当前配置。

默认路径：

```text
~/.openviking/ovcli.conf
```

也可以指定其他文件：

```bash
export OPENVIKING_CLI_CONFIG_FILE=/path/to/ovcli.conf
```

## 完整示例

```json
{
  "url": "https://openviking.example.com",
  "api_key": "<user-or-admin-key>",
  "root_api_key": "<root-key>",
  "account": "acme",
  "user": "alice",
  "actor_peer_id": "agent:research-assistant",
  "timeout": 60,
  "output": "table",
  "echo_command": true,
  "show_progress": false,
  "verbose": false,
  "profile": false,
  "upload": {
    "ignore_dirs": "node_modules,.cache,dist",
    "include": "*.md,*.pdf",
    "exclude": "*.tmp,*.log"
  },
  "extra_headers": {
    "X-Tenant": "acme"
  },
  "gateway_token": "<gateway-token>"
}
```

不需要的字段可以省略。本地 `dev` 模式通常只需要 `url`。

## 连接与鉴权

```json
{
  "url": "https://openviking.example.com",
  "api_key": "<user-or-admin-key>",
  "root_api_key": "<root-key>",
  "account": "acme",
  "user": "alice",
  "actor_peer_id": "agent:research-assistant",
  "extra_headers": {
    "X-Tenant": "acme"
  },
  "gateway_token": "<gateway-token>"
}
```

| 字段 | 类型 / 可选值 | 默认值 | 作用 |
|---|---|---|---|
| `url` | HTTP(S) URL | `http://127.0.0.1:1933` | OpenViking 服务端地址 |
| `api_key` | string / `null` | `null` | 普通数据操作使用的 user/admin key |
| `root_api_key` | string / `null` | `null` | `ov --sudo` 管理操作使用的 root key |
| `account` | string / `null` | `null` | trusted 模式或 root-key-only 配置使用的账号身份 |
| `user` | string / `null` | `null` | trusted 模式或 root-key-only 配置使用的用户身份 |
| `actor_peer_id` | string / `null` | `null` | 默认 Actor Peer 标识 |
| `agent_id` | string / `null` | `null` | 兼容字段；新配置使用 `actor_peer_id`，两者不能同时设置 |
| `extra_headers` | object / `null` | `null` | 每个 HTTP 请求附加的自定义请求头；`extra_header` 是兼容别名 |
| `gateway_token` | string / `null` | `null` | 网关挑战重试时使用的 `X-Gateway-Token` |

### API Key 选择

| 配置方式 | 普通命令 | `ov --sudo` |
|---|---|---|
| 仅 `api_key` | 使用 user/admin key | 不可用 |
| 仅 `root_api_key`，并配置 `account`、`user` | 使用 root key 和显式身份 | 使用 root key |
| 同时配置两种 key | 使用 `api_key` | 使用 `root_api_key` |
| 两种 key 都不配置 | 仅适用于未开启鉴权的本地服务 | 不可用 |

`ov.conf` 中的 `server.root_api_key` 是服务端接受的凭证；CLI 管理该服务端时，`ovcli.conf` 中的 `root_api_key` 需要与其一致。

## 命令行为

```json
{
  "timeout": 120,
  "echo_command": true,
  "show_progress": true,
  "verbose": false,
  "profile": false
}
```

| 字段 | 类型 / 可选值 | 默认值 | 作用 |
|---|---|---|---|
| `timeout` | number，秒，`> 0` | `60` | HTTP 请求超时 |
| `echo_command` | boolean | `true` | 是否显示 `find`、`search`、`ls` 等命令的实际请求参数 |
| `show_progress` | boolean | `false` | 上传时是否默认显示进度 |
| `verbose` | boolean | `false` | 上传时是否默认输出诊断信息 |
| `profile` | boolean | `false` | 是否请求性能 profile；服务端还需启用 `server.profile_enabled` |
| `output` | `"table"` / `"json"` | `"table"` | 兼容字段；当前命令使用 `-o table` 或 `-o json` 选择输出格式 |

`--profile`、`--progress`、`--no-progress`、`--verbose` 等命令行参数会覆盖本次命令的配置。

## 上传过滤

```json
{
  "upload": {
    "ignore_dirs": "node_modules,.cache,dist",
    "include": "*.md,*.pdf",
    "exclude": "*.tmp,*.log"
  }
}
```

| 字段 | 类型 / 格式 | 默认值 | 作用 |
|---|---|---|---|
| `upload.ignore_dirs` | 逗号分隔字符串 / `null` | `null` | 忽略的目录名 |
| `upload.include` | 逗号分隔 glob / `null` | `null` | 只上传匹配的文件 |
| `upload.exclude` | 逗号分隔 glob / `null` | `null` | 排除匹配的文件 |

本地目录上传还会遵循 `.gitignore`。命令行 `--include`、`--exclude` 会与配置文件中的规则合并。

## 相关环境变量

`ov` CLI 直接使用的环境变量只有少量几个：

| 环境变量 | 作用 |
|---|---|
| `OPENVIKING_CLI_CONFIG_FILE` | 指定要读取的 `ovcli.conf` 路径 |
| `OPENVIKING_UPLOAD_MODE` | 指定临时上传模式：`local` 或 `shared` |

`ov config add` 和 `ov config edit` 的 `--api-key-env <变量名>`、`--root-api-key-env <变量名>` 可以从指定环境变量读取密钥，并写入配置文件。

Agent 插件使用的 `OPENVIKING_AUTO_RECALL`、`OPENVIKING_RECALL_LIMIT`、`OPENVIKING_AUTO_CAPTURE`、`OPENVIKING_DEBUG` 等变量由插件进程读取，不是 `ovcli.conf` 字段。

## 多服务配置

普通 `ov` 命令以及 `ov config show`、`ov config validate` 按以下顺序解析实际配置：

1. 设置 `OPENVIKING_CLI_CONFIG_FILE` 后，该路径具有最高优先级；文件不存在时会直接报错。
2. 未设置该变量时，使用默认 Active 文件：

```text
~/.openviking/ovcli.conf
```

交互式管理器以及 `ov config list`、`switch`、`add`、`edit`、`delete` 始终管理默认配置仓库。该仓库中的命名配置与默认 Active 文件位于同一目录：

```text
~/.openviking/ovcli.conf.<name>
```

例如，一份生产环境配置可以写成：

```json
{
  "url": "https://openviking.example.com",
  "api_key": "<production-api-key>",
  "timeout": 120
}
```

常用命令：

```bash
ov config
ov config list
ov config switch <name>
ov config validate
ov config show
```

`ov config switch <name>` 会把命名配置复制为默认 Active 文件。如果仍设置了 `OPENVIKING_CLI_CONFIG_FILE`，普通 `ov` 命令会继续读取环境变量指定的文件；需要取消该变量后才会使用刚切换的默认配置。新的 `ov` 命令会重新读取实际配置文件；已经运行的 Agent 客户端需要重启后才会读取变更。

交互式配置和 Agent 辅助配置步骤见[OpenViking CLI 配置指南](../getting-started/05-cli-setup.md)。
