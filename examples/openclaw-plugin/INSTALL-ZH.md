# 为 OpenClaw 安装 OpenViking

OpenViking 通过 `@openclaw/openviking` 插件为 OpenClaw 提供长期记忆、知识库检索、语义搜索和 RAG 上下文能力。

这份文档同时面向用户和自动化 agent：用户可以按步骤执行，agent 可以按命令和 JSON 结果判断下一步。

## 不要把插件和 Skill 装混

`@openclaw/openviking` 是 OpenClaw 插件。

不要用下面这个命令安装本插件：

```bash
clawhub install openviking
```

这个命令安装的是名为 `openviking` 的 AgentSkill，不是 OpenClaw 插件。

安装插件应使用：

```bash
openclaw plugins install @openclaw/openviking
```

## 前置要求

| 组件 | 要求 |
| --- | --- |
| Node.js | >= 22 |
| OpenClaw | >= 2026.4.8 |

插件以远程模式连接到已有的 OpenViking 服务。它不会帮你启动 OpenViking server。需要先启动 OpenViking，并保持服务运行，再把插件的 `baseUrl` 指向这个 HTTP 服务。默认本地地址是 `http://127.0.0.1:1933`。

OpenClaw 插件包版本边界：

- `2026.4.8` 是当前插件支持的最低 OpenClaw 版本。
- `2026.5.3` 开始，OpenClaw 在安装包时会校验 TypeScript 插件入口是否有编译后的 JavaScript 产物。
- `2026.5.4` 及之后，已安装/全局插件如果缺少编译后的 JavaScript，运行时不再回退加载 `.ts` 源码，插件可能被跳过。
- 推荐的 `openclaw plugins install @openclaw/openviking` 会安装已经发布并包含 `dist/*.js` 的插件包，普通用户不需要本地编译。
- `ov-install` 是备用/源码安装路径。当 ClawHub 或 OpenClaw 插件管理器路径不可用、被限流，或者明确需要测试源码 ref 时才使用。目标 OpenClaw `>= 2026.5.3` 时，它会在安装过程中编译插件。

快速检查：

```bash
node -v
openclaw --version
```

## 启动 OpenViking Server

如果 OpenViking 和 OpenClaw 在同一台机器上，最短流程是：

```bash
pip install openviking --upgrade --force-reinstall
openviking-server init
openviking-server doctor
openviking-server
```

`openviking-server init` 用来生成服务端配置，`openviking-server doctor` 用来检查本地模型和 provider 鉴权是否可用，`openviking-server` 才是真正启动 HTTP API 的命令。OpenClaw 使用插件期间，这个服务进程需要一直运行。

后台启动可以用：

```bash
mkdir -p ~/.openviking/data/log
nohup openviking-server > ~/.openviking/data/log/openviking.log 2>&1 &
```

如果 OpenViking 跑在另一台机器上，需要监听可访问的地址和端口，例如：

```bash
openviking-server --host 0.0.0.0 --port 1933
```

然后把 OpenClaw 插件的 `baseUrl` 配成对应地址，例如 `http://your-server:1933`。

安装或重启插件前，先确认服务能访问：

```bash
curl http://127.0.0.1:1933/health
```

## 推荐安装方式

普通用户、正式环境和 agent 自动安装都优先使用这条路径。

### 1. 安装插件

```bash
openclaw plugins install @openclaw/openviking
```

如果你的 OpenClaw 环境需要显式 registry 前缀，使用：

```bash
openclaw plugins install clawhub:@openclaw/openviking
```

### 2. 配置插件

用户交互式配置：

```bash
openclaw openviking setup
```

Agent 非交互配置：

```bash
openclaw openviking setup --base-url <OPENVIKING_URL> --api-key <API_KEY> --json
```

示例：

```bash
openclaw openviking setup --base-url http://127.0.0.1:1933 --api-key sk-xxx --json
```

`setup` 会写入 `plugins.entries.openviking.config`，并激活 `plugins.slots.contextEngine=openviking`。

