# Vikingbot Docker 一键部署

本目录提供 Vikingbot 的 Docker 一键部署脚本，支持本地快速部署和多架构支持。

## 前置要求

请先安装 Docker：

- **macOS**: 下载 [Docker Desktop](https://www.docker.com/products/docker-desktop)
- **Windows**: 下载 [Docker Desktop](https://www.docker.com/products/docker-desktop)
- **Linux**: 参考 [Docker 官方文档](https://docs.docker.com/engine/install/)

验证 Docker 安装：
```bash
docker --version
```

## 快速开始

### 从火山引擎镜像部署（推荐）

如果你已经有推送到火山引擎镜像仓库的镜像，可以直接拉取并部署：

```bash
# 1. 创建必要的目录结构
mkdir -p ~/.vikingbot/

# 2. 启动容器
docker run -d \
    --name vikingbot \
    --restart unless-stopped \
    --platform linux/amd64 \
    -v ~/.vikingbot:/root/.vikingbot \
    -p 18791:18791 \
    vikingbot-cn-beijing.cr.volces.com/vikingbot/vikingbot:latest \
    gateway

# 3. 查看日志
docker logs --tail 50 -f vikingbot
```

按 `Ctrl+C` 退出日志查看，容器继续后台运行。

### 本地代码构建镜像部署

如果你想从本地代码构建镜像并部署：

#### 一行命令部署

```bash
./deploy/docker/deploy.sh
```

脚本会自动检测本地架构（arm64/amd64）并构建适配的镜像。

#### 分步部署

##### 1. 构建镜像

```bash
./deploy/docker/build-image.sh
```

##### 2. 部署服务

```bash
./deploy/docker/deploy.sh
```

##### 3. 停止服务

```bash
./deploy/docker/stop.sh
```

## 多架构支持

脚本自动支持多架构，无需手动配置！

### 自动检测（推荐）

脚本会自动检测你的系统架构并使用对应镜像：

```bash
# Apple Silicon (M1/M2/M3) - 自动使用 linux/arm64
./deploy/docker/deploy.sh

# Intel/AMD - 自动使用 linux/amd64
./deploy/docker/deploy.sh
```

### 手动指定架构

如需手动指定：

```bash
# 构建 arm64 镜像（Apple Silicon）
PLATFORM=linux/arm64 ./deploy/docker/build-image.sh

# 构建 amd64 镜像（Intel/AMD）
PLATFORM=linux/amd64 ./deploy/docker/build-image.sh

# 同时构建两个架构（多架构镜像）
MULTI_ARCH=true ./deploy/docker/build-image.sh
```

### 使用指定架构部署

```bash
# 使用 arm64 镜像部署
PLATFORM=linux/arm64 ./deploy/docker/deploy.sh

# 使用 amd64 镜像部署
PLATFORM=linux/amd64 ./deploy/docker/deploy.sh
```

## 文件说明

| 文件 | 说明 |
|------|------|
| `build-image.sh` | 一键构建 Docker 镜像（支持多架构） |
| `deploy.sh` | 一键部署（自动构建镜像+启动容器，自动检测架构） |
| `stop.sh` | 停止并清理容器 |
| `image_upload.sh` | 将本地镜像上传到火山引擎镜像仓库 |
| `image_upload.example.yaml` | 镜像上传配置文件示例 |
| `README.md` | 本文档 |

## 使用 Docker Compose

项目根目录也提供了 `docker-compose.yml`：

```bash
# 启动服务
docker-compose up -d

# 查看日志
docker-compose logs -f

# 停止服务
docker-compose down
```

## 环境变量配置

### build-image.sh

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `IMAGE_NAME` | `vikingbot` | 镜像名称 |
| `IMAGE_TAG` | `latest` | 镜像标签 |
| `DOCKERFILE` | `deploy/Dockerfile` | Dockerfile 路径 |
| `NO_CACHE` | `false` | 是否不使用缓存 |
| `PLATFORM` | 自动检测 | 目标平台 (linux/amd64, linux/arm64) |
| `MULTI_ARCH` | `false` | 是否构建多架构镜像 |

**示例：**

```bash
# 构建带版本标签的镜像
IMAGE_TAG=v1.0.0 ./deploy/docker/build-image.sh

# 不使用缓存重新构建
NO_CACHE=true ./deploy/docker/build-image.sh

# 构建 arm64 镜像
PLATFORM=linux/arm64 ./deploy/docker/build-image.sh

# 同时构建 amd64+arm64 多架构镜像
MULTI_ARCH=true ./deploy/docker/build-image.sh
```

### deploy.sh

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `CONTAINER_NAME` | `vikingbot` | 容器名称 |
| `IMAGE_NAME` | `vikingbot` | 镜像名称 |
| `IMAGE_TAG` | `latest` | 镜像标签 |
| `HOST_PORT` | `18791` | 主机端口 |
| `CONTAINER_PORT` | `18791` | 容器端口 |
| `COMMAND` | `gateway` | 启动命令 |
| `AUTO_BUILD` | `true` | 镜像不存在时自动构建 |
| `PLATFORM` | 自动检测 | 使用的镜像平台 |

**示例：**

```bash
# 使用自定义端口
HOST_PORT=8080 ./deploy/docker/deploy.sh

# 不自动构建镜像
AUTO_BUILD=false ./deploy/docker/deploy.sh

# 强制使用 arm64 镜像
PLATFORM=linux/arm64 ./deploy/docker/deploy.sh
```

### stop.sh

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `CONTAINER_NAME` | `vikingbot` | 容器名称 |
| `REMOVE_IMAGE` | `false` | 是否同时删除镜像 |
| `REMOVE_VOLUME` | `false` | 是否同时删除数据卷 |

**示例：**

```bash
# 完全清理（容器+镜像+数据卷）
REMOVE_IMAGE=true REMOVE_VOLUME=true ./deploy/docker/stop.sh
```

## 配置文件

首次部署时，脚本会自动创建配置文件：`~/.vikingbot/config.json`

编辑该文件填入你的 API keys：

```json
{
  "providers": {
    "openrouter": {
      "apiKey": "sk-or-xxx"
    }
  },
  "agents": {
    "defaults": {
      "model": "openrouter/anthropic/claude-3.5-sonnet"
    }
  },
  "gateway": {
    "host": "0.0.0.0",
    "port": 18791
  }
}
```

**重要：** Console Web UI 端口是 **18791**，不是 18790！

## 访问控制台

部署成功后，访问：http://localhost:18791

## 常用命令

```bash
# 查看日志
docker logs -f vikingbot

# 进入容器
docker exec -it vikingbot bash

# 运行 vikingbot 命令
docker exec vikingbot vikingbot status

# 重启容器
docker restart vikingbot
```

## 架构兼容性说明

| 系统 | 架构 | 自动检测 | 手动指定 |
|------|------|----------|----------|
| Apple Silicon (M1/M2/M3) | arm64 | ✓ | `PLATFORM=linux/arm64` |
| Intel Mac | amd64 | ✓ | `PLATFORM=linux/amd64` |
| Linux PC/Server | amd64 | ✓ | `PLATFORM=linux/amd64` |
| Linux ARM Server | arm64 | ✓ | `PLATFORM=linux/arm64` |
| Windows (WSL2) | amd64 | ✓ | `PLATFORM=linux/amd64` |

## 与 VKE 部署共用 Dockerfile

注意：本地 Docker 部署和 VKE 部署**共用同一个 Dockerfile**（`deploy/Dockerfile`），确保了环境一致性。

- VKE 部署：使用 `deploy/vke/vke_deploy.py`
- 本地部署：使用 `deploy/docker/deploy.sh`
- 两者都使用：`deploy/Dockerfile`

Dockerfile 已移除平台硬编码，支持灵活的多架构构建！

## 跨平台镜像构建（推送到仓库）

如果你需要构建可以在 Windows/Mac/Linux 多平台运行的镜像，可以使用 `build-multiarch.sh`：

### 前置准备

1. 准备一个 Docker 镜像仓库（如 Docker Hub, ACR, Harbor 等）
2. 登录到镜像仓库

### 构建并推送跨平台镜像

```bash
# 构建 linux/amd64 + linux/arm64 双架构镜像并推送
REGISTRY=your-registry.com PUSH=true ./deploy/docker/build-multiarch.sh
```

### 环境变量配置

| 变量 | 说明 | 示例 |
|------|------|------|
| `REGISTRY` | 镜像仓库地址 | `registry.example.com` |
| `IMAGE_NAME` | 镜像名称 | `vikingbot` |
| `IMAGE_TAG` | 镜像标签 | `latest` |
| `PUSH` | 是否推送 | `true` / `false` |
| `PLATFORMS` | 目标架构 | `linux/amd64,linux/arm64` |

### 使用跨平台镜像

推送成功后，在任何平台都可以直接使用：

```bash
# 在 Apple Silicon Mac 上
PLATFORM=linux/arm64 ./deploy/docker/deploy.sh

# 在 Intel/AMD Linux 上
PLATFORM=linux/amd64 ./deploy/docker/deploy.sh

# 或让脚本自动检测
./deploy/docker/deploy.sh
```

### 验证镜像架构

```bash
# 查看镜像支持的架构
docker manifest inspect your-registry.com/vikingbot:latest
```
