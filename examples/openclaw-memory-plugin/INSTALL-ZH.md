# 为 OpenClaw 安装 OpenViking 记忆功能

通过 [OpenViking](https://github.com/volcengine/OpenViking) 为 [OpenClaw](https://github.com/openclaw/openclaw) 提供长效记忆能力。安装完成后，OpenClaw 将自动**记住**对话中的重要信息，并在回复前**回忆**相关内容。

---

## 一键安装

**前置条件：** Python >= 3.10，Node.js >= 22。安装助手会自动检查并提示安装缺少的组件。

### 方式 A：npm 安装（推荐，全平台）

```bash
npm install -g openclaw-openviking-setup-helper
ov-install
```

非交互模式（使用默认配置）：

```bash
ov-install -y
```

安装到指定 OpenClaw 实例：

```bash
ov-install --workdir ~/.openclaw-second
```

### 方式 B：curl 一键安装（Linux / macOS）

```bash
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-memory-plugin/install.sh | bash
```

非交互模式：

```bash
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/openclaw-memory-plugin/install.sh | bash -s -y
```

安装到指定 OpenClaw 实例：

```bash
curl -fsSL ... | bash -s -- --workdir ~/.openclaw-openclaw-second
```

脚本会自动检测多个 OpenClaw 实例并让你选择。还会提示选择 local/remote 模式——remote 模式连接远端 OpenViking 服务，不需要安装 Python。

---

## 前置条件

| 组件 | 版本要求 | 用途 |
|------|----------|------|
| **Python** | >= 3.10 | OpenViking 运行时 |
| **Node.js** | >= 22 | OpenClaw 运行时 |
| **火山引擎 Ark API Key** | — | Embedding + VLM 模型调用 |

快速检查：

```bash
python3 --version   # >= 3.10
node -v              # >= v22
openclaw --version   # 已安装
```

- Python: https://www.python.org/downloads/
- Node.js: https://nodejs.org/
- OpenClaw: `npm install -g openclaw && openclaw onboard`

---

## 方式一：本地部署（推荐）

在本机启动 OpenViking 服务，适合个人使用。

### Step 1: 安装 OpenViking

```bash
python3 -m pip install openviking --upgrade
```

验证：`python3 -c "import openviking; print('ok')"`

> 遇到 `externally-managed-environment`？使用一键安装脚本（自动处理 venv）或手动创建：
> `python3 -m venv ~/.openviking/venv && ~/.openviking/venv/bin/pip install openviking`

### Step 2: 运行安装助手

#### 方式 A：npm 全局安装（推荐）

```bash
npm install -g openclaw-openviking-setup-helper
ov-install
```

#### 方式 B：从仓库运行

```bash
git clone https://github.com/volcengine/OpenViking.git
cd OpenViking
npx ./examples/openclaw-memory-plugin/setup-helper
```

安装助手会提示输入 Ark API Key 并自动生成配置文件。

### Step 3: 启动

```bash
source ~/.openclaw/openviking.env && openclaw gateway
```

看到 `memory-openviking: local server started` 表示成功。

### Step 4: 验证

```bash
openclaw status
# Memory 行应显示：enabled (plugin memory-openviking)
```

---

## 方式二：连接远端 OpenViking

已有运行中的 OpenViking 服务？只需配置 OpenClaw 插件指向远端，**不需要安装 Python / OpenViking**。

**前置：** 已有 OpenViking 服务地址 + API Key（如服务端启用了认证）。

### Step 1: 部署插件代码

```bash
git clone https://github.com/volcengine/OpenViking.git
cd OpenViking/examples/openclaw-memory-plugin
npm install
openclaw plugin link .
```

### Step 2: 配置远端连接

```bash
openclaw config set plugins.enabled true --json
openclaw config set plugins.slots.memory memory-openviking
openclaw config set plugins.entries.memory-openviking.config.mode remote
openclaw config set plugins.entries.memory-openviking.config.baseUrl "http://your-server:1933"
openclaw config set plugins.entries.memory-openviking.config.apiKey "your-api-key"
openclaw config set plugins.entries.memory-openviking.config.autoRecall true --json
openclaw config set plugins.entries.memory-openviking.config.autoCapture true --json
```

### Step 3: 启动并验证

```bash
openclaw gateway
openclaw status
```

---

## 配置参考

### `~/.openviking/ov.conf`（本地模式）

```json
{
  "root_api_key": null,
  "server": { "host": "127.0.0.1", "port": 1933 },
  "storage": {
    "workspace": "/home/yourname/.openviking/data",
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

> `root_api_key`：设置后，所有 HTTP 请求须携带 `X-API-Key` 头。本地模式默认为 `null`（不启用认证）。

### `agentId` 配置（插件配置）

通过 `X-OpenViking-Agent` header 传给服务端的 Agent 标识，用于区分不同的 OpenClaw 实例。

自定义方式：

```bash
# 在插件配置中指定
openclaw config set plugins.entries.memory-openviking.config.agentId "my-agent"
```

如果未配置，插件会自动生成一个随机唯一的 ID（格式：`openclaw-<hostname>-<random>`）。

### `~/.openclaw/openviking.env`

由安装助手自动生成，记录 Python 路径等环境变量：

```bash
export OPENVIKING_PYTHON='/usr/local/bin/python3'
```

---

## 日常使用

```bash
# 启动
source ~/.openclaw/openviking.env && openclaw gateway

# 关闭记忆
openclaw config set plugins.slots.memory none

# 开启记忆
openclaw config set plugins.slots.memory memory-openviking
```

---

## 常见问题

| 症状 | 原因 | 修复 |
|------|------|------|
| `port occupied` | 端口被其他进程占用 | 换端口：`openclaw config set plugins.entries.memory-openviking.config.port 1934` |
| `extracted 0 memories` | API Key 或模型名配置错误 | 检查 `ov.conf` 中 `api_key` 和 `model` 字段 |
| 插件未加载 | 未加载环境变量 | 启动前执行 `source ~/.openclaw/openviking.env` |
| `externally-managed-environment` | Python PEP 668 限制 | 使用 venv 或一键安装脚本 |
| `TypeError: unsupported operand type(s) for \|` | Python < 3.10 | 升级 Python 至 3.10+ |

---

## 卸载

```bash
lsof -ti tcp:1933 tcp:1833 tcp:18789 | xargs kill -9
npm uninstall -g openclaw && rm -rf ~/.openclaw
python3 -m pip uninstall openviking -y && rm -rf ~/.openviking
```

---

**另见：** [INSTALL.md](./INSTALL.md)（English） · [INSTALL-AGENT.md](./INSTALL-AGENT.md)（Agent Install Guide）
