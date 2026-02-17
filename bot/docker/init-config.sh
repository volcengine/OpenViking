#!/bin/bash

# Vikingbot 配置初始化脚本

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}========================================${NC}"
echo -e "${YELLOW}  Vikingbot 配置初始化${NC}"
echo -e "${YELLOW}========================================${NC}"
echo ""

# 检查本地配置目录
VIKINGBOT_DIR="$HOME/.vikingbot"
CONFIG_FILE="$VIKINGBOT_DIR/config.json"

if [ -f "$CONFIG_FILE" ]; then
    echo -e "${YELLOW}配置文件已存在: $CONFIG_FILE${NC}"
    echo -e "${YELLOW}跳过初始化。${NC}"
    echo ""
    echo "现有配置:"
    cat "$CONFIG_FILE" | head -20
    exit 0
fi

# 创建目录
echo -e "${GREEN}[1/3]${NC} 创建配置目录..."
mkdir -p "$VIKINGBOT_DIR"

# 复制配置模板
echo -e "${GREEN}[2/3]${NC} 复制配置模板..."
cp "$SCRIPT_DIR/config.example.json" "$CONFIG_FILE"

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  初始化完成!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "配置文件位置: ${YELLOW}$CONFIG_FILE${NC}"
echo ""
echo "下一步:"
echo "  1. 编辑配置文件: vim $CONFIG_FILE"
echo "  2. 填入你的 API keys"
echo "  3. 上传到容器云盘"
echo ""
