#!/bin/bash
# 运行测试脚本

set -e

# 检查是否在虚拟环境中
if [[ "$VIRTUAL_ENV" == "" ]]; then
    echo "激活虚拟环境..."
    source venv/bin/activate
fi

echo "====================================="
echo "运行 OpenClaw 自动化测试"
echo "====================================="
echo ""

# 检查参数
if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
    echo "用法: $0 [选项]"
    echo ""
    echo "选项:"
    echo "  -h, --help          显示帮助信息"
    echo "  -a, --all           运行全部测试（默认）"
    echo "  -p, --p0            仅运行 P0 级测试"
    echo "  -c, --crud          仅运行 CRUD 操作测试"
    echo "  -x, --complex       仅运行复杂场景测试"
    echo "  -v, --verbose       详细输出模式"
    echo "  -r, --report        生成 HTML 测试报告"
    echo ""
    exit 0
fi

# 默认选项
VERBOSE=""
REPORT=""
TEST_TYPE=""

# 解析参数
while [[ $# -gt 0 ]]; do
    case $1 in
        -v|--verbose)
            VERBOSE="-v"
            shift
            ;;
        -r|--report)
            REPORT="--html=reports/test_report.html --self-contained-html"
            shift
            ;;
        -a|--all)
            TEST_TYPE=""
            shift
            ;;
        -p|--p0)
            TEST_TYPE="tests/p0/"
            shift
            ;;
        -c|--crud)
            TEST_TYPE="tests/crud/"
            shift
            ;;
        -x|--complex)
            TEST_TYPE="tests/complex/"
            shift
            ;;
        *)
            echo "未知选项: $1"
            echo "使用 -h 查看帮助"
            exit 1
            ;;
    esac
done

# 确保报告目录存在
mkdir -p reports logs

# 运行测试
if [ -n "$TEST_TYPE" ]; then
    echo "运行 $TEST_TYPE 目录下的测试..."
    pytest $TEST_TYPE $VERBOSE $REPORT
else
    echo "运行 pytest 测试文件..."
    pytest test_pytest.py $VERBOSE $REPORT
fi

echo ""
echo "====================================="
echo "测试完成"
echo "====================================="
if [ -n "$REPORT" ]; then
    echo "测试报告已生成: reports/test_report.html"
    echo "使用 'open reports/test_report.html' 查看报告"
fi
echo ""
