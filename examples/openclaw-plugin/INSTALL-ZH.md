# 为 OpenClaw 安装 OpenViking 记忆功能

通过 [OpenViking](https://github.com/volcengine/OpenViking) 为 [OpenClaw](https://github.com/openclaw/openclaw) 提供长效记忆能力。安装完成后，OpenClaw 会自动记住对话中的重要信息，并在回复前回忆相关内容。

> 当前文档介绍的是基于 `context-engine` 架构的新版 OpenViking 插件。

## 前置条件

| 组件 | 版本要求 |
| --- | --- |
| Node.js | >= 22 |
| OpenClaw | >= 2026.3.7 |

插件以远程模式连接到已有的 OpenViking 服务。它不会帮你启动 OpenViking server。需要先启动 OpenViking，并保持服务运行，再把插件的 `baseUrl` 指向这个 HTTP 服务。默认本地地址是 `http://127.0.0.1:1933`。

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

## 旧版升级说明

如果你之前安装过旧版 `memory-openviking`，先清理旧插件，再执行下面的安装或升级命令。

- 新版 `openviking` 与旧版 `memory-openviking` 不兼容，不能混装。
- 如果你从未安装过旧版插件，可以跳过本节。

```bash
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-plugin/upgrade_scripts/cleanup-memory-openviking.sh -o cleanup-memory-openviking.sh
bash cleanup-memory-openviking.sh
```

## 通过 ClawHub 安装（推荐）

```bash
openclaw plugins install clawhub:@openclaw/openviking
```

安装完成后运行交互式配置向导：

```bash
openclaw openviking setup
```

向导会提示你填写远程 OpenViking 服务地址和可选的 API Key，并将配置写入 `$OPENCLAW_STATE_DIR/openclaw.json`（默认：`~/.openclaw/openclaw.json`）。

## 通过 ov-install 安装（替代方案）

`ov-install` 一键完成插件部署。macOS、Linux、Windows 的流程相同。

```bash
npm install -g openclaw-openviking-setup-helper

# 安装插件
ov-install

# 安装插件到指定 OpenClaw 实例
ov-install --workdir ~/.openclaw-second
```

## 升级

要把插件升级到最新版本，执行：

```bash
npm install -g openclaw-openviking-setup-helper@latest && ov-install -y
```

## 安装或升级到指定版本

如果要安装或升级到某个正式发布版本，执行：

```bash
ov-install -y --version 0.2.9
```

## 参数说明

| 参数 | 含义 |
| --- | --- |
| `--workdir PATH` | 指定 OpenClaw 数据目录 |
| `--version VER` | 指定插件版本，例如 `0.2.9` 会对应插件 `v0.2.9` |
| `--current-version` | 查看当前已安装的插件版本 |
| `--plugin-version REF` | 指定插件版本，支持 tag、分支或 commit |
| `--github-repo owner/repo` | 指定插件来源仓库，默认 `volcengine/OpenViking` |
| `--update` | 只升级插件 |
| `-y` | 非交互模式，使用默认配置 |

## OpenClaw 插件参数说明

插件配置写在 `plugins.entries.openviking.config` 下。通常安装助手会自动写好，只有在你需要手动调整时，才需要关注下面这些参数。

查看当前插件整体配置：

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

## 启动

安装完成后，运行：

```bash
openclaw gateway restart
```

Windows PowerShell：

```powershell
openclaw gateway restart
```

## 验证

检查插件是否已接管 `contextEngine`：

```bash
openclaw config get plugins.slots.contextEngine
```

输出 `openviking` 即表示插件已生效。

查看运行日志：

```bash
openclaw logs --follow
```

日志中出现 `openviking: registered context-engine`，表示插件已成功加载。

查看 OpenViking 自身日志：

默认日志文件在你的 `workspace/data/log/openviking.log`。如果使用默认配置，通常对应：

```bash
cat ~/.openviking/data/log/openviking.log
```

查看当前已安装版本：

```bash
ov-install --current-version
```

### 链路检查（可选）

如果上述验证都正常，还想进一步确认从 Gateway 到 OpenViking 的完整链路是否通畅，可以使用插件自带的健康检查脚本：

```bash
python examples/openclaw-plugin/health_check_tools/ov-healthcheck.py
```

该脚本会进行一次真实的对话注入，然后从 OpenViking 侧验证会话是否被正确捕获、提交、归档并提取出记忆。详细说明见 [health_check_tools/HEALTHCHECK-ZH.md](./health_check_tools/HEALTHCHECK-ZH.md)。

## 卸载

卸载 OpenClaw 插件：

```bash
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-plugin/upgrade_scripts/uninstall-openclaw-plugin.sh -o uninstall-openviking.sh
bash uninstall-openviking.sh
```

如果你的 OpenClaw 数据目录不是默认路径：

```bash
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-plugin/upgrade_scripts/uninstall-openclaw-plugin.sh -o uninstall-openviking.sh
bash uninstall-openviking.sh --workdir ~/.openclaw-second
```
