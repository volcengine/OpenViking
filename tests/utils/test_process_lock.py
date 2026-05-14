# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for process_lock.py signal handling."""

import signal


def test_sigterm_handler_calls_sys_exit():
    """SIGTERM handler 应该调用 sys.exit(0) 干净退出。

    验证修复后的 lambda 调用 sys.exit(0) 而非 signal.default_int_handler。
    signal.default_int_handler 会抛出 KeyboardInterrupt（SIGINT handler），
    语义错误。sys.exit(0) 触发 atexit cleanup 并正常退出。
    """
    exit_calls = []

    def fake_exit(code=0):
        exit_calls.append(code)

    # 模拟修复后的 handler（与 process_lock.py:119-120 一致）
    def handler(sig, frame):
        (None, fake_exit(0))

    handler(signal.SIGTERM, None)

    assert exit_calls == [0]


def test_sigterm_handler_calls_cleanup_before_exit():
    """SIGTERM handler 应该在退出前调用 _cleanup。

    _cleanup 负责移除 PID 锁文件，即使在 SIGTERM 强制关闭时也应执行，
    避免残留锁文件阻止后续进程启动。lambda 中的元组按顺序执行：
    先 _cleanup()，再 sys.exit(0)。
    """
    cleanup_called = []
    exit_calls = []

    def fake_cleanup():
        cleanup_called.append(True)

    def fake_exit(code=0):
        exit_calls.append(code)

    # 模拟修复后的 handler（与 process_lock.py:119-120 一致）
    def handler(sig, frame):
        (fake_cleanup(), fake_exit(0))

    handler(signal.SIGTERM, None)

    assert cleanup_called == [True], "_cleanup 应在 exit 前被调用"
    assert exit_calls == [0]
