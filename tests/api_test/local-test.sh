#!/bin/bash
# OpenViking API 测试 - 本地测试脚本
# 模拟 GitHub Actions 流水线的执行流程

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  OpenViking API 测试 - 本地执行${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# 1. 检查 Python 版本
echo -e "${YELLOW}[1/7] 检查 Python 版本...${NC}"
PYTHON_VERSION=$(python3 --version | awk '{print $2}')
echo "Python 版本: $PYTHON_VERSION"
python3 -c "import sys; assert sys.version_info >= (3, 10), 'Python 3.10+ required'"
echo -e "${GREEN}✓ Python 版本检查通过${NC}"
echo ""

# 2. 安装 OpenViking
echo -e "${YELLOW}[2/7] 安装 OpenViking...${NC}"
cd "$(dirname "$0")/../.."
pip install -e .
echo -e "${GREEN}✓ OpenViking 安装成功${NC}"
echo ""

# 3. 安装测试依赖
echo -e "${YELLOW}[3/7] 安装测试依赖...${NC}"
cd tests/api_test
pip install -r requirements.txt
echo -e "${GREEN}✓ 测试依赖安装成功${NC}"
echo ""

# 4. 创建 OpenViking 配置文件
echo -e "${YELLOW}[4/8] 创建 OpenViking 配置文件...${NC}"
mkdir -p ~/.openviking
cat > ~/.openviking/ov.conf << EOF
{
  "server": {
    "root_api_key": "test-root-api-key"
  },
  "vlm": {
    "provider": "volcengine",
    "api_key": "dummy-vlm-api-key",
    "model": "doubao-seed-2-0-mini-260215",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3",
    "temperature": 0.1,
    "max_retries": 3
  },
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "api_key": "dummy-embedding-api-key",
      "model": "doubao-embedding-vision-251215",
      "api_base": "https://ark.cn-beijing.volces.com/api/v3",
      "dimension": 1024,
      "input": "multimodal"
    }
  }
}
EOF
echo -e "${GREEN}✓ 配置文件创建成功${NC}"
echo ""

# 5. 找到可用端口
echo -e "${YELLOW}[5/8] 查找可用端口...${NC}"
find_available_port() {
    local port=1933
    while true; do
        if ! lsof -Pi :$port -sTCP:LISTEN -t >/dev/null 2>&1; then
            echo $port
            return
        fi
        port=$((port + 1))
    done
}
SERVER_PORT=$(find_available_port)
echo "使用端口: $SERVER_PORT"
echo -e "${GREEN}✓ 找到可用端口${NC}"
echo ""

# 6. 启动 OpenViking Server
echo -e "${YELLOW}[6/8] 启动 OpenViking Server...${NC}"
export ROOT_API_KEY=test-root-api-key
export SERVER_PORT=$SERVER_PORT
nohup python -m openviking.server.bootstrap > openviking-server.log 2>&1 &
SERVER_PID=$!
echo $SERVER_PID > openviking-server.pid
echo "Server PID: $SERVER_PID"

# 等待服务启动
echo "等待服务启动..."
for i in {1..30}; do
    if curl -s http://127.0.0.1:$SERVER_PORT/health | grep -q '"healthy":true'; then
        echo -e "${GREEN}✓ 服务已就绪！${NC}"
        break
    fi
    echo "等待... ($i/30)"
    sleep 2
done

# 检查服务是否启动成功
if ! curl -s http://127.0.0.1:$SERVER_PORT/health | grep -q '"healthy":true'; then
    echo -e "${RED}✗ 服务启动失败！${NC}"
    echo "服务日志："
    cat openviking-server.log
    exit 1
fi
echo ""

# 7. 运行 API 测试
echo -e "${YELLOW}[7/8] 运行 API 测试...${NC}"
export OPENVIKING_API_KEY=test-root-api-key
export SERVER_URL=http://127.0.0.1:$SERVER_PORT
python -m pytest . -v --html=api-test-report.html --self-contained-html --ignore=retrieval/ --ignore=resources/test_pack.py --ignore=resources/test_wait_processed.py
TEST_RESULT=$?
echo ""

# 8. 停止服务
echo -e "${YELLOW}[8/8] 停止 OpenViking Server...${NC}"
if [ -f openviking-server.pid ]; then
    kill $SERVER_PID 2>/dev/null || true
    pkill -f "openviking.server.bootstrap" 2>/dev/null || true
    rm -f openviking-server.pid
fi
echo -e "${GREEN}✓ 服务已停止${NC}"
echo ""

# 总结
echo -e "${GREEN}========================================${NC}"
if [ $TEST_RESULT -eq 0 ]; then
    echo -e "${GREEN}  ✓ 所有测试通过！${NC}"
else
    echo -e "${RED}  ✗ 部分测试失败${NC}"
fi
echo -e "${GREEN}========================================${NC}"
echo ""
echo "测试报告: tests/api_test/api-test-report.html"
echo "服务日志: tests/api_test/openviking-server.log"
echo ""

exit $TEST_RESULT
