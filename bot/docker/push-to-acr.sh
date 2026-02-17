#!/bin/bash

# 火山引擎 ACR 一键推送脚本
# 使用前请在 ~/.acr_env 中配置：
# ACR_REGISTRY=vikingbot-cn-beijing.cr.volces.com
# ACR_NAMESPACE=vikingbot
# ACR_REPOSITORY=vikingbot
# ACR_USERNAME=你的用户名@账户ID
# ACR_PASSWORD=你的密码

set -e

# 获取脚本所在目录，然后找到项目根目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}========================================${NC}"
echo -e "${YELLOW}  火山引擎 ACR 一键推送脚本${NC}"
echo -e "${YELLOW}========================================${NC}"

# 从 ~/.acr_env 加载环境变量
ENV_FILE="$HOME/.acr_env"
if [ -f "$ENV_FILE" ]; then
    echo -e "${GREEN}[0/5]${NC} 加载环境变量从 $ENV_FILE..."
    export $(grep -v '^#' "$ENV_FILE" | xargs)
else
    echo -e "${YELLOW}警告: $ENV_FILE 文件不存在${NC}"
fi

# 检查环境变量
check_env() {
    local var_name=$1
    local var_value=${!var_name}
    if [ -z "$var_value" ]; then
        echo -e "${RED}错误: 环境变量 $var_name 未设置${NC}"
        echo -e "${YELLOW}请在 $ENV_FILE 中配置或通过 export 设置${NC}"
        exit 1
    fi
}

check_env "ACR_REGISTRY"
check_env "ACR_NAMESPACE"
check_env "ACR_REPOSITORY"
check_env "ACR_USERNAME"
check_env "ACR_PASSWORD"

# 配置变量
IMAGE_TAG=${IMAGE_TAG:-latest}
LOCAL_IMAGE_NAME=${LOCAL_IMAGE_NAME:-vikingbot}
# 火山引擎镜像格式：<registry>/<namespace>/<repository>:<tag>
FULL_IMAGE_NAME="$ACR_REGISTRY/$ACR_NAMESPACE/$ACR_REPOSITORY:$IMAGE_TAG"

echo -e "${GREEN}[1/5]${NC} 配置信息:"
echo "  Project Root: $PROJECT_ROOT"
echo "  Registry: $ACR_REGISTRY"
echo "  Namespace: $ACR_NAMESPACE"
echo "  Repository: $ACR_REPOSITORY"
echo "  Tag: $IMAGE_TAG"
echo "  Full Image: $FULL_IMAGE_NAME"
echo ""

# 1. 登录 ACR
echo -e "${GREEN}[2/5]${NC} 登录 ACR..."
echo "$ACR_PASSWORD" | docker login --username "$ACR_USERNAME" --password-stdin "$ACR_REGISTRY"

# 2. 构建镜像
echo -e "${GREEN}[3/5]${NC} 构建 Docker 镜像..."
cd "$PROJECT_ROOT"
docker build -f docker/Dockerfile -t "$LOCAL_IMAGE_NAME:$IMAGE_TAG" .

# 3. 打标签
echo -e "${GREEN}[4/5]${NC} 打标签..."
docker tag "$LOCAL_IMAGE_NAME:$IMAGE_TAG" "$FULL_IMAGE_NAME"

# 4. 推送镜像
echo -e "${GREEN}[5/5]${NC} 推送镜像..."
docker push "$FULL_IMAGE_NAME"

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  推送成功!${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "镜像地址: ${YELLOW}$FULL_IMAGE_NAME${NC}"
echo ""
echo "下一步可以:"
echo "  1. 在火山引擎控制台查看镜像"
echo "  2. 使用该镜像创建容器实例/VKE 部署"
echo ""
