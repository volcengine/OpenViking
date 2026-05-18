# OpenClaw 插件

通过 OpenViking 为 [OpenClaw](https://github.com/openclaw/openclaw) 提供长效记忆能力。安装完成后，OpenClaw 会自动记住对话中的重要信息，并在回复前回忆相关内容。

插件以 `openviking` context engine 的形式注册——负责长期记忆检索、会话归档、归档摘要与记忆抽取，覆盖 OpenClaw 全生命周期。

源码：[examples/openclaw-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/openclaw-plugin)

## 前置条件

| 组件 | 版本要求 |
| --- | --- |
| Node.js | >= 22 |
| OpenClaw | >= 2026.3.7 |

插件以远程模式连接到已有的 OpenViking 服务，安装前请确保有可访问的 HTTP 服务——参见 [部署指南](../guides/03-deployment.md)。快速检查：

```bash
node -v
openclaw --version
```

> **从旧版 `memory-openviking` 升级？** 与新插件不兼容，请先清理：
>
> ```bash
> curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-plugin/upgrade_scripts/cleanup-memory-openviking.sh -o cleanup-memory-openviking.sh
> bash cleanup-memory-openviking.sh
> ```

## 通过 ClawHub 安装（推荐）

```bash
openclaw plugins install clawhub:@openclaw/openviking
```

随后运行交互式配置向导：

```bash
openclaw openviking setup
```

向导会提示填写远程 OpenViking 服务地址和可选的 API Key，并将配置写入 `$OPENCLAW_STATE_DIR/openclaw.json`（默认：`~/.openclaw/openclaw.json`）。

重启 gateway：

```bash
openclaw gateway restart
```

## 通过 `ov-install` 安装（替代方案）

`ov-install` 一键完成插件部署：

```bash
npm install -g openclaw-openviking-setup-helper
ov-install
```

常用变体：

```bash
# 指定 OpenClaw 数据目录
ov-install --workdir ~/.openclaw-second

# 锁定到某个发布版本
ov-install -y --version 0.2.9
```

之后升级：

```bash
npm install -g openclaw-openviking-setup-helper@latest && ov-install -y
```

### `ov-install` 参数

| 参数                       | 含义                                                            |
| -------------------------- | --------------------------------------------------------------- |
| `--workdir PATH`           | OpenClaw 数据目录                                               |
| `--version VER`            | 插件版本（如 `0.2.9` → 插件 `v0.2.9`）                          |
| `--current-version`        | 查看当前已安装的插件版本                                        |
| `--plugin-version REF`     | 仅指定插件版本，自动识别：npm 版本（如 `2026.5.8`）或 npm dist-tag（如 `dev`）走 npm；其它 ref（如 `v0.3.16`、`main`）走 GitHub 仓库；默认 `npm latest` |
| `--plugin-source=npm\|github` | 插件下载来源（默认 `npm`）                                       |
| `--plugin-package=NAME`    | npm 插件包名（默认 `@openviking/openclaw-plugin`）              |
| `--github-repo owner/repo` | 指定插件来源仓库，默认 `volcengine/OpenViking`                  |
| `--update`                 | 只升级插件                                                      |
| `-y`                       | 非交互模式，使用默认配置                                        |

## 插件配置

插件配置位于 `plugins.entries.openviking.config`。通常 setup 已经写好，仅在更换服务器等场景需要手动调整。

```bash
openclaw config get plugins.entries.openviking.config
```

| 参数           | 默认值                  | 含义                                                  |
| -------------- | ----------------------- | ----------------------------------------------------- |
| `baseUrl`      | `http://127.0.0.1:1933` | 远程 OpenViking HTTP 端点                             |
| `apiKey`       | empty                   | 可选的 OpenViking API Key                             |
| `agent_prefix` | `default`               | 本 OpenClaw 实例在远程使用的 agent 前缀                |

常见设置：

```bash
openclaw config set plugins.entries.openviking.config.baseUrl http://your-server:1933
openclaw config set plugins.entries.openviking.config.apiKey your-api-key
openclaw config set plugins.entries.openviking.config.agent_prefix your-prefix
```

## 验证

要一键检查插件注册、服务端连通性和版本兼容性，运行：

```bash
openclaw openviking status
```

自动化场景下，可为 `openclaw openviking status` 追加 `--json` 获取机器可读结果。`openclaw openviking setup` 也支持 `--json`，但自动化使用时需同时提供 `--base-url`。

或手动核对各个信号。确认插件占用了 `contextEngine` 槽位：

```bash
openclaw config get plugins.slots.contextEngine
```

输出 `openviking` 即说明插件已生效。

跟随 OpenClaw 日志查看注册信息：

```bash
openclaw logs --follow
# 期望出现：openviking: registered context-engine
```

OpenViking 服务端日志（默认路径）：

```bash
cat ~/.openviking/data/log/openviking.log
```

当前插件版本：

```bash
ov-install --current-version
```

### 全链路健康检查（可选）

如要进一步验证 Gateway → OpenViking 全链路，运行：

```bash
python examples/openclaw-plugin/health_check_tools/ov-healthcheck.py
```

脚本会通过 Gateway 注入一段真实对话，从 OpenViking 侧验证 session 已捕获、commit、归档并完成记忆抽取。详见 [HEALTHCHECK.md](https://github.com/volcengine/OpenViking/blob/main/examples/openclaw-plugin/health_check_tools/HEALTHCHECK.md)。

## 卸载

```bash
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-plugin/upgrade_scripts/uninstall-openclaw-plugin.sh -o uninstall-openviking.sh
bash uninstall-openviking.sh
```

非默认 OpenClaw 状态目录请追加 `--workdir ~/.openclaw-second`。

## 参见

- [完整安装指南](https://github.com/volcengine/OpenViking/blob/main/examples/openclaw-plugin/INSTALL.md) — 所有安装路径、参数与验证步骤
- [插件设计说明](https://github.com/volcengine/OpenViking/blob/main/examples/openclaw-plugin/README.md) — 架构、身份与路由、hook 生命周期
- [Agent 操作指南](https://github.com/volcengine/OpenViking/blob/main/examples/openclaw-plugin/INSTALL-AGENT.md) — 给代用户执行安装的 agent 看