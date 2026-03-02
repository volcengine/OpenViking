#!/usr/bin/env python3
"""
test_vikingbot - Vikingbot 测试框架命令行工具

这个工具提供：
1. 列出所有可用的测试及其测试的基础能力
2. 运行指定的测试
3. 显示详细的测试规范

使用方法：
    test_vikingbot list              - 列出所有测试
    test_vikingbot run [tests...]    - 运行测试
    test_vikingbot spec <test>       - 显示测试规范
"""

import sys
import argparse
from pathlib import Path
from dataclasses import dataclass
from typing import Optional


# 添加 bot 目录到路径
tester_path = Path(__file__).parent
# bot 目录在 tester 的上上级 (tests/tester -> tests -> bot)
bot_path = tester_path / "../.."
sys.path.insert(0, str(bot_path.resolve()))
sys.path.insert(0, str(tester_path.resolve()))


@dataclass
class TestInfo:
    """测试信息"""
    name: str
    file: str
    description: str
    purpose: str
    specs: list[str]


# 测试定义
TEST_DEFINITIONS = {
    "agent_single_turn": TestInfo(
        name="agent_single_turn",
        file="tests/test_agent_single_turn.py",
        description="Agent 单轮对话测试",
        purpose="验证 `vikingbot agent -m \"\"` 单聊功能是否正常工作",
        specs=[
            "vikingbot agent 命令可以执行",
            "可以发送消息给 agent",
            "agent 可以正常响应",
        ],
    ),
}


def list_tests() -> None:
    """列出所有可用的测试"""
    print("\n" + "=" * 80)
    print("Vikingbot 测试框架 - 可用测试".center(80))
    print("=" * 80)
    print()

    for test_id, test_info in TEST_DEFINITIONS.items():
        print(f"\033[1;34m● {test_id}\033[0m")
        print(f"  \033[1;37m{test_info.description}\033[0m")
        print(f"  文件: {test_info.file}")
        print()
        print(f"  \033[1;33m目的:\033[0m {test_info.purpose}")
        print()
        print(f"  \033[1;32m测试的基础能力:\033[0m")
        for spec in test_info.specs:
            print(f"    - {spec}")
        print()


def show_spec(test_id: str) -> None:
    """显示指定测试的详细规范"""
    if test_id not in TEST_DEFINITIONS:
        print(f"\033[1;31m错误:\033[0m 未知的测试 '{test_id}'")
        print()
        print(f"可用的测试: {', '.join(TEST_DEFINITIONS.keys())}")
        sys.exit(1)

    test_info = TEST_DEFINITIONS[test_id]

    print("\n" + "=" * 80)
    print(f"测试规范: {test_info.name}".center(80))
    print("=" * 80)
    print()
    print(f"\033[1;37m{test_info.description}\033[0m")
    print()
    print(f"\033[1;33m测试目的:\033[0m")
    print(f"  {test_info.purpose}")
    print()
    print(f"\033[1;32m测试规格:\033[0m")

    # 尝试读取 spec 文件
    spec_file = tester_path / f"specs/{test_id}.md"
    if spec_file.exists():
        print()
        print(spec_file.read_text())
    else:
        for i, spec in enumerate(test_info.specs, 1):
            print(f"  {i}. {spec}")


def run_tests(tests: Optional[list[str]] = None) -> int:
    """运行测试"""
    import pytest

    # 确定要运行的测试文件
    if not tests:
        # 运行所有测试
        test_files = [info.file for info in TEST_DEFINITIONS.values()]
    else:
        # 运行指定的测试
        test_files = []
        for test_id in tests:
            if test_id not in TEST_DEFINITIONS:
                print(f"\033[1;31m错误:\033[0m 未知的测试 '{test_id}'")
                print(f"可用的测试: {', '.join(TEST_DEFINITIONS.keys())}")
                return 1
            test_files.append(TEST_DEFINITIONS[test_id].file)

    print("\n" + "=" * 80)
    print("运行 Vikingbot 测试".center(80))
    print("=" * 80)
    print()
    print(f"测试文件: {', '.join(test_files)}")
    print()

    # 检查测试文件是否存在
    for f in test_files:
        if not (tester_path / f).exists():
            print(f"\033[1;33m警告:\033[0m 测试文件不存在: {f}")
            print(f"      请先创建测试文件")
            return 1

    # 运行 pytest
    args = ["-v", "--tb=short"] + test_files
    return pytest.main(args)


def main() -> int:
    """主函数"""
    parser = argparse.ArgumentParser(
        description="Vikingbot 测试框架 - 测试 vikingbot 的基础功能",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  test_vikingbot list                    列出所有测试
  test_vikingbot spec agent_single_turn  显示单轮对话测试的详细规范
  test_vikingbot run                     运行所有测试
  test_vikingbot run agent_single_turn   运行单轮对话测试
        """,
    )

    subparsers = parser.add_subparsers(title="命令", dest="command")

    # list 命令
    list_parser = subparsers.add_parser("list", help="列出所有可用的测试")

    # spec 命令
    spec_parser = subparsers.add_parser("spec", help="显示测试的详细规范")
    spec_parser.add_argument("test", help="测试名称 (如: agent_single_turn)")

    # run 命令
    run_parser = subparsers.add_parser("run", help="运行测试")
    run_parser.add_argument(
        "tests", nargs="*", help="要运行的测试（留空运行所有测试）"
    )

    args = parser.parse_args()

    if args.command == "list":
        list_tests()
        return 0
    elif args.command == "spec":
        show_spec(args.test)
        return 0
    elif args.command == "run":
        return run_tests(args.tests)
    else:
        # 默认显示帮助
        parser.print_help()
        print("\n\033[1;33m提示:\033[0m 使用 'test_vikingbot list' 查看所有可用测试")
        return 1


if __name__ == "__main__":
    sys.exit(main())