如果 OpenViking 服务暂时不可达，但你仍希望先保存配置：

```bash
openclaw openviking setup --base-url <OPENVIKING_URL> --api-key <API_KEY> --allow-offline --json
```

如果使用 root API key，可能还需要租户上下文：

```bash
openclaw openviking setup \
  --base-url <OPENVIKING_URL> \
  --api-key <ROOT_API_KEY> \
  --account-id <ACCOUNT_ID> \
  --user-id <USER_ID> \
  --json
```

如果已有其他 context engine 占用 slot，setup 默认不会替换。确认要替换时再使用：

```bash
openclaw openviking setup --base-url <OPENVIKING_URL> --api-key <API_KEY> --force-slot --json
```

如需自定义 agent 路由前缀（可选；多数用户留空即可）：

```bash
openclaw openviking setup --base-url <OPENVIKING_URL> --api-key <API_KEY> --agent-prefix <PREFIX> --json
```

### 3. 重启 OpenClaw Gateway

```bash
openclaw gateway restart
```

如果你的 OpenClaw 版本使用不同的重启命令，请使用对应的 gateway 重启方式。

### 4. 验证

```bash
openclaw openviking status --json
```

期望结果：

| JSON 字段 | 期望值 |
| --- | --- |
| `configured` | `true` |
| `slotActive` | `true` |
| `health.ok` | 服务可达时应为 `true` |

也可以直接查看 OpenClaw 配置：

```bash
openclaw config get plugins.entries.openviking.config
openclaw config get plugins.slots.contextEngine
```

`plugins.slots.contextEngine` 应输出 `openviking`。

## Agent 判断规则

Agent 应优先使用 `--json`，并根据这些字段判断下一步：

| 结果 | 含义 | 建议动作 |
| --- | --- | --- |
| `success: true` | 配置已保存，setup 完成 | 重启 gateway，然后执行 status |
| `success: false`, `action: "slot_blocked"` | 配置可能已保存，但其他插件占用 `contextEngine` | 询问用户后再用 `--force-slot` |
| `success: false`, `action: "error"` | 校验失败 | 展示 `error`，不要宣称安装成功 |
| `health.ok: false` | 服务不可达 | 检查 URL/服务状态；只有用户接受时才用 `--allow-offline` |
| `keyProbe.keyType: "root_key"` | root key 需要租户上下文 | 追加 `--account-id` 和 `--user-id` |

## 配置说明

插件配置位于：

```text
plugins.entries.openviking.config
```

核心字段：

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `mode` | `remote` | 兼容旧配置的字段。当前只支持 remote。 |
| `baseUrl` | `http://127.0.0.1:1933` | OpenViking HTTP 地址 |
| `apiKey` | 空 | OpenViking API key |
| `agent_prefix` | 空 | OpenClaw agent ID 的可选前缀；若没有 agent ID，插件使用 `main`。交互式配置只接受字母、数字、`_` 和 `-`。 |
| `accountId` | 空 | 使用 root API key 时需要 |
| `userId` | 空 | 使用 root API key 时需要 |

普通修改优先使用 setup：

```bash
openclaw openviking setup --reconfigure
```

查看当前配置：

```bash
openclaw config get plugins.entries.openviking.config
```

### 配置参数

插件连接到已有的远端 OpenViking 服务。

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `baseUrl` | `http://127.0.0.1:1933` | 远端 OpenViking 服务地址 |
| `apiKey` | 空 | 远端 OpenViking API Key；服务端未开启认证时可不填 |
| `agent_prefix` | 空 | OpenClaw agent ID 的可选前缀；如果拿不到 agent ID，插件使用 `main`。交互式配置只接受字母、数字、`_` 和 `-` |

常见设置：

```bash
openclaw config set plugins.entries.openviking.config.baseUrl http://your-server:1933
openclaw config set plugins.entries.openviking.config.apiKey your-api-key
openclaw config set plugins.entries.openviking.config.agent_prefix your-prefix
```

