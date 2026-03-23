# OpenClaw + OpenViking 上下文引擎插件

使用 [OpenViking](https://github.com/volcengine/OpenViking) 作为 [OpenClaw](https://github.com/openclaw/openclaw) 的长期记忆后端。在 OpenClaw 中，此插件注册为 `openviking` 上下文引擎。安装后，OpenClaw 将自动**记忆**对话中的重要信息，并在响应前**召回**相关上下文。

> **ℹ️ 历史兼容性说明**
>
> 传统的 OpenViking/OpenClaw 集成在 OpenClaw `2026.3.12` 附近存在一个已知问题，即对话可能在插件加载后挂起。
> 该问题仅影响传统插件路径；本文档中描述的当前上下文引擎插件 2.0 不受此影响，因此新安装无需因此降级 OpenClaw。
> 插件 2.0 也不向后兼容传统的 `memory-openviking` 插件及其配置，因此升级必须替换旧设置，而不是混合使用两个版本。
> 插件 2.0 还依赖于 OpenClaw 的上下文引擎功能，不支持旧版 OpenClaw；使用此插件前请先升级 OpenClaw。
> 如果您正在排查传统部署的问题，请参阅 [#591](https://github.com/volcengine/OpenViking/issues/591) 和上游修复 PR：openclaw/openclaw#34673、openclaw/openclaw#33547。

> **🚀 插件 2.0（上下文引擎架构）**
>
> 本文档涵盖当前基于上下文引擎架构的 OpenViking 插件 2.0，这是 AI 编码助手的推荐集成路径。
> 有关设计背景和早期讨论，请参阅：
> https://github.com/volcengine/OpenViking/discussions/525

---

## 目录

- [一键安装](#一键安装)
- [手动设置](#手动设置)
  - [前置要求](#前置要求)
  - [本地模式（个人使用）](#本地模式个人使用)
  - [远程模式（团队共享）](#远程模式团队共享)
  - [火山引擎 ECS 部署](#火山引擎-ecs-部署)
- [启动与验证](#启动与验证)
- [配置参考](#配置参考)
- [日常使用](#日常使用)
- [Web 控制台（可视化）](#web-控制台可视化)
- [故障排除](#故障排除)
- [卸载](#卸载)

---

## 一键安装

适用于想要快速获得本地体验的用户。安装助手会自动处理环境检测、依赖安装和配置文件生成。

### 方法 A：npm 安装（推荐，跨平台）

```bash
npm install -g openclaw-openviking-setup-helper
ov-install
```

### 方法 B：curl 一键安装（Linux / macOS）

```bash
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-plugin/install.sh | bash
```

安装助手将引导您完成：

1. **环境检查** — 检测 Python >= 3.10、Node.js、cmake 等
2. **选择 OpenClaw 实例** — 如果本地安装了多个实例，列出供您选择
3. **选择部署模式** — 本地或远程（见下文）
4. **生成配置** — 自动写入 `~/.openviking/ov.conf` 和 `~/.openclaw/openviking.env`

<details>
<summary>安装助手选项</summary>

```
ov-install [options]

  -y, --yes              非交互式，使用默认值
  --workdir <path>       OpenClaw 配置目录（默认：~/.openclaw）
  -h, --help             显示帮助

环境变量：
  OPENVIKING_PYTHON       Python 路径
  OPENVIKING_CONFIG_FILE  ov.conf 路径
  OPENVIKING_REPO         本地 OpenViking 仓库路径
  OPENVIKING_ARK_API_KEY  火山引擎 API Key（-y 模式下跳过提示）
```

</details>

---

## 手动设置

### 前置要求

| 组件 | 版本 | 用途 |
|-----------|---------|---------|
| **Python** | >= 3.10 | OpenViking 运行时（本地模式） |
| **Node.js** | >= 22 | OpenClaw 运行时 |
| **火山引擎 Ark API Key** | — | Embedding + VLM 模型调用 |

```bash
python3 --version   # >= 3.10
node -v              # >= v22
openclaw --version   # 已安装
```

- Python: https://www.python.org/downloads/
- Node.js: https://nodejs.org/
- OpenClaw: `npm install -g openclaw && openclaw onboard`

---

### 本地模式个人使用

最简单的选项 — 几乎无需配置。记忆服务与您的OpenClaw一起在本地运行。您只需要一个火山引擎Ark API Key。

#### 步骤 1：安装 OpenViking

```bash
python3 -m pip install openviking --upgrade
```

验证：`python3 -c "import openviking; print('ok')"`

> 遇到 `externally-managed-environment`？使用一键安装程序（自动处理 venv）或手动创建一个：
> ```bash
> python3 -m venv ~/.openviking/venv && ~/.openviking/venv/bin/pip install openviking
> ```

#### 步骤 2：运行安装助手

```bash
# 方法 A：npm 安装（推荐，跨平台）
npm install -g openclaw-openviking-setup-helper
ov-install

# 方法 B：curl 一键安装（Linux / macOS）
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-plugin/install.sh | bash
```

选择 **本地** 模式，保持默认设置，并输入您的 Ark API Key。

生成的配置文件：
- `~/.openviking/ov.conf` — OpenViking 服务配置
- `~/.openclaw/openviking.env` — 环境变量（Python 路径等）

#### 步骤 3：启动

```bash
source ~/.openclaw/openviking.env && openclaw gateway restart
```

> 在本地模式下，您必须先 `source` 环境文件 — 插件会自动启动一个 OpenViking子进程。

#### 步骤 4：验证

```bash
openclaw status
# ContextEngine 行应显示：enabled (plugin openviking)
```

---

### 远程模式团队共享

适用于多个OpenClaw实例或团队使用。部署一个独立的OpenViking服务，供多个agents共享。**客户端无需 Python/OpenViking。**

#### 步骤 1：部署 OpenViking 服务

编辑 `~/.openviking/ov.conf` — 设置 `root_api_key` 以启用多租户：

```json
{
  "server": {
    "host": "127.0.0.1",
    "port": 1933,
    "root_api_key": "<your-root-api-key>",
    "cors_origins": ["*"]
  },
  "storage": {
    "workspace": "~/.openviking/data",
    "vectordb": {
      "name": "context",
      "backend": "local"
    },
    "agfs": {
      "log_level": "warn",
      "backend": "local"
    }
  },
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "api_key": "<your-ark-api-key>",
      "model": "doubao-embedding-vision-251215",
      "api_base": "https://ark.cn-beijing.volces.com/api/v3",
      "dimension": 1024,
      "input": "multimodal"
    }
  },
  "vlm": {
    "provider": "volcengine",
    "api_key": "<your-ark-api-key>",
    "model": "doubao-seed-2-0-pro-260215",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3",
    "temperature": 0.1,
    "max_retries": 3
  }
}
```

启动服务：

```bash
openviking-server
```

#### 步骤 2：创建团队和用户

```bash
# 创建团队 + 管理员
curl -X POST http://localhost:1933/api/v1/admin/accounts \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-api-key>" \
  -d '{
    "account_id": "my-team",
    "admin_user_id": "admin"
  }'

# 添加成员
curl -X POST http://localhost:1933/api/v1/admin/accounts/my-team/users \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-or-admin-key>" \
  -d '{
    "user_id": "xiaomei",
    "role": "user"
  }'
```

#### 步骤 3：配置 OpenClaw 插件

```bash
openclaw plugins enable openviking
openclaw config set gateway.mode local
openclaw config set plugins.slots.contextEngine openviking
openclaw config set plugins.entries.openviking.config.mode remote
openclaw config set plugins.entries.openviking.config.baseUrl "http://your-server:1933"
openclaw config set plugins.entries.openviking.config.apiKey "<user-api-key>"
openclaw config set plugins.entries.openviking.config.agentId "<agent-id>"
openclaw config set plugins.entries.openviking.config.autoRecall true --json
openclaw config set plugins.entries.openviking.config.autoCapture true --json
```

#### 步骤 4：启动与验证

```bash
# 远程模式 — 无需 source 环境文件
openclaw gateway restart
openclaw status
```

---

### 火山引擎 ECS 部署

在火山引擎 ECS 上部署 OpenClaw + OpenViking。详见 [火山引擎文档](https://www.volcengine.com/docs/6396/2249500?lang=zh)。

> ECS 实例限制在 root 下全局 pip 安装以保护系统 Python。请先创建 venv。

```bash
# 1. 安装
npm install -g openclaw-openviking-setup-helper
ov-install

# 2. 加载环境
source /root/.openclaw/openviking.env

# 3. 启动 OpenViking 服务器
python -m openviking.server.bootstrap

# 4. 启动 Web 控制台（确保入站安全组允许 TCP 8020）
python -m openviking.console.bootstrap --host 0.0.0.0 --port 8020 --openviking-url http://127.0.0.1:1933
```

访问 `http://<public-ip>:8020` 使用 Web 控制台。

---

## 启动与验证

### 本地模式

```bash
source ~/.openclaw/openviking.env && openclaw gateway restart
```

### 远程模式

```bash
openclaw gateway restart
```

### 检查插件状态

```bash
openclaw status
# ContextEngine 行应显示：enabled (plugin openviking)
```

### 查看插件配置

```bash
openclaw config get plugins.entries.openviking.config
```

---

## 配置参考

### `~/.openviking/ov.conf`（本地模式）

```json
{
  "root_api_key": null,
  "server": { "host": "127.0.0.1", "port": 1933 },
  "storage": {
    "workspace": "~/.openviking/data",
    "vectordb": { "backend": "local" },
    "agfs": { "backend": "local", "port": 1833 }
  },
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "api_key": "<your-ark-api-key>",
      "model": "doubao-embedding-vision-251215",
      "api_base": "https://ark.cn-beijing.volces.com/api/v3",
      "dimension": 1024,
      "input": "multimodal"
    }
  },
  "vlm": {
    "provider": "volcengine",
    "api_key": "<your-ark-api-key>",
    "model": "doubao-seed-2-0-pro-260215",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3"
  }
}
```

> `root_api_key`：设置后，所有 HTTP 请求必须包含 `X-API-Key` 头。本地模式下默认为 `null`（禁用身份验证）。

### 插件配置选项

| 选项 | 默认值 | 描述 |
|--------|---------|-------------|
| `mode` | `remote` | `local`（启动本地服务器）或 `remote`（连接远程服务器） |
| `baseUrl` | `http://127.0.0.1:1933` | OpenViking 服务器 URL（远程模式） |
| `apiKey` | — | OpenViking API Key（可选） |
| `agentId` | 自动生成 | agent标识符，用于区分 OpenClaw 实例。如果未设置则自动生成 `openclaw-<hostname>-<random>` |
| `configPath` | `~/.openviking/ov.conf` | 配置文件路径（本地模式） |
| `port` | `1933` | 本地服务器端口（本地模式） |
| `targetUri` | `viking://user/memories` | 默认记忆搜索范围 |
| `autoCapture` | `true` | 对话后自动提取记忆 |
| `captureMode` | `semantic` | 提取模式：`semantic`（完整语义）/ `keyword`（仅触发词） |
| `captureMaxLength` | `24000` | 每次提取的最大文本长度 |
| `autoRecall` | `true` | 对话前自动召回相关记忆 |
| `recallLimit` | `6` | 自动召回期间注入的最大记忆数 |
| `recallScoreThreshold` | `0.01` | 召回的最低相关性分数 |
| `ingestReplyAssist` | `true` | 检测到多方对话文本时添加回复指导 |

### `~/.openclaw/openviking.env`

由安装助手自动生成，存储环境变量（如 Python 路径）：

```bash
export OPENVIKING_PYTHON='/usr/local/bin/python3'
```

---

## 日常使用

```bash
# 启动（本地模式 — 先 source 环境文件）
source ~/.openclaw/openviking.env && openclaw gateway

# 启动（远程模式 — 无需环境文件）
openclaw gateway

# 禁用上下文引擎
openclaw config set plugins.slots.contextEngine legacy

# 重新启用 OpenViking 作为上下文引擎
openclaw config set plugins.slots.contextEngine openviking
```

> 更改上下文引擎插槽后请重启网关。

---

## Web-控制台可视化

OpenViking 提供 Web 控制台用于调试和检查存储的记忆。

```bash
python -m openviking.console.bootstrap \
  --host 127.0.0.1 \
  --port 8020 \
  --openviking-url http://127.0.0.1:1933 \
  --write-enabled
```

在浏览器中打开 http://127.0.0.1:8020。

---

## 故障排除

### 常见问题

| 症状 | 原因 | 解决方案 |
|---------|-------|-----|
| 对话挂起，无响应 | 通常是受历史 OpenClaw `2026.3.12` 问题影响的 2.0 之前传统集成 | 如果您使用传统路径，请参阅 [#591](https://github.com/volcengine/OpenViking/issues/591) 并临时降级到 `2026.3.11`；对于当前安装，请迁移到插件 2.0 |
| 日志中出现 `registerContextEngine is unavailable` | OpenClaw 版本过旧，未暴露插件 2.0 所需的上下文引擎 API | 升级 OpenClaw 到当前版本，然后重启网关并验证 `openclaw status` 显示 `openviking` 作为 ContextEngine |
| agent静默挂起，无输出 | 自动召回缺少超时保护 | 临时禁用自动召回：`openclaw config set plugins.entries.openviking.config.autoRecall false --json`，或应用 [#673](https://github.com/volcengine/OpenViking/issues/673) 中的补丁 |
| ContextEngine 不是 `openviking` | 插件插槽未配置 | `openclaw config set plugins.slots.contextEngine openviking` |
| `memory_store failed: fetch failed` | OpenViking 未运行 | 检查 `ov.conf` 和 Python 路径；验证服务是否运行 |
| `health check timeout` | 端口被陈旧进程占用 | `lsof -ti tcp:1933 \| xargs kill -9`，然后重启 |
| `extracted 0 memories` | API Key 或模型名称错误 | 检查 `ov.conf` 中的 `api_key` 和 `model` |
| `port occupied` | 端口被其他进程占用 | 更改端口：`openclaw config set plugins.entries.openviking.config.port 1934` |
| 插件未加载 | 环境文件未 source | 启动前运行 `source ~/.openclaw/openviking.env` |
| `externally-managed-environment` | Python PEP 668 限制 | 使用 venv 或一键安装程序 |
| `TypeError: unsupported operand type(s) for \|` | Python < 3.10 | 升级 Python 到 3.10+ |

### 查看日志

```bash
# OpenViking 日志
cat ~/.openviking/data/log/openviking.log

# OpenClaw 网关日志
cat ~/.openclaw/logs/gateway.log
cat ~/.openclaw/logs/gateway.err.log

# 检查 OpenViking 进程是否存活
lsof -i:1933

# 快速连接检查
curl http://localhost:1933
# 预期：{"detail":"Not Found"}
```

---

## 卸载

```bash
lsof -ti tcp:1933 tcp:1833 tcp:18789 | xargs kill -9
npm uninstall -g openclaw && rm -rf ~/.openclaw
python3 -m pip uninstall openviking -y && rm -rf ~/.openviking
```

---

**另见：** [INSTALL-ZH.md](./INSTALL-ZH.md)（中文详细安装指南）· [INSTALL.md](./INSTALL.md)（英文安装指南）· [INSTALL-AGENT.md](./INSTALL-AGENT.md)（Agent 安装指南）
