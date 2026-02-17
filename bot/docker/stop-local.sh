#!/bin/bash

# Vikingbot 本地容器停止和清理脚本

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# 默认配置
CONTAINER_NAME=${CONTAINER_NAME:-vikingbot}
REMOVE_IMAGE=${REMOVE_IMAGE:-false}

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Vikingbot 本地容器清理${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# 检查容器是否存在
if [ ! "$(docker ps -aq -f name=^/${CONTAINER_NAME}$)" ]; then
    echo -e "${YELLOW}容器 ${CONTAINER_NAME} 不存在${NC}"
    exit 0
fi

# 显示容器状态
echo -e "容器状态:"
docker ps -a --filter name=^/${CONTAINER_NAME}$ --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
echo ""

# 确认操作
read -p "确定要停止并删除容器 ${CONTAINER_NAME} 吗? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${YELLOW}操作已取消${NC}"
    exit 0
fi

# 停止容器（如果正在运行）
echo -e "${GREEN}[1/2]${NC} 停止容器..."
if [ "$(docker ps -q -f name=^/${CONTAINER_NAME}$)" ]; then
    docker stop "${CONTAINER_NAME}" > /dev/null
    echo -e "  ${GREEN}✓${NC} 容器已停止"
else
    echo -e "  ${GREEN}✓${NC} 容器未运行"
fi

# 删除容器
echo -e "${GREEN}[2/2]${NC} 删除容器..."
docker rm "${CONTAINER_NAME}" > /dev/null
echo -e "  ${GREEN}✓${NC} 容器已删除"

# 可选：删除镜像
if [ "$REMOVE_IMAGE" = "true" ]; then
    echo ""
    read -p "是否同时删除镜像 vikingbot:latest? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo -e "${GREEN}删除镜像...${NC}"
        docker rmi vikingbot:latest 2>/dev/null || true
        echo -e "  ${GREEN}✓${NC} 镜像已删除"
    fi
fi

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  清理完成!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "提示:"
echo "  重新启动: ${YELLOW}./docker/run-local.sh${NC}"
echo ""
