#!/bin/bash

# Vikingbot 多架构镜像构建脚本
# 功能：
# 1. 构建跨平台 Docker 镜像（linux/amd64 + linux/arm64）
# 2. 支持推送到远程镜像仓库
# 3. 支持仅本地加载（不推送）

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# 默认配置
IMAGE_NAME=${IMAGE_NAME:-vikingbot}
IMAGE_TAG=${IMAGE_TAG:-latest}
DOCKERFILE=${DOCKERFILE:-deploy/docker/Dockerfile}
NO_CACHE=${NO_CACHE:-false}
# 平台列表
PLATFORMS=${PLATFORMS:-linux/amd64,linux/arm64}
# 是否推送（默认仅本地加载）
PUSH=${PUSH:-false}
# 远程仓库地址（如需要推送）
REGISTRY=${REGISTRY:-}

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Vikingbot 多架构镜像构建${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# 1. 检查 Docker 是否安装
echo -e "${GREEN}[1/6]${NC} 检查 Docker..."
if ! command -v docker &> /dev/null; then
    echo -e "${RED}错误: Docker 未安装${NC}"
    echo "请先安装 Docker: https://www.docker.com/get-started"
    exit 1
fi
echo -e "  ${GREEN}✓${NC} Docker 已安装"

# 2. 检查 Docker Buildx
echo -e "${GREEN}[2/6]${NC} 检查 Docker Buildx..."
if ! docker buildx version &> /dev/null; then
    echo -e "${RED}错误: Docker Buildx 不可用${NC}"
    echo "请确保使用 Docker Desktop 或启用了 Buildx"
    exit 1
fi
echo -e "  ${GREEN}✓${NC} Docker Buildx 已就绪"

# 3. 检查 Dockerfile 是否存在
echo -e "${GREEN}[3/6]${NC} 检查 Dockerfile..."
if [ ! -f "$PROJECT_ROOT/$DOCKERFILE" ]; then
    echo -e "${RED}错误: Dockerfile 不存在${NC}"
    echo "路径: $PROJECT_ROOT/$DOCKERFILE"
    exit 1
fi
echo -e "  ${GREEN}✓${NC} Dockerfile 存在"

# 4. 显示构建配置
echo -e "${GREEN}[4/6]${NC} 构建配置:"
echo "  项目根目录: $PROJECT_ROOT"
echo "  Dockerfile: $DOCKERFILE"

if [ -n "$REGISTRY" ]; then
    FULL_IMAGE_NAME="${REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}"
else
    FULL_IMAGE_NAME="${IMAGE_NAME}:${IMAGE_TAG}"
fi

echo "  镜像名称: ${FULL_IMAGE_NAME}"
echo "  目标平台: ${PLATFORMS}"
echo "  不使用缓存: ${NO_CACHE}"
echo "  推送至仓库: ${PUSH}"

if [ "$PUSH" = "true" ] && [ -z "$REGISTRY" ]; then
    echo ""
    echo -e "${YELLOW}⚠️  警告: PUSH=true 但未指定 REGISTRY${NC}"
    echo -e "   镜像将仅本地加载，不会推送${NC}"
    PUSH=false
fi

# 5. 创建/使用 builder 实例
echo -e "${GREEN}[5/6]${NC} 准备 Buildx builder..."
BUILDER_NAME="vikingbot-builder"

# 检查 builder 是否存在
if ! docker buildx inspect "${BUILDER_NAME}" &> /dev/null; then
    echo "  创建新的 builder 实例..."
    docker buildx create --name "${BUILDER_NAME}" --use
else
    echo "  使用现有的 builder 实例..."
    docker buildx use "${BUILDER_NAME}"
fi
echo -e "  ${GREEN}✓${NC} Builder 已就绪"

# 6. 构建多架构镜像
echo -e "${GREEN}[6/6]${NC} 开始构建多架构镜像..."
echo ""

cd "$PROJECT_ROOT"

BUILD_ARGS=""
if [ "$NO_CACHE" = "true" ]; then
    BUILD_ARGS="--no-cache"
fi

if [ "$PUSH" = "true" ]; then
    # 推送模式：构建并推送
    echo "模式: 构建并推送至仓库"
    echo "镜像: ${FULL_IMAGE_NAME}"
    echo ""

    docker buildx build $BUILD_ARGS \
        -f "$DOCKERFILE" \
        -t "${FULL_IMAGE_NAME}" \
        --platform "${PLATFORMS}" \
        --push \
        .
else
    # 本地模式：构建并加载到本地（注意：buildx load 仅支持单架构）
    echo "模式: 构建并加载至本地"
    echo ""
    echo -e "${YELLOW}⚠️  注意: buildx load 仅支持单架构${NC}"
    echo -e "   正在构建本地架构镜像...${NC}"
    echo ""

    # 检测本地架构
    if [[ "$(uname -m)" == "arm64" ]] || [[ "$(uname -m)" == "aarch64" ]]; then
        LOCAL_PLATFORM="linux/arm64"
    else
        LOCAL_PLATFORM="linux/amd64"
    fi

    echo "本地架构: ${LOCAL_PLATFORM}"
    echo ""

    docker buildx build $BUILD_ARGS \
        -f "$DOCKERFILE" \
        -t "${FULL_IMAGE_NAME}" \
        --platform "${LOCAL_PLATFORM}" \
        --load \
        .
fi

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  多架构镜像构建完成!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "镜像信息:"
echo "  名称: ${FULL_IMAGE_NAME}"
echo "  平台: ${PLATFORMS}"
echo ""
echo "常用命令:"
echo "  查看镜像:            ${YELLOW}docker images ${IMAGE_NAME}${NC}"
if [ "$PUSH" != "true" ]; then
    echo "  测试本地镜像:        ${YELLOW}docker run --rm ${FULL_IMAGE_NAME} status${NC}"
fi
echo ""
echo "跨平台使用示例："
echo "  Windows/Mac/Linux (Intel):  使用 linux/amd64 镜像"
echo "  Mac (Apple Silicon):        使用 linux/arm64 镜像"
echo "  Linux ARM 服务器:            使用 linux/arm64 镜像"
echo ""
echo "推送到远程仓库示例："
echo "  REGISTRY=my-registry.com PUSH=true ./deploy/docker/build-multiarch.sh"
echo ""
