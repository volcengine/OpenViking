# 为 OpenClaw 安装 OpenViking 记忆功能

通过 [OpenViking](https://github.com/volcengine/OpenViking) 为 [OpenClaw](https://github.com/openclaw/openclaw) 提供长效记忆能力。安装完成后，OpenClaw 将自动**记住**对话中的重要信息，并在回复前**回忆**相关内容。OpenViking最新版本发布[WebConsole](https://github.com/volcengine/OpenViking/tree/main/openviking/console),方便调试和运维。文档方式三也为大家提供了如何在WebConsole界面验证记忆被写入，提供好用易懂的使用说明，欢迎大家试用和反馈。

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
## 方式三 火山引擎 ECS 版 Openclaw 接入 OpenViking

本部分主要介绍如何在火山引擎ECS上接入OpenViking，并使用WebConsole验证写入。详情可见[文档](https://www.volcengine.com/docs/6396/2249500?lang=zh)。

需注意 ECS 实例为了保护系统 Python 不被弄坏，在根目录（root）部署会有限制，不能直接用 pip 装全局包，推荐先创建虚拟环境，在虚拟环境下完成以下操作步骤。

**前置：** 已有 ECS OpenClaw实例。

### Step 1: npm 安装

```python
npm install -g openclaw-openviking-setup-helper
ov-install
```
本安装模式已经在OpenViking内置了vlm和embedding模型，若不需要修改，直接按回车，按照指引填入API key即可. 安装完成后，会自动生成配置文件，如需修改，输入 vim ~/.openviking/ov.conf，按 i 进入编辑模式，按 esc 键退出编辑模式，输入 :wq 按回车键，保存并退出文件。

终端加载 OpenClaw 环境变量：

```bash
source /root/.openclaw/openviking.env
```
### Step 2: 启动OpenViking

先启动 OpenViking Server：

```python
python -m openviking.server.bootstrap
```
然后启动 web 控制台，启动之前，需要确认本实例安全组是否已经在入向规则处开放 TCP 8020 端口，若没有，需先点击实例安全组配置：

```python
python -m openviking.console.bootstrap --host 0.0.0.0 --port 8020 --openviking-url http://127.0.0.1:1933
```
在实例中，找到你的服务器公网IP，用你的服务器公网IP访问: http://你的服务器公网IP:8020

即可开始体验 web console 🎉

你可以直接在web界面查询文件信息，验证OpenViking memory-plugin记忆写入是否生效；也可以可以在OpenClaw日志中验证memory-openviking是否读取记忆，验证方式：


```bash
grep -i inject /tmp/openclaw/openclaw-2026-03-13.log | awk -F'"' '{for(i=1;i<=NF;i++) if($i ~ /^[0-9]{2}:[0-9]{2}:[0-9]{2}/) {time=$i; break}} /injecting [0-9]+ memories/ {print time, "memory-openviking:", gensub(/.*(injecting [0-9]+ memories).*/, "\\1", "1")}'
```

也可以直接运行grep "inject" /tmp/openclaw/openclaw-2026-03-13.log查看全部信息。


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
