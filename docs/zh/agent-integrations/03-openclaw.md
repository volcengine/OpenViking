# OpenClaw 插件

为 [OpenClaw](https://github.com/openclaw/openclaw) 添加长效记忆。安装完成后，OpenClaw 会自动记住对话中的重要信息，并在每次回复前召回相关上下文。

源码：[examples/openclaw-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/openclaw-plugin)

## 前置条件

| 组件 | 版本要求 |
| --- | --- |
| Node.js | >= 22 |
| OpenClaw | >= 2026.4.8 |

插件需要连接到一个正在运行的 OpenViking 服务——参见 [部署指南](../guides/03-deployment.md)。

<details>
<summary><b>从旧版 <code>memory-openviking</code> 升级？</b></summary>

旧插件不兼容，请先清理：

```bash
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-plugin/upgrade_scripts/cleanup-memory-openviking.sh -o cleanup-memory-openviking.sh
bash cleanup-memory-openviking.sh
```

</details>

## 安装

```bash
openclaw plugins install clawhub:@openviking/openclaw-plugin
openclaw openviking setup --base-url http://your-server:1933 --api-key sk-xxx --json
openclaw gateway restart
```

`setup` 向导写入配置并激活插件。安装完成后开始对话——OpenClaw 会自动记忆和召回。

<details>
<summary><b>备用方案：通过 <code>ov-install</code> 安装</b></summary>

当 ClawHub 不可用时：

```bash
npm install -g openclaw-openviking-setup-helper
ov-install --base-url http://your-server:1933
```

常用参数：

| 参数 | 含义 |
| --- | --- |
| `--workdir PATH` | OpenClaw 数据目录（默认 `~/.openclaw`） |
| `--plugin-version=VER` | 插件版本：npm 版本、dist-tag 或 Git ref |
| `--base-url URL` | OpenViking 服务地址 |
| `--api-key KEY` | OpenViking API Key |
| `--uninstall` | 卸载插件 |

完整参数列表见 [安装指南](https://github.com/volcengine/OpenViking/blob/main/examples/openclaw-plugin/INSTALL.md)。

</details>

## 验证

```bash
openclaw openviking status
```

一键检查插件注册、服务端连通性和版本兼容性。追加 `--json` 获取机器可读结果。

<details>
<summary><b>手动验证</b></summary>

确认插件占用了 `contextEngine` 槽位：

```bash
openclaw config get plugins.slots.contextEngine
# 期望输出：openviking
```

全链路健康检查：

```bash
python examples/openclaw-plugin/health_check_tools/ov-healthcheck.py
```

详见 [HEALTHCHECK.md](https://github.com/volcengine/OpenViking/blob/main/examples/openclaw-plugin/health_check_tools/HEALTHCHECK.md)。

</details>

<details>
<summary><b>配置</b></summary>

插件配置位于 `plugins.entries.openviking.config`，通常 setup 已经写好。

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `baseUrl` | `http://127.0.0.1:1933` | OpenViking 服务端点 |
| `apiKey` | 空 | OpenViking API Key |
| `peer_prefix` | 空 | `peer_role=assistant` 时 assistant peer 身份的可选前缀 |

```bash
openclaw config set plugins.entries.openviking.config.baseUrl http://your-server:1933
openclaw config set plugins.entries.openviking.config.apiKey your-api-key
```

</details>

## 卸载

```bash
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-plugin/upgrade_scripts/uninstall-openclaw-plugin.sh -o uninstall-openviking.sh
bash uninstall-openviking.sh
```

## 参见

- [完整安装指南](https://github.com/volcengine/OpenViking/blob/main/examples/openclaw-plugin/INSTALL.md) — 所有安装路径与参数
- [插件设计说明](https://github.com/volcengine/OpenViking/blob/main/examples/openclaw-plugin/README.md) — 架构、身份与路由、hook 生命周期
- [Agent 操作指南](https://github.com/volcengine/OpenViking/blob/main/examples/openclaw-plugin/INSTALL-AGENT.md) — 给代用户执行安装的 agent 看
