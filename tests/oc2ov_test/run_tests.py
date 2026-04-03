#!/usr/bin/env python3
"""
测试运行入口
"""

import argparse
import sys
import unittest

from utils.logger import setup_logger


def get_test_suite(test_type: str = None):
    """
    获取测试套件
    """
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    if test_type == "p0":
        suite.addTests(loader.loadTestsFromName("tests.p0.test_memory_write"))
    elif test_type == "crud":
        suite.addTests(loader.loadTestsFromName("tests.crud.test_memory_crud"))
    elif test_type == "complex":
        suite.addTests(loader.loadTestsFromName("tests.complex.test_complex_scenarios"))
    else:
        suite.addTests(loader.loadTestsFromName("tests.p0.test_memory_write"))
        suite.addTests(loader.loadTestsFromName("tests.crud.test_memory_crud"))
        suite.addTests(loader.loadTestsFromName("tests.complex.test_complex_scenarios"))

    return suite


def main():
    parser = argparse.ArgumentParser(description="OpenClaw - OpenViking 端到端自动化测试")
    parser.add_argument(
        "--type",
        "-t",
        choices=["all", "p0", "crud", "complex"],
        default="all",
        help="测试类型: all(全部), p0(P0级), crud(CRUD操作), complex(复杂场景)",
    )
    parser.add_argument(
        "--test",
        "-s",
        help="运行指定的测试用例, 例如: tests.p0.test_memory_write.TestMemoryWriteGroupA",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")

    args = parser.parse_args()

    setup_logger()

    if args.test:
        suite = unittest.TestSuite()
        suite.addTests(unittest.TestLoader().loadTestsFromName(args.test))
    else:
        suite = get_test_suite(args.type)

    verbosity = 2 if args.verbose else 1
    runner = unittest.TextTestRunner(verbosity=verbosity)
    result = runner.run(suite)

    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
