#!/bin/bash
# Vikingbot 本地一键部署脚本
# ~/.vikingbot 会挂载到容器的 /root/.vikingbot（bridge 首次启动时自动初始化）
# 用法: ./deploy/docker/deploy.sh
# 变量: CONTAINER_NAME, IMAGE_NAME, IMAGE_TAG, HOST_PORT, COMMAND, AUTO_BUILD, PLATFORM

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

RED=$'\033[0;31m'
GREEN=$'\033[0;32m'
YELLOW=$'\033[1;33m'
BLUE=$'\033[0;34m'
NC=$'\033[0m'

CONTAINER_NAME=${CONTAINER_NAME:-vikingbot}
IMAGE_NAME=${IMAGE_NAME:-vikingbot}
IMAGE_TAG=${IMAGE_TAG:-latest}
HOST_PORT=${HOST_PORT:-18791}
COMMAND=${COMMAND:-gateway}
AUTO_BUILD=${AUTO_BUILD:-true}
# openviking 只有 linux/amd64 wheel，固定使用 amd64（Apple Silicon 由 Docker Desktop Rosetta 模拟）
PLATFORM=${PLATFORM:-linux/amd64}
VIKINGBOT_DIR="$HOME/.vikingbot"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Vikingbot 本地部署${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# 1. 检查 Docker
echo -e "${GREEN}[1/6]${NC} 检查 Docker..."
if ! command -v docker &> /dev/null; then
    echo -e "${RED}错误: Docker 未安装${NC}"
    echo "请先安装 Docker: https://www.docker.com/get-started"
    exit 1
fi
echo -e "  ${GREEN}✓${NC} Docker 已安装，平台: ${PLATFORM}"

# 2. 检查/构建镜像
echo -e "${GREEN}[2/6]${NC} 检查镜像 ${IMAGE_NAME}:${IMAGE_TAG}..."
if ! docker images --format "{{.Repository}}:{{.Tag}}" | grep -q "^${IMAGE_NAME}:${IMAGE_TAG}$"; then
    if [ "$AUTO_BUILD" = "true" ]; then
        echo -e "  ${YELLOW}镜像不存在，开始自动构建...${NC}"
        PLATFORM="$PLATFORM" IMAGE_NAME="$IMAGE_NAME" IMAGE_TAG="$IMAGE_TAG" \
            "$SCRIPT_DIR/build-image.sh"
    else
        echo -e "${RED}错误: 镜像不存在。请先运行 build-image.sh${NC}"
        exit 1
    fi
else
    echo -e "  ${GREEN}✓${NC} 镜像已存在"
fi

# 3. 初始化 ~/.vikingbot 目录
echo -e "${GREEN}[3/6]${NC} 初始化 ${VIKINGBOT_DIR}..."
mkdir -p "$VIKINGBOT_DIR/workspace" "$VIKINGBOT_DIR/sessions" "$VIKINGBOT_DIR/sandboxes" "$VIKINGBOT_DIR/bridge"
# 创建 OpenViking 配置文件占位符
touch "$VIKINGBOT_DIR/ov.conf"
echo -e "  ${GREEN}✓${NC} 目录已就绪"

# 4. 检查配置文件
echo -e "${GREEN}[4/6]${NC} 检查配置文件..."
CONFIG_FILE="$VIKINGBOT_DIR/config.json"
if [ ! -f "$CONFIG_FILE" ]; then
    echo -e "  ${YELLOW}配置文件不存在，创建默认配置...${NC}"
    cat > "$CONFIG_FILE" << 'EOF'
{
  "providers": {
    "openrouter": {
      "apiKey": ""
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
EOF
    echo ""
    echo -e "${YELLOW}  ⚠  请先编辑配置文件填入 API Keys，再重新运行此脚本:${NC}"
    echo -e "     ${YELLOW}$CONFIG_FILE${NC}"
    echo ""
    exit 1
else
    echo -e "  ${GREEN}✓${NC} 配置文件已存在"
fi

# 5. 清理旧容器
echo -e "${GREEN}[5/6]${NC} 清理旧容器..."
if docker ps -aq -f "name=^/${CONTAINER_NAME}$" | grep -q .; then
    docker rm -f "${CONTAINER_NAME}" > /dev/null
    echo -e "  ${GREEN}✓${NC} 旧容器已删除"
else
    echo -e "  ${GREEN}✓${NC} 无旧容器"
fi

# 6. 启动容器
echo -e "${GREEN}[6/6]${NC} 启动容器..."
echo "  容器名: ${CONTAINER_NAME}"
echo "  镜像:   ${IMAGE_NAME}:${IMAGE_TAG}"
echo "  命令:   vikingbot ${COMMAND}"
echo "  端口:   ${HOST_PORT} → 18791"
echo "  挂载:   ${VIKINGBOT_DIR} → /root/.vikingbot"
echo ""

docker run -d \
    --name "${CONTAINER_NAME}" \
    --restart unless-stopped \
    --platform "${PLATFORM}" \
    -v "${VIKINGBOT_DIR}:/root/.vikingbot" \
    -p "${HOST_PORT}:18791" \
    -e OPENVIKING_CONFIG_FILE=/root/.vikingbot/ov.conf \
    "${IMAGE_NAME}:${IMAGE_TAG}" \
    "${COMMAND}"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  部署成功!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "  控制台: ${YELLOW}http://localhost:${HOST_PORT}${NC}"
echo ""
echo "常用命令:"
echo "  查看日志:  ${YELLOW}docker logs -f ${CONTAINER_NAME}${NC}"
echo "  进入容器:  ${YELLOW}docker exec -it ${CONTAINER_NAME} bash${NC}"
echo "  重启:      ${YELLOW}docker restart ${CONTAINER_NAME}${NC}"
echo "  停止:      ${YELLOW}./deploy/docker/stop.sh${NC}"
echo ""
echo "正在输出日志 (Ctrl+C 退出)..."
echo "----------------------------------------"
docker logs --tail 20 -f "${CONTAINER_NAME}"
