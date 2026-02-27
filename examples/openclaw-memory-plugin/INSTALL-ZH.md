# 为 OpenClaw 安装 OpenViking 记忆功能

通过 [OpenViking](https://github.com/volcengine/OpenViking) 为 [OpenClaw](https://github.com/openclaw/openclaw) 提供长效记忆能力。安装完成后，OpenClaw 将自动**记住**对话中的重要信息，并在回复前**回忆**相关内容。

---

## 一、快速开始（让 OpenClaw 自动安装）

先将技能文件复制到 OpenClaw 技能目录，再让 OpenClaw 完成后续步骤：

**Linux / macOS：**

```bash
mkdir -p ~/.openclaw/skills/install-openviking-memory
cp examples/openclaw-memory-plugin/skills/install-openviking-memory/SKILL.md \
   ~/.openclaw/skills/install-openviking-memory/
```

**Windows (cmd)：**

```cmd
mkdir "%USERPROFILE%\.openclaw\skills\install-openviking-memory"
copy examples\openclaw-memory-plugin\skills\install-openviking-memory\SKILL.md ^
     "%USERPROFILE%\.openclaw\skills\install-openviking-memory\"
```

然后对 OpenClaw 说：**「安装 OpenViking 记忆」** — 它会读取技能并自动完成安装。

如需手动安装，请继续阅读。

---

## 二、环境要求

### 总览

| 组件 | 版本要求 | 用途 | 必需？ |
|------|----------|------|--------|
| **Python** | >= 3.10 | OpenViking 运行时 | 是 |
| **Node.js** | >= 22 | OpenClaw 运行时 + 安装助手 | 是 |
| **cmake** | — | 编译 C++ 扩展（OpenViking + OpenClaw 的 node-llama-cpp） | 是 |
| **g++ (gcc-c++)** | — | C++ 编译器 | 是 |
| **Go** | >= 1.25 | 编译 AGFS 服务端（仅 Linux 源码安装） | 源码安装必需 |
| **火山引擎 Ark API Key** | — | Embedding + VLM 模型调用 | 是 |

> **PyPI 安装 vs 源码安装：**
> - `pip install openviking`（PyPI 预编译包）：只需 Python、cmake、g++，**不需要 Go**
> - `pip install -e .`（源码安装）：需要 Python、cmake、g++ **以及 Go >= 1.25**（Linux 上编译 AGFS）
> - **Windows** 用户可直接使用预编译 wheel 包，无需 Go

### 快速检查

```bash
python3 --version     # >= 3.10
node -v               # >= v22
cmake --version       # 已安装
g++ --version         # 已安装
go version            # >= go1.25（源码安装需要）
```

如果以上命令均正常，可跳过"环境准备"直接进入[第三节：安装步骤](#三安装步骤)。

---

## 三、环境准备（Linux）

> 如果你的系统已满足上述环境要求，可跳过此节。

### 3.1 安装编译工具

> 已安装？运行 `cmake --version && g++ --version`，如果都有输出则跳过此步。

**RHEL / CentOS / openEuler / Fedora：**

```bash
sudo dnf install -y gcc gcc-c++ cmake make
```

**Ubuntu / Debian：**

```bash
sudo apt update
sudo apt install -y build-essential cmake
```

### 3.2 安装 Python 3.10+

> 已安装？运行 `python3 --version`，如果显示 >= 3.10 则跳过此步。

许多 Linux 发行版（如 openEuler 22.03、CentOS 7/8）自带 Python 3.9 或更低版本，且软件仓库中往往没有 Python 3.10+ 的包。推荐从源码编译。

#### 方式一：从源码编译（推荐）

```bash
# 1. 安装编译依赖
# RHEL / CentOS / openEuler / Fedora：
sudo dnf install -y gcc make openssl-devel bzip2-devel libffi-devel \
    zlib-devel readline-devel sqlite-devel xz-devel tk-devel

# Ubuntu / Debian：
# sudo apt install -y build-essential libssl-dev libbz2-dev libffi-dev \
#     zlib1g-dev libreadline-dev libsqlite3-dev liblzma-dev tk-dev

# 2. 下载并编译
cd /tmp
curl -O https://www.python.org/ftp/python/3.11.12/Python-3.11.12.tgz
tar xzf Python-3.11.12.tgz
cd Python-3.11.12
./configure --prefix=/usr/local --enable-optimizations --enable-shared \
    LDFLAGS="-Wl,-rpath /usr/local/lib"
make -j$(nproc)
sudo make altinstall

# 3. 创建软链接，使 python3 / pip3 指向新版本
sudo ln -sf /usr/local/bin/python3.11 /usr/local/bin/python3
sudo ln -sf /usr/local/bin/pip3.11 /usr/local/bin/pip3

# 4. 验证
python3 --version   # 确认 >= 3.10 即可
```

> **提示：** 使用 `altinstall` 而非 `install`，避免覆盖系统默认 Python。`/usr/local/bin` 在 `PATH` 中通常优先于 `/usr/bin`，创建软链接后 `python3` 即指向新版本。

#### 方式二：通过包管理器安装（部分发行版可用）

```bash
# RHEL / CentOS / openEuler / Fedora（不一定可用）
sudo dnf install -y python3.11 python3.11-devel python3.11-pip

# Ubuntu 22.04+ 自带 Python 3.10
sudo apt install -y python3 python3-dev python3-pip python3-venv

# Ubuntu 20.04 或更旧版本，需添加 PPA
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt install -y python3.11 python3.11-dev python3.11-venv
```

> 如果 `dnf install python3.11` 报 `No match for argument`，说明仓库中没有该包，请使用源码编译方式。

安装完成后升级 pip：

```bash
python3 -m pip install --upgrade pip
```

> 下载 Python 包较慢？参见[附录：网络加速](#七网络加速镜像与代理配置)配置 pip 镜像。

### 3.3 安装 Node.js >= 22

> 已安装？运行 `node -v`，如果显示 >= v22 则跳过此步。

OpenClaw 要求 Node.js >= 22。安装助手脚本也依赖 Node.js 运行。

#### 方式一：通过 NodeSource 安装（推荐）

```bash
# RHEL / CentOS / openEuler / Fedora
curl -fsSL https://rpm.nodesource.com/setup_22.x | sudo bash -
sudo dnf install -y nodejs

# Ubuntu / Debian
# curl -fsSL https://deb.nodesource.com/setup_22.x | sudo bash -
# sudo apt install -y nodejs
```

#### 方式二：通过 nvm 安装（无需 root 权限）

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
source ~/.bashrc
nvm install 22
nvm use 22
```

#### 方式三：手动下载二进制包

```bash
wget https://nodejs.org/dist/v22.14.0/node-v22.14.0-linux-x64.tar.xz
sudo tar -C /usr/local -xJf node-v22.14.0-linux-x64.tar.xz
echo 'export PATH=$PATH:/usr/local/node-v22.14.0-linux-x64/bin' >> ~/.bashrc
source ~/.bashrc
```

> ARM 架构请将 `linux-x64` 替换为 `linux-arm64`。

验证：

```bash
node -v   # >= v22
npm -v
```

### 3.4 安装 Go >= 1.25（仅源码安装需要）

> 已安装？运行 `go version`，如果显示 >= go1.25 则跳过此步。
> 使用 `pip install openviking`（PyPI 预编译包）的用户也可跳过。

Linux 源码安装 OpenViking 时需要 Go 编译 AGFS 服务端。

```bash
# 下载（ARM 请替换为 go1.25.6.linux-arm64.tar.gz）
wget https://go.dev/dl/go1.25.6.linux-amd64.tar.gz

# 解压
sudo rm -rf /usr/local/go
sudo tar -C /usr/local -xzf go1.25.6.linux-amd64.tar.gz

# 配置环境变量
cat >> ~/.bashrc << 'EOF'
export GOROOT=/usr/local/go
export GOPATH=$HOME/go
export PATH=$PATH:$GOROOT/bin:$GOPATH/bin
EOF
source ~/.bashrc

# 验证
go version   # >= go1.25
```

> Go 模块下载慢？参见[附录：网络加速](#七网络加速镜像与代理配置)配置 GOPROXY。

### 3.5 验证环境就绪

```bash
python3 --version     # >= 3.10
node -v               # >= v22
cmake --version       # 已安装
g++ --version         # 已安装
go version            # >= go1.25（源码安装需要）
```

全部通过后即可开始安装。

---

## 四、安装步骤

### 4.1 安装 OpenClaw

> **前置条件：** cmake 和 g++ 必须已安装（OpenClaw 依赖 `node-llama-cpp`，安装时需编译 C++ 代码）。

```bash
npm install -g openclaw
```

> 下载慢？参见[附录：网络加速](#七网络加速镜像与代理配置)配置 npm 镜像。

运行引导程序配置 LLM：

```bash
openclaw onboard
```

验证：

```bash
openclaw --version
```

### 4.2 安装 OpenViking

```bash
git clone https://github.com/volcengine/OpenViking.git
cd OpenViking
```

#### 方式 A：从 PyPI 安装（推荐，无需 Go）

```bash
python3 -m pip install openviking
```

#### 方式 B：从源码安装（开发者模式，需要 Go）

**Linux / macOS：**

```bash
go version && cmake --version && g++ --version   # 确认工具已安装
python3 -m pip install -e .
```

**Windows：**

```powershell
python -m pip install -e .
```

> **提示：** Linux 源码安装**必须**安装 Go >= 1.25（编译 AGFS）。如确需跳过（高级用户）：
> ```bash
> OPENVIKING_SKIP_AGFS_BUILD=1 python3 -m pip install -e .
> ```

验证：

```bash
python3 -c "import openviking; print('ok')"
```

### 4.3 运行安装助手

在 OpenViking 仓库根目录下执行：

```bash
npx ./examples/openclaw-memory-plugin/setup-helper
```

安装助手会依次完成：

1. **环境检查** — 校验 cmake、g++、Python、Go、OpenClaw 是否就绪
2. **安装 OpenViking**（如尚未安装）
3. **交互配置** — 提示输入以下信息：
   - 数据存储路径（默认为绝对路径，如 `/home/yourname/.openviking/data`）
   - 火山引擎 Ark API Key
   - VLM 模型名称（默认 `doubao-seed-1-8-251228`）
   - Embedding 模型名称（默认 `doubao-embedding-vision-250615`）
   - 服务端口（默认 1933 / 1833）
4. **生成配置** — 创建 `~/.openviking/ov.conf`
5. **部署插件** — 将 `memory-openviking` 注册到 OpenClaw
6. **写入环境文件** — 生成 `~/.openclaw/openviking.env`

> 非交互模式：`npx ./examples/openclaw-memory-plugin/setup-helper -y`

### 4.4 启动并验证

**Linux / macOS：**

```bash
source ~/.openclaw/openviking.env && openclaw gateway
```

**Windows (cmd)：**

```cmd
call "%USERPROFILE%\.openclaw\openviking.env.bat" && openclaw gateway
```

看到以下输出表示安装成功：

```
[gateway] listening on ws://127.0.0.1:18789
[gateway] memory-openviking: local server started (http://127.0.0.1:1933, config: ...)
```

检查插件状态：

```bash
openclaw status
# Memory 行应显示：enabled (plugin memory-openviking)
```

测试记忆功能：

```bash
openclaw tui
```

输入：「请记住：我最喜欢的编程语言是 Python。」

在新对话中问：「我最喜欢的编程语言是什么？」

OpenClaw 应能从记忆中回忆并回答。

---

## 五、日常使用

每次使用带记忆功能的 OpenClaw：

**Linux / macOS：**

```bash
source ~/.openclaw/openviking.env && openclaw gateway
```

**Windows (cmd)：**

```cmd
call "%USERPROFILE%\.openclaw\openviking.env.bat" && openclaw gateway
```

> **便捷方式（Linux/macOS）：** 加入 `~/.bashrc`：
> ```bash
> alias openclaw-start='source ~/.openclaw/openviking.env && openclaw gateway'
> ```

插件会自动启动和停止 OpenViking 服务。

---

## 六、配置参考

### `~/.openviking/ov.conf`

```json
{
  "server": {
    "host": "127.0.0.1",
    "port": 1933
  },
  "storage": {
    "workspace": "/home/yourname/.openviking/data",
    "vectordb": { "backend": "local" },
    "agfs": { "backend": "local", "port": 1833 }
  },
  "embedding": {
    "dense": {
      "backend": "volcengine",
      "api_key": "<your-api-key>",
      "model": "doubao-embedding-vision-250615",
      "api_base": "https://ark.cn-beijing.volces.com/api/v3",
      "dimension": 1024,
      "input": "multimodal"
    }
  },
  "vlm": {
    "backend": "volcengine",
    "api_key": "<your-api-key>",
    "model": "doubao-seed-1-8-251228",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3",
    "temperature": 0.1,
    "max_retries": 3
  }
}
```

> **注意：** `workspace` 必须使用**绝对路径**（如 `/home/yourname/.openviking/data`），不支持 `~` 或相对路径。安装助手会自动获取并填入。

### `~/.openclaw/openviking.env`

由安装助手自动生成：

```bash
export OPENVIKING_PYTHON='/usr/local/bin/python3'
export OPENVIKING_GO_PATH='/usr/local/go/bin'  # 可选
```

Windows 版（`openviking.env.bat`）：

```cmd
set OPENVIKING_PYTHON=C:\path\to\python.exe
set OPENVIKING_GO_PATH=C:\path\to\go\bin
```

### 安装助手选项

```
npx ./examples/openclaw-memory-plugin/setup-helper [选项]

  -y, --yes     非交互模式，使用默认值
  -h, --help    显示帮助

环境变量：
  OPENVIKING_PYTHON       Python 解释器路径
  OPENVIKING_CONFIG_FILE  自定义 ov.conf 路径
  OPENVIKING_REPO         本地仓库路径（在仓库内运行时自动检测）
  OPENVIKING_ARK_API_KEY  跳过 API Key 提示（用于 CI/脚本）
```

---

## 七、常见问题

### 安装阶段

#### Q: `cmake not found` / `g++ not found`（安装 OpenClaw 或 OpenViking 时）

OpenClaw 依赖 `node-llama-cpp`（需编译 C++），OpenViking 的 C++ 扩展也需要 cmake/g++。

```bash
# RHEL / CentOS / openEuler
sudo dnf install -y gcc gcc-c++ cmake make

# Ubuntu / Debian
sudo apt install -y build-essential cmake
```

#### Q: `No matching distribution found for python-multipart>=0.0.22`

pip 使用了 Python 3.9 解释器。请确认使用 Python 3.10+：

```bash
python3 --version        # 确认 >= 3.10
python3 -m pip install -e .
```

#### Q: `fatal error: Python.h: No such file or directory`

缺少 Python 开发头文件：

```bash
# RHEL / CentOS / openEuler
sudo dnf install -y python3-devel   # 或 python3.11-devel

# Ubuntu / Debian
sudo apt install -y python3-dev     # 或 python3.11-dev
```

> 如果是源码编译的 Python，开发头文件已包含在内，无需额外安装。

#### Q: `Go compiler not found` / AGFS 构建失败

Linux 源码安装**必须**安装 Go >= 1.25，参见 [3.4 安装 Go](#34-安装-go--125仅源码安装需要)。

```bash
go version              # 确认 >= 1.25
python3 -m pip install -e .
```

#### Q: Go 模块下载超时（`dial tcp: i/o timeout`）

配置 Go 代理，参见[附录：网络加速](#七网络加速镜像与代理配置)。

#### Q: npm 安装报 `ERR_INVALID_URL`

通常是代理环境变量格式错误。代理地址**必须**包含 `http://` 前缀：

```bash
# 错误
export https_proxy=192.168.1.1:7897

# 正确
export https_proxy=http://192.168.1.1:7897
```

或清除代理：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY
```

#### Q: npm 安装报 `ENOTEMPTY`

上次安装中断留下残留文件，清理后重试：

```bash
rm -rf $(npm root -g)/openclaw $(npm root -g)/.openclaw-*
npm install -g openclaw
```

### 运行阶段

#### Q: 网关输出中看不到 `memory-openviking` 插件

- 是否在 `openclaw gateway` 之前加载了环境变量？
  - Linux/macOS：`source ~/.openclaw/openviking.env`
  - Windows：`call "%USERPROFILE%\.openclaw\openviking.env.bat"`
- 运行 `openclaw status` 检查插件状态
- 重新执行安装助手：`npx ./examples/openclaw-memory-plugin/setup-helper`

#### Q: `health check timeout at http://127.0.0.1:1933`

端口被旧进程占用，清理后重启：

```bash
# Linux / macOS
lsof -ti tcp:1933 tcp:1833 | xargs kill -9
source ~/.openclaw/openviking.env && openclaw gateway
```

```cmd
REM Windows
for /f "tokens=5" %a in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":1933 :1833"') do taskkill /PID %a /F
call "%USERPROFILE%\.openclaw\openviking.env.bat" && openclaw gateway
```

#### Q: `extracted 0 memories`

`ov.conf` 中的模型配置有误，请检查：

- `embedding.dense.api_key` 为有效的火山引擎 Ark API Key
- `vlm.api_key` 已设置（通常与 embedding 相同）
- `vlm.model` 为模型名称（如 `doubao-seed-1-8-251228`），**不是** API Key

### Python 版本问题

#### Q: `TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'`

Python 版本低于 3.10（`X | None` 语法是 3.10 引入的），需升级 Python，参见 [3.2 安装 Python](#32-安装-python-310)。

#### Q: `pip install -e .` 安装到了错误的 Python

使用显式路径指定 Python：

```bash
python3.11 -m pip install -e .
export OPENVIKING_PYTHON=python3.11
npx ./examples/openclaw-memory-plugin/setup-helper
```

---

## 八、网络加速（镜像与代理配置）

> 以下配置适用于下载速度较慢的网络环境。

### pip 镜像

```bash
# 永久配置（推荐）
python3 -m pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
python3 -m pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn

# 其他可选镜像
# 阿里云：https://mirrors.aliyun.com/pypi/simple/
# 华为云：https://repo.huaweicloud.com/repository/pypi/simple/
# 腾讯云：https://mirrors.cloud.tencent.com/pypi/simple/
```

单次安装时临时指定：

```bash
python3 -m pip install openviking -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### npm 镜像

```bash
# 永久配置（推荐）
npm config set registry https://registry.npmmirror.com

# 单次指定
npm install -g openclaw --registry=https://registry.npmmirror.com
```

### Go 代理

```bash
# 七牛云（推荐）
go env -w GOPROXY=https://goproxy.cn,direct

# 阿里云
# go env -w GOPROXY=https://mirrors.aliyun.com/goproxy/,direct

# 关闭校验（部分私有模块可能需要）
go env -w GONOSUMCHECK=*

# 验证
go env GOPROXY
```

> 配置后全局生效，后续 `pip install -e .` 编译 AGFS 时会自动使用。

---

## 九、卸载

**Linux / macOS：**

```bash
# 停止服务
lsof -ti tcp:1933 tcp:1833 tcp:18789 | xargs kill -9

# 卸载 OpenClaw
npm uninstall -g openclaw
rm -rf ~/.openclaw

# 卸载 OpenViking
python3 -m pip uninstall openviking -y
rm -rf ~/.openviking
```

**Windows (cmd)：**

```cmd
REM 停止服务
for /f "tokens=5" %a in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":1933 :1833 :18789"') do taskkill /PID %a /F

REM 卸载 OpenClaw
npm uninstall -g openclaw
rmdir /s /q "%USERPROFILE%\.openclaw"

REM 卸载 OpenViking
python -m pip uninstall openviking -y
rmdir /s /q "%USERPROFILE%\.openviking"
```

---

**另见：** [INSTALL.md](./INSTALL.md)（English version）
