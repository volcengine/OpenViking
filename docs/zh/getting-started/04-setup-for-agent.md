# OpenViking 安装 SOP（For Agent）

## 目标

帮助用户以最小路径完成 OpenViking 安装、配置、自检和启动。

## 总原则

- 默认走普通用户安装路径，不默认走源码构建
- 默认使用预编译包，不默认要求 Go / Rust / C++ / CMake
- 配置不确定时必须先问用户，不要替用户猜 provider、model、api_base、api_key、workspace
- 只有在安装失败并明确指向本地编译，或用户主动要求源码安装时，才进入源码构建路径

## SOP

### 1. 判断路径

先判断用户属于哪一类：

#### A. 普通最小安装
满足任一情况即可进入：
- 用户只是想安装并跑起来
- 用户只是想体验或接入 OpenViking
- 用户没有要求源码开发
- 用户没有要求修改底层原生组件

执行路径：
1. 安装 Python 包
2. 询问模型配置
3. 生成 `~/.openviking/ov.conf`
4. 运行 `openviking-server doctor`
5. 启动 `openviking-server`

#### B. 本地模型安装（Ollama）
满足任一情况即可进入：
- 用户明确说要本地模型
- 用户明确说要 Ollama
- 用户不想手填大量模型参数

执行路径：
1. 运行 `openviking-server init`
2. 运行 `openviking-server doctor`
3. 启动 `openviking-server`

#### C. Docker 安装
满足任一情况即可进入：
- 用户明确说要用 Docker 安装或运行
- 用户不想在本机直接安装 Python 包
- 用户想把配置和数据通过 volume 挂载持久化

执行路径：
1. 确认用户是否已有现成 `ov.conf`
2. 如果没有，先确认模型配置或引导在容器内运行 `openviking-server init`
3. 使用镜像或 `docker-compose.yml` 启动容器
4. 验证 `/health`

#### D. Windows 安装
满足任一情况即可进入：
- 用户当前在 Windows 环境
- 用户要求 Windows 安装步骤

执行路径：
1. 优先按普通最小安装路径走预编译 wheel
2. 使用 Windows 的环境变量写法配置 `OPENVIKING_CONFIG_FILE`
3. 运行 `openviking-server doctor`
4. 启动 `openviking-server`
5. 只有在 wheel 不可用或安装失败时，才进入 Windows 本地编译路径

#### E. 源码构建
只有以下情况才进入：
- 用户明确要求源码安装
- 安装失败且错误信息明确要求本地编译
- 当前平台没有预编译 wheel
- 用户明确要修改或重编底层原生组件

进入后再说明需要：
- Go 1.22+
- Rust 1.91.1+
- C++ 编译器
- CMake

### 2. 提问

如果用户没有给出完整模型配置，先问，不要直接写配置文件。

#### 必问项

1. 你准备使用哪种模型提供商？
   - `openai`
   - `azure`
   - `volcengine`
   - `openai-codex`
   - `ollama`

2. 你是否已经确定：
   - embedding 模型名
   - VLM 模型名
   - API Key / 鉴权方式

3. `storage.workspace` 想放在哪个目录？

#### 按 provider 继续追问

##### openai
- embedding 模型名
- VLM 模型名
- 是否使用 `https://api.openai.com/v1`
- API Key 是否已准备好

##### azure
- embedding deployment name
- VLM deployment name
- Azure API Base
- Azure API Key
- 是否使用默认 `api_version = 2025-01-01-preview`

##### volcengine
- embedding 模型名
- VLM 模型名
- 是否使用 `https://ark.cn-beijing.volces.com/api/v3`
- API Key 是否已准备好

##### openai-codex
- 是否希望通过 `openviking-server init` 完成 Codex OAuth
- VLM 模型名
- embedding 使用哪个 provider 和模型

##### ollama
- 是否接受直接运行 `openviking-server init`
- 是否已经安装 Ollama
- 希望使用哪些本地 embedding / VLM 模型

