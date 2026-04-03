#!/bin/bash
# OpenClaw 测试环境设置脚本

set -e

echo "====================================="
echo "OpenClaw - OpenViking 测试环境设置"
echo "====================================="
echo ""

# 检查虚拟环境是否已存在
if [ ! -d "venv" ]; then
    echo "创建虚拟环境..."
    python -m venv venv
fi

echo "激活虚拟环境..."
source venv/bin/activate

echo ""
echo "升级 pip..."
pip install --upgrade pip

echo ""
echo "安装项目依赖..."
pip install -r requirements.txt

echo ""
echo "安装测试报告生成依赖..."
pip install pytest-html

echo ""
echo "====================================="
echo "环境设置完成！"
echo "====================================="
echo ""
echo "使用以下命令激活虚拟环境："
echo "  source venv/bin/activate"
echo ""
echo "或运行测试："
echo "  ./run.sh"
echo ""
