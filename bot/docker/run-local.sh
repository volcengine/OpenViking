#!/bin/bash

# Vikingbot 本地一键运行脚本
# 功能：
# 1. 检查并构建镜像（如需要）
# 2. 初始化配置（如需要）
# 3. 停止旧容器（如存在）
# 4. 启动新容器
# 5. 显示状态和日志

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# 默认配置
CONTAINER_NAME=${CONTAINER_NAME:-vikingbot}
IMAGE_NAME=${IMAGE_NAME:-vikingbot}
IMAGE_TAG=${IMAGE_TAG:-latest}
VIKINGBOT_DIR="$HOME/.vikingbot"
CONFIG_FILE="$VIKINGBOT_DIR/config.json"
HOST_PORT=${HOST_PORT:-18790}
CONTAINER_PORT=${CONTAINER_PORT:-18790}
COMMAND=${COMMAND:-gateway}

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Vikingbot 本地一键启动${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# 1. 检查 Docker 是否安装
echo -e "${GREEN}[1/7]${NC} 检查 Docker..."
if ! command -v docker &> /dev/null; then
    echo -e "${RED}错误: Docker 未安装${NC}"
    echo "请先安装 Docker: https://www.docker.com/get-started"
    exit 1
fi
echo -e "  ${GREEN}✓${NC} Docker 已安装"

# 2. 检查镜像是否存在
echo -e "${GREEN}[2/7]${NC} 检查 Docker 镜像..."
if ! docker images --format "{{.Repository}}:{{.Tag}}" | grep -q "^${IMAGE_NAME}:${IMAGE_TAG}$"; then
    echo -e "  ${YELLOW}镜像不存在，开始构建...${NC}"
    cd "$PROJECT_ROOT"
    docker build -f docker/Dockerfile -t "${IMAGE_NAME}:${IMAGE_TAG}" .
else
    echo -e "  ${GREEN}✓${NC} 镜像已存在: ${IMAGE_NAME}:${IMAGE_TAG}"
fi

# 3. 初始化配置
echo -e "${GREEN}[3/7]${NC} 检查配置文件..."
if [ ! -f "$CONFIG_FILE" ]; then
    echo -e "  ${YELLOW}配置文件不存在，初始化...${NC}"
    "$SCRIPT_DIR/init-config.sh"
    echo ""
    echo -e "${YELLOW}⚠️  请先编辑配置文件并填入 API keys:${NC}"
    echo -e "   ${YELLOW}$CONFIG_FILE${NC}"
    echo ""
    echo -e "编辑完成后重新运行此脚本。"
    exit 1
else
    echo -e "  ${GREEN}✓${NC} 配置文件已存在"
fi

# 4. 停止并删除旧容器（如存在）
echo -e "${GREEN}[4/7]${NC} 清理旧容器..."
if [ "$(docker ps -aq -f name=^/${CONTAINER_NAME}$)" ]; then
    if [ "$(docker ps -q -f name=^/${CONTAINER_NAME}$)" ]; then
        echo -e "  停止运行中的容器..."
        docker stop "${CONTAINER_NAME}" > /dev/null
    fi
    echo -e "  删除旧容器..."
    docker rm "${CONTAINER_NAME}" > /dev/null
    echo -e "  ${GREEN}✓${NC} 旧容器已清理"
else
    echo -e "  ${GREEN}✓${NC} 无旧容器需要清理"
fi

# 5. 创建配置目录（确保存在）
echo -e "${GREEN}[5/7]${NC} 准备挂载目录..."
mkdir -p "${VIKINGBOT_DIR}/workspace"
mkdir -p "${VIKINGBOT_DIR}/sandboxes"
mkdir -p "${VIKINGBOT_DIR}/bridge"
echo -e "  ${GREEN}✓${NC} 目录已准备"

# 6. 启动新容器
echo -e "${GREEN}[6/7]${NC} 启动容器..."
echo "  容器名称: ${CONTAINER_NAME}"
echo "  镜像: ${IMAGE_NAME}:${IMAGE_TAG}"
echo "  命令: ${COMMAND}"
echo "  端口映射: ${HOST_PORT}:${CONTAINER_PORT}"
echo "  挂载: ${VIKINGBOT_DIR}:/root/.vikingbot"

docker run -d \
  --name "${CONTAINER_NAME}" \
  --restart unless-stopped \
  -v "${VIKINGBOT_DIR}:/root/.vikingbot" \
  -p "${HOST_PORT}:${CONTAINER_PORT}" \
  "${IMAGE_NAME}:${IMAGE_TAG}" \
  "${COMMAND}"

echo -e "  ${GREEN}✓${NC} 容器已启动"

# 7. 显示状态和日志
echo -e "${GREEN}[7/7]${NC} 检查容器状态..."
sleep 2

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  启动成功!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "容器信息:"
echo "  名称: ${CONTAINER_NAME}"
echo "  状态: $(docker inspect -f '{{.State.Status}}' "${CONTAINER_NAME}")"
echo "  端口: ${HOST_PORT}"
echo ""
echo "常用命令:"
echo "  查看日志:    ${YELLOW}docker logs -f ${CONTAINER_NAME}${NC}"
echo "  停止容器:    ${YELLOW}docker stop ${CONTAINER_NAME}${NC}"
echo "  启动容器:    ${YELLOW}docker start ${CONTAINER_NAME}${NC}"
echo "  重启容器:    ${YELLOW}docker restart ${CONTAINER_NAME}${NC}"
echo "  删除容器:    ${YELLOW}docker rm -f ${CONTAINER_NAME}${NC}"
echo "  进入容器:    ${YELLOW}docker exec -it ${CONTAINER_NAME} bash${NC}"
echo "  运行命令:    ${YELLOW}docker exec ${CONTAINER_NAME} vikingbot status${NC}"
echo ""
echo "正在显示日志 (Ctrl+C 退出)..."
echo "----------------------------------------"
docker logs --tail 20 -f "${CONTAINER_NAME}"