## 升级

```bash
openclaw plugins update openviking
openclaw gateway restart
openclaw openviking status --json
```

确认 `configured` 和 `slotActive` 都是 `true`。

## 卸载

```bash
openclaw plugins uninstall openviking
openclaw config set plugins.slots.contextEngine legacy
openclaw gateway restart
```

当前 OpenClaw 原生卸载不一定会把 `plugins.slots.contextEngine` 恢复为 `legacy`。显式执行 `config set` 可以避免 slot 继续指向已卸载插件。

## 可选链路健康检查

如果 status 已通过，还想验证 Gateway 到 OpenViking 的完整链路，可以在仓库 checkout 中运行：

```bash
python examples/openclaw-plugin/health_check_tools/ov-healthcheck.py
```

该脚本会注入一次真实对话，并在 OpenViking 侧验证会话捕获、提交、归档和记忆提取。详见 [health_check_tools/HEALTHCHECK-ZH.md](./health_check_tools/HEALTHCHECK-ZH.md)。

## 备用路径：ov-install

`ov-install` 是备用路径，不是主安装方式。仅当 `openclaw plugins install @openclaw/openviking` 无法访问 ClawHub、被限流，或者你明确需要从 Git 分支/源码 ref 安装测试时使用。

先尝试 OpenClaw 插件管理器。如果该路径不可用，再执行：

```bash
npm install -g openclaw-openviking-setup-helper
ov-install
```

常用备用/源码参数：

| 参数 | 含义 |
| --- | --- |
| `--workdir PATH` | 指定 OpenClaw state 目录 |
| `--version REF` | 指定 Git ref、tag、branch 或发布版本 |
| `--current-version` | 查看 helper 记录的当前版本 |
| `--base-url URL` | OpenViking 服务器地址（启用非交互模式） |
| `--api-key KEY` | OpenViking API key |
| `--agent-prefix PREFIX` | Agent 路由前缀 |
| `--update` | 更新 helper 管理的安装 |

面向用户的安装，请先使用 `openclaw plugins install @openclaw/openviking`。只有作为备用路径时才选择 `ov-install`。

## 从 ov-install 迁移到 openclaw plugin install

如果之前通过 `ov-install` 安装了 OpenViking，切换到推荐的 `openclaw plugins install` 安装方式前需要清理。

### 同一插件 ID（openviking，版本 >= 0.3.x）

ov-install 的 context-engine 部署会将文件写入 `~/.openclaw/extensions/openviking/`。通过 npm 安装后，OpenClaw 可能仍从旧目录加载。清理步骤：

```bash
# 删除 ov-install 部署的文件
rm -rf ~/.openclaw/extensions/openviking/

# 通过 OpenClaw 插件管理器安装
openclaw plugins install @openclaw/openviking

# 重新配置（openclaw.json 中的已有配置会保留）
openclaw openviking setup --reconfigure
openclaw gateway restart
openclaw openviking status --json
```

已有的配置字段（`baseUrl`、`apiKey`、`agentId` 等）会保留。新版本在运行时兼容读取旧字段名，无需手动修改配置。

### 旧插件 ID（memory-openviking，版本 < 0.3.x）

旧版 memory 插件使用了不同的插件 ID 和 slot：

```bash
# 卸载旧插件
openclaw plugins uninstall memory-openviking 2>/dev/null || true

# 清理旧 slot 和文件
openclaw config set plugins.slots.memory none
rm -rf ~/.openclaw/extensions/memory-openviking/

# 安装新插件
openclaw plugins install @openclaw/openviking
openclaw openviking setup --base-url <OPENVIKING_URL> --api-key <API_KEY> --json
openclaw gateway restart
openclaw openviking status --json
```

或使用清理脚本：

```bash
bash examples/openclaw-plugin/upgrade_scripts/cleanup-memory-openviking.sh
```

另见：[INSTALL.md](./INSTALL.md) 和 [INSTALL-AGENT.md](./INSTALL-AGENT.md)。
