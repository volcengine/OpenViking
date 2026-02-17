# Docker 部署

本目录包含 vikingbot 的 Docker 部署相关文件。

## 文件说明

| 文件 | 说明 |
|------|------|
| `Dockerfile` | 优化后的 Dockerfile |
| `push-to-acr.sh` | 一键推送到火山引擎 ACR 的脚本 |
| `init-config.sh` | 初始化本地配置文件 |
| `config.example.json` | vikingbot 配置示例 |
| `.acr_env.example` | ACR 环境变量示例 |
| `DEPLOY.md` | 远程容器部署详细指南 |

---

## 快速开始

### 1. 推送镜像到 ACR

```bash
# 配置 ACR 环境变量
cp docker/.acr_env.example ~/.acr_env
vim ~/.acr_env

# 一键构建并推送
./docker/push-to-acr.sh
```

### 2. 初始化本地配置

```bash
# 初始化配置文件
./docker/init-config.sh

# 编辑配置，填入 API keys
vim ~/.vikingbot/config.json
```

### 3. 本地运行测试（推荐一键脚本）

```bash
# 一键启动（推荐）
./docker/run-local.sh

# 或者手动操作：
# 构建镜像
docker build -f docker/Dockerfile -t vikingbot:latest .

# 初始化配置（首次）
docker run -v ~/.vikingbot:/root/.vikingbot --rm vikingbot:latest onboard

# 运行 gateway
docker run -d \
  --name vikingbot \
  -v ~/.vikingbot:/root/.vikingbot \
  -p 18790:18790 \
  vikingbot:latest gateway
```

### 4. 一键脚本说明

| 脚本 | 说明 |
|------|------|
| `run-local.sh` | 一键启动本地容器（自动检查镜像、初始化配置、启动容器） |
| `stop-local.sh` | 停止并清理本地容器 |
| `init-config.sh` | 初始化配置文件 |
| `push-to-acr.sh` | 推送到火山引擎 ACR |

**使用示例：**

```bash
# 一键启动
./docker/run-local.sh

# 停止容器
./docker/stop-local.sh

# 自定义配置启动
CONTAINER_NAME=my-bot COMMAND=agent ./docker/run-local.sh
```

**环境变量配置（可选）：**

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `CONTAINER_NAME` | `vikingbot` | 容器名称 |
| `IMAGE_NAME` | `vikingbot` | 镜像名称 |
| `IMAGE_TAG` | `latest` | 镜像标签 |
| `HOST_PORT` | `18790` | 主机端口 |
| `COMMAND` | `gateway` | 启动命令 |

---

## 远程容器部署

详细步骤请查看 [DEPLOY.md](./DEPLOY.md)

### 快速步骤：

1. 推送镜像到 ACR（已完成）
2. 准备配置文件（使用 `init-config.sh`）
3. 在火山引擎创建云盘（持久卷）
4. 创建容器实例，挂载云盘到 `/root/.vikingbot`
5. 上传配置文件到云盘
6. 启动容器

---

## 配置说明

### vikingbot 配置 (`~/.vikingbot/config.json`)

参考 [config.example.json](./config.example.json)

### ACR 配置 (`~/.acr_env`)

参考 [.acr_env.example](./.acr_env.example)