#### Docker 额外必问项

如果用户选择 Docker，还要继续确认：
- 用户是想用 `docker run` 还是 `docker compose`
- 本机是否已有 `~/.openviking/ov.conf`
- 是否要把宿主机 `~/.openviking` 挂载到容器 `/app/.openviking`
- 是否要直接通过环境变量 `OPENVIKING_CONF_CONTENT` 注入完整 JSON 配置

#### Windows 额外必问项

如果用户在 Windows，还要继续确认：
- 用户使用的是 PowerShell 还是 cmd.exe
- 用户是否只接受预编译 wheel 安装
- 如果需要本地编译，是否已安装 CMake 和 MinGW

### 3. 生成配置

只有在用户确认完必要信息后，才能写 `~/.openviking/ov.conf`。

#### 最小配置结构

```json
{
  "storage": {
    "workspace": "..."
  },
  "embedding": {
    "dense": {
      "provider": "...",
      "api_base": "...",
      "api_key": "...",
      "model": "..."
    }
  },
  "vlm": {
    "provider": "...",
    "api_base": "...",
    "api_key": "...",
    "model": "..."
  }
}
```

#### 可选字段

只有在 provider 需要、README 示例明确包含、或用户明确要求时，才加入：
- `dimension`
- `api_version`
- `max_concurrent`
- `temperature`
- `max_retries`

#### 不要做的事

- 不要填假密钥
- 不要填用户未确认的路径
- 不要把 README 注释复制进 JSON
- 不要替用户猜模型名或私有 API 地址

### 4. 执行命令

#### 路径 A：普通最小安装

```bash
pip install openviking --upgrade --force-reinstall
```

用户确认配置后写入 `~/.openviking/ov.conf`，然后执行：

```bash
openviking-server doctor
openviking-server
```

#### 路径 B：本地模型安装（Ollama）

```bash
openviking-server init
openviking-server doctor
openviking-server
```

#### 路径 C：Docker 安装

##### 方案 1：使用现成镜像直接运行

如果用户已有本机配置目录，优先建议：

```bash
docker run --rm \
  -p 1933:1933 \
  -p 8020:8020 \
  -v ~/.openviking:/app/.openviking \
  ghcr.io/volcengine/openviking:latest
```

说明：
- 容器内默认配置路径是 `/app/.openviking/ov.conf`
- 容器内 `HOME=/app`
- 建议把宿主机 `~/.openviking` 挂载到容器 `/app/.openviking` 持久化配置、CLI 配置和 workspace 数据

##### 方案 2：使用 `docker-compose.yml`

如果用户希望使用 compose，仓库里已有示例：
- 镜像：`ghcr.io/volcengine/openviking:latest`
- 端口：`1933:1933`、`8020:8020`
- volume：`~/.openviking:/app/.openviking`

此时直接让用户基于仓库根目录执行：

```bash
docker compose up -d
```

##### 方案 3：容器内初始化配置

如果用户还没有 `ov.conf`，可以二选一：

1. 先在宿主机生成并挂载进容器
2. 启动容器后进入容器内执行：

```bash
docker exec -it openviking openviking-server init
```

Dockerfile 还支持在首次启动时通过 `OPENVIKING_CONF_CONTENT` 注入完整 JSON；如果用户明确想这样做，可以采用，但前提仍是配置值已确认。

##### Docker 验证

启动后验证：

```bash
curl http://localhost:1933/health
```

#### 路径 D：Windows 安装

优先按预编译 wheel 路径执行：

```bat
pip install openviking --upgrade --force-reinstall
```

配置文件写好后，按用户 shell 设置环境变量。

##### PowerShell

```powershell
$env:OPENVIKING_CONFIG_FILE = "$HOME/.openviking/ov.conf"
```

##### cmd.exe

```bat
set "OPENVIKING_CONFIG_FILE=%USERPROFILE%\.openviking\ov.conf"
```

然后执行：

```bat
openviking-server doctor
openviking-server
```

