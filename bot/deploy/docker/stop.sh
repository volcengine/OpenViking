#!/bin/bash
# Vikingbot 停止脚本
# 用法: ./deploy/docker/stop.sh
# 变量: CONTAINER_NAME, REMOVE_IMAGE, IMAGE_NAME, IMAGE_TAG

set -e

RED=$'\033[0;31m'
GREEN=$'\033[0;32m'
YELLOW=$'\033[1;33m'
NC=$'\033[0m'

CONTAINER_NAME=${CONTAINER_NAME:-vikingbot}
REMOVE_IMAGE=${REMOVE_IMAGE:-false}
IMAGE_NAME=${IMAGE_NAME:-vikingbot}
IMAGE_TAG=${IMAGE_TAG:-latest}

echo -e "${YELLOW}停止 Vikingbot...${NC}"

if docker ps -aq -f "name=^/${CONTAINER_NAME}$" | grep -q .; then
    docker rm -f "${CONTAINER_NAME}" > /dev/null
    echo -e "${GREEN}✓ 容器 ${CONTAINER_NAME} 已停止并删除${NC}"
else
    echo -e "  容器 ${CONTAINER_NAME} 不存在，跳过"
fi

if [ "$REMOVE_IMAGE" = "true" ]; then
    if docker images --format "{{.Repository}}:{{.Tag}}" | grep -q "^${IMAGE_NAME}:${IMAGE_TAG}$"; then
        docker rmi "${IMAGE_NAME}:${IMAGE_TAG}"
        echo -e "${GREEN}✓ 镜像 ${IMAGE_NAME}:${IMAGE_TAG} 已删除${NC}"
    fi
fi

echo -e "${GREEN}完成${NC}"