如果用户还要配置 CLI 文件：

##### PowerShell

```powershell
$env:OPENVIKING_CLI_CONFIG_FILE = "$HOME/.openviking/ovcli.conf"
```

##### cmd.exe

```bat
set "OPENVIKING_CLI_CONFIG_FILE=%USERPROFILE%\.openviking\ovcli.conf"
```

#### 路径 E：源码构建

只有进入源码构建路径后，才向用户说明并准备 Go / Rust / C++ / CMake。

### 5. 失败分流

#### 情况 1：配置文件缺失、路径错误或 JSON 无法解析

优先检查：
- `~/.openviking/ov.conf` 是否存在
- 是否通过环境变量或 `--config` 指向了错误路径
- 配置文件是否是合法 JSON

处理原则：
- 先修正配置文件路径或 JSON 语法
- 再重新运行 `openviking-server doctor`

#### 情况 2：模型配置不完整

典型表现：
- 缺少 embedding 或 VLM 配置
- 缺少 `provider` / `model` / `api_key`
- `openai-codex` 只配了 VLM，但 embedding 没配

处理原则：
- 先补齐最小配置
- 不要替用户猜模型名或密钥
- 如果是 `openai-codex`，提醒用户它主要解决 VLM，embedding 仍需单独确认

#### 情况 3：模型服务不可连通或鉴权不可用

优先检查：
- API Base 是否正确
- API Key / 鉴权方式是否正确
- 如果是 `openai-codex`，是否已经通过 `openviking-server init` 完成 OAuth
- 如果是 Ollama，服务是否已启动

处理原则：
- 先修正 provider 配置和鉴权状态
- 对 Ollama 优先建议：

```bash
openviking-server init
```

- 然后重新运行：

```bash
openviking-server doctor
```

#### 情况 4：本地依赖或打包产物不可用

典型表现：
- 原生引擎模块无法导入
- AGFS / RAGFS 相关绑定不可用
- 安装后缺少打包产物

处理原则：
- 先执行一次标准重装：

```bash
pip install openviking --upgrade --force-reinstall
```

- 如果仍失败，再判断是否需要进入源码构建路径
- 不要一开始就默认要求用户安装整套本地构建工具链

#### 情况 5：安装过程进入源码编译

优先确认是否属于：
- 当前平台没有对应 wheel
- 用户本来就在走源码安装
- 预编译产物缺失

处理原则：
- 只有确认进入源码构建路径后，才补充 Go / Rust / C++ / CMake
- Windows 本地编译优先补 CMake 和 MinGW
- 不要把源码构建依赖当作普通安装默认前置

#### 情况 6：Windows 安装失败

优先按这个顺序判断：
1. 当前 Python / 架构是否命中了预编译 wheel
2. 是否实际进入了源码编译路径
3. 环境变量是否按 PowerShell 或 cmd.exe 正确设置
4. 如果进入本地编译，是否缺少 CMake / MinGW

处理原则：
- 优先修正 wheel、路径和环境变量问题
- 只有明确进入本地编译路径时，才补装构建依赖

#### 情况 7：Docker 启动后不可用

优先检查：
- `~/.openviking` 是否正确挂载到 `/app/.openviking`
- 容器内是否存在 `/app/.openviking/ov.conf`
- 模型配置是否完整
- `curl http://localhost:1933/health` 是否返回正常
- 是否需要进入容器执行 `openviking-server init`

处理原则：
- 先修正 volume 挂载和配置文件
- 再检查 provider、模型和鉴权配置

#### 情况 8：用户不知道怎么选模型

先不要写配置。

引导规则：
- 用户已有某家云服务账号，就优先沿用该 provider
- 用户想本地运行，就优先建议 Ollama + `openviking-server init`
- 用户想用 `openai-codex`，提醒它主要解决 VLM，embedding 仍需单独确认

## 其他详细参考
- [OpenViking 官方GitHub 仓库](https://github.com/volcengine/OpenViking)
